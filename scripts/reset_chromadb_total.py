"""
方案 C：全量重置 ChromaDB — 导出 -> 删除 memdb -> 导入 -> VACUUM

清理内容：
  - 所有孤立 HNSW 索引段目录（UUID 目录）
  - SQLite 中 87 个测试集合记录
  - SQLite 深层压缩回收空间

用法：
  1. 确保无其他进程占用 ChromaDB（如 Dashboard、MCP Server）
  2. cd D:\DevSpace\MEMOS
  3. .\venv\Scripts\python scripts\reset_chromadb_total.py
"""
import json
import logging
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memos.config import config
from memos.storage.chroma import create_store, ChromaDBPersistentStore

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

COLLECTION = "project_memory"
MEMDB = Path(config.chroma.path)
DB_PATH = MEMDB / "chroma.sqlite3"


def export_all(store):
    """分批导出全部数据"""
    all_ids, all_docs, all_metas, all_embs = [], [], [], []
    offset, batch_size = 0, 500
    while True:
        batch = store.get(limit=batch_size, offset=offset, include=["documents", "metadatas", "embeddings"])
        ids = batch.get("ids", [])
        if not ids:
            break
        all_ids.extend(ids)
        all_docs.extend(batch.get("documents", []))
        all_metas.extend(batch.get("metadatas", []))
        all_embs.extend(batch.get("embeddings", []))
        offset += len(ids)
    return all_ids, all_docs, all_metas, all_embs


def import_all(store, ids, docs, metas, embs):
    """分批导入数据"""
    imported = 0
    batch_size = 500
    for i in range(0, len(ids), batch_size):
        end = min(i + batch_size, len(ids))
        store.add(
            documents=docs[i:end],
            embeddings=embs[i:end],
            metadatas=metas[i:end],
            ids=ids[i:end],
        )
        imported += end - i
    return imported


def main():
    if not store_healthy():
        logger.error("ChromaDB 状态异常，中止")
        return

    # 1. 导出
    logger.info("=== 1/5 导出数据 ===")
    store = create_store(COLLECTION)
    total = store.count()
    logger.info("集合 '%s' 共 %s 条记录", COLLECTION, total)
    ids, docs, metas, embs = export_all(store)
    logger.info("导出完成: %s 条", len(ids))

    # 2. 关闭连接
    logger.info("\n=== 2/5 关闭 ChromaDB 连接 ===")

    # 3. 删除 memdb 目录
    logger.info("\n=== 3/5 删除 memdb 目录 ===")
    shutil.rmtree(MEMDB, ignore_errors=True)
    logger.info("已删除 %s", MEMDB)

    # 4. 重新导入
    logger.info("\n=== 4/5 重新导入 ===")
    MEMDB.mkdir(parents=True, exist_ok=True)
    store = create_store(COLLECTION)
    imported = import_all(store, ids, docs, metas, embs)
    logger.info("导入完成: %s 条", imported)

    # 5. VACUUM
    logger.info("\n=== 5/5 VACUUM 压缩 ===")
    store.vacuum()

    # 统计
    sqlite_size = DB_PATH.stat().st_size
    dir_count = len([d for d in MEMDB.iterdir() if d.is_dir()])
    logger.info("\n完成！清理后状态：")
    logger.info("  chroma.sqlite3: %.1f MB", sqlite_size / 1024 / 1024)
    logger.info("  索引目录: %s 个", dir_count)
    logger.info("  记录数: %s", store.count())

    healthy = store_healthy()
    logger.info("  健康检查: %s", "通过" if healthy else "失败！")


def store_healthy():
    try:
        store = create_store(COLLECTION)
        store.count()
        return True
    except Exception as e:
        logger.error("健康检查失败: %s", e)
        return False


if __name__ == "__main__":
    main()
