"""
迁移脚本：为所有缺失 todo_status 字段的待办记录补充 todo_status="pending"。

背景: v0.4.5 之前创建的待办记录可能缺少 todo_status 字段（当时尚未引入此字段），
导致 server/mcp.py 和 web/routes/todos.py 中查询 pending 待办时无法直接在 ChromaDB
层过滤，需要全量拉取后在 Python 侧回退。本脚本为所有存量旧数据补齐 todo_status="pending"，
迁移后即可消除 pending 特判逻辑，让 ChromaDB 直接按 todo_status 过滤。

用法:
    python scripts/migrate_todo_status.py [--dry-run] [--batch-size 100]
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    print("MEMOS 待办状态字段迁移工具")
    print("目标: 为缺少 todo_status 字段的旧待办记录补充 pending")
    print("=" * 60)

    # 加载项目配置
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from memos.config import config
    from memos.engine.memory import create_store

    store = create_store()

    # Step 1: 获取所有 type=todo 记录的 ID 和 metadatas
    print("\n[1/3] 扫描待办记录...")
    where = {"type": "todo"}
    total = store.count(where=where)
    print(f"  共 {total} 条 type=todo 记录")

    if total == 0:
        print("\n  无待办记录，退出。")
        return

    # 分批拉取 metadatas，找出缺 todo_status 的记录
    all_missing = []
    offset = 0
    while offset < total:
        chunk = store.get(where=where, limit=batch_size, offset=offset, include=["metadatas"])
        if not chunk["ids"]:
            break
        for i, mem_id in enumerate(chunk["ids"]):
            meta = chunk["metadatas"][i] or {}
            if not meta.get("todo_status"):
                all_missing.append((mem_id, meta))
        offset += len(chunk["ids"])
        print(f"  扫描进度: {offset}/{total} (缺字段: {len(all_missing)})")

    print(f"\n  扫描完成: 共 {total} 条待办，{len(all_missing)} 条缺少 todo_status 字段")

    if not all_missing:
        print("\n  所有记录均已包含 todo_status 字段，无需迁移。")
        return

    if dry_run:
        print(f"\n[DRY RUN] 将更新 {len(all_missing)} 条记录:")
        for mem_id, meta in all_missing[:10]:
            doc_preview = meta.get("document", "")
            if not doc_preview:
                # 尝试从 store 获取 document
                doc_chunk = store.get(ids=[mem_id], include=["documents"])
                doc_preview = (doc_chunk.get("documents") or [""])[0] or ""
            print(f"  - {mem_id[:12]}... | {doc_preview[:50]}")
        if len(all_missing) > 10:
            print(f"  ...及其他 {len(all_missing) - 10} 条")
        print("\n[DRY RUN] 使用 --dry-run 跳过实际操作。")
        print("确认无误后移除 --dry-run 运行以执行迁移。")
        return

    # Step 2: 批量补充 todo_status
    print(f"\n[2/3] 批量补充 todo_status='pending' ({len(all_missing)} 条)...")
    success = 0
    for i in range(0, len(all_missing), batch_size):
        batch = all_missing[i : i + batch_size]
        ids = [mem_id for mem_id, _ in batch]
        metas = []
        for _, meta in batch:
            new_meta = dict(meta)
            new_meta["todo_status"] = "pending"
            metas.append(new_meta)
        try:
            store.update(ids=ids, metadatas=metas)
            success += len(batch)
        except Exception as e:
            print(f"  [ERROR] 批次 {i // batch_size + 1} 更新失败: {e}")
            # 逐条重试
            for mem_id, meta in batch:
                try:
                    new_meta = dict(meta)
                    new_meta["todo_status"] = "pending"
                    store.update(ids=[mem_id], metadatas=[new_meta])
                    success += 1
                except Exception as e2:
                    print(f"  [ERROR] {mem_id[:12]}... 更新失败: {e2}")
        print(f"  迁移进度: {min(i + batch_size, len(all_missing))}/{len(all_missing)}")

    # Step 3: 验证
    print(f"\n[3/3] 验证迁移结果...")
    still_missing = 0
    offset = 0
    while offset < total:
        chunk = store.get(where=where, limit=batch_size, offset=offset, include=["metadatas"])
        if not chunk["ids"]:
            break
        for meta in chunk["metadatas"]:
            meta = meta or {}
            if not meta.get("todo_status"):
                still_missing += 1
        offset += len(chunk["ids"])
    print(f"  验证完成: 迁移后仍有 {still_missing} 条缺少 todo_status")

    print(f"\n{'=' * 60}")
    print(f"迁移完成! 成功: {success}, 失败: {len(all_missing) - success}")
    if still_missing == 0:
        print("所有待办记录均已包含 todo_status 字段。")
        print("现在可以安全修改代码，移除 pending 特判逻辑。")
    else:
        print(f"仍有 {still_missing} 条记录缺失，建议重新运行或检查日志。")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"\n耗时: {time.time() - t0:.1f}s")
