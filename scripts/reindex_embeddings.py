"""
嵌入模型升级后重建 ChromaDB 索引。

用法:
    python scripts/reindex_embeddings.py [--dry-run] [--batch-size 100]

步骤:
    1. 备份现有 memdb/ 目录
    2. 用新模型重新编码所有记忆
    3. 将新嵌入写入新 collection
    4. (手动) 确认无误后更新 config 并切换
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

from sentence_transformers import SentenceTransformer


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEMDB_PATH = PROJECT_ROOT / "memdb"
BACKUP_PATH = PROJECT_ROOT / f"memdb_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def parse_args():
    dry_run = "--dry-run" in sys.argv
    batch_size = 100
    for i, arg in enumerate(sys.argv):
        if arg == "--batch-size" and i + 1 < len(sys.argv):
            try:
                batch_size = int(sys.argv[i + 1])
            except ValueError:
                pass
    return dry_run, batch_size


def main():
    dry_run, batch_size = parse_args()

    print("=" * 60)
    print("MEMOS 嵌入索引重建工具")
    print("=" * 60)

    # 1. 加载配置
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from memos.config import config

    old_model_path = PROJECT_ROOT / "model" / "all-MiniLM-L6-v2"
    new_model_path = Path(config.model.path)
    old_dim = 384
    new_dim = config.model.vector_dim

    print(f"\n旧模型: {old_model_path.name} ({old_dim}维)")
    print(f"新模型: {new_model_path.name} ({new_dim}维)")

    if not new_model_path.exists():
        print(f"\n[ERROR] 新模型路径不存在: {new_model_path}")
        print("请先下载模型到该目录后再运行本脚本。")
        sys.exit(1)

    # 2. 加载新模型
    print(f"\n[1/4] 加载新模型: {new_model_path}")
    encoder = SentenceTransformer(str(new_model_path))

    # 验证维度
    test_vec = encoder.encode("test")
    actual_dim = len(test_vec)
    if actual_dim != new_dim:
        print(f"[WARN] 配置 vector_dim={new_dim}，但模型输出维度={actual_dim}，使用实际维度")
        new_dim = actual_dim
    print(f"  [OK] 模型输出维度: {new_dim}")

    # 3. 连接 ChromaDB，读取所有记录
    print("\n[2/4] 读取现有记忆数据...")
    import chromadb

    client = chromadb.PersistentClient(path=str(MEMDB_PATH))
    collection_name = config.chroma.collection_name

    # 列出所有 collection
    all_collections = client.list_collections()
    print(f"  现有 {len(all_collections)} 个 collection: {[c.name for c in all_collections]}")

    try:
        old_collection = client.get_collection(collection_name)
    except Exception:
        print(f"  [ERROR] collection '{collection_name}' 不存在")
        sys.exit(1)

    total = old_collection.count()
    print(f"  collection '{collection_name}': {total} 条记录")

    if total == 0:
        print("  无数据需要迁移，退出。")
        sys.exit(0)

    # 分批读取
    all_ids = []
    all_documents = []
    all_metadatas = []
    for offset in range(0, total, batch_size):
        chunk = old_collection.get(limit=batch_size, offset=offset, include=["documents", "metadatas"])
        all_ids.extend(chunk["ids"])
        all_documents.extend(chunk["documents"])
        all_metadatas.extend(chunk["metadatas"])

    print(f"  读取完成: {len(all_documents)} 条文档")

    # 4. 重新编码
    print(f"\n[3/4] 用新模型重新编码 ({len(all_documents)} 条)...")
    new_embeddings = encoder.encode(all_documents, show_progress_bar=True).tolist()

    # 5. 备份并写入新 collection
    print(f"\n[4/4] 备份并迁移数据...")

    if not dry_run:
        # 备份
        print(f"  备份 memdb/ → {BACKUP_PATH.name}")
        shutil.copytree(MEMDB_PATH, BACKUP_PATH)

        # 创建新 collection（添加 _v2 后缀避免冲突）
        new_collection_name = f"{collection_name}_v2"
        print(f"  创建新 collection: '{new_collection_name}'")

        # 删除已存在的同名 collection
        try:
            client.delete_collection(new_collection_name)
            print(f"  [INFO] 删除已有 collection '{new_collection_name}'")
        except Exception:
            pass

        new_collection = client.create_collection(
            name=new_collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        # 分批写入
        for i in range(0, len(all_ids), batch_size):
            end = min(i + batch_size, len(all_ids))
            new_collection.add(
                ids=all_ids[i:end],
                documents=all_documents[i:end],
                embeddings=new_embeddings[i:end],
                metadatas=all_metadatas[i:end],
            )
            print(f"  写入 {i + 1}-{end}/{len(all_ids)}")

        print(f"\n{'=' * 60}")
        print("迁移完成!")
        print(f"  备份位置: {BACKUP_PATH}")
        print(f"  新 collection: '{new_collection_name}'")
        print(f"  记录数: {len(all_ids)}")
        print(f"  嵌入维度: {new_dim}")
        print(f"\n下一步:")
        print(f"  1. 验证新 collection: 修改 config.json 中 collection_name 为 '{new_collection_name}' 后测试检索")
        print(f"  2. 确认无误后可删除旧 collection")
        print(f"  3. 或直接删除新 collection 并恢复备份")
        print(f"{'=' * 60}")
    else:
        print(f"\n  [DRY RUN] 将迁移 {len(all_ids)} 条记录，维度 {old_dim} → {new_dim}")
        print(f"  [DRY RUN] 备份将保存到: {BACKUP_PATH}")
        print(f"  [DRY RUN] 使用 --dry-run 跳过实际操作")


if __name__ == "__main__":
    main()
