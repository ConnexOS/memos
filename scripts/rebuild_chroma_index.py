"""
重建 ChromaDB 集合索引：导出全量数据 → 删除集合 → 重新导入。

作用：清理 ChromaDB 积累的 HNSW 索引段文件（UUID 目录），回收磁盘空间。
用法：python scripts/rebuild_chroma_index.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memos.storage.chroma import create_store, ChromaDBPersistentStore

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

COLLECTION = "project_memory"


def main():
    store = create_store(COLLECTION)
    if not isinstance(store, ChromaDBPersistentStore):
        logger.error("仅支持 persistent 模式")
        return

    before = store.count()
    logger.info("集合 '%s' 当前 %s 条记录", COLLECTION, before)

    if before == 0:
        logger.info("空集合，无需重建")
        return

    logger.info("开始重建索引...")
    result = store.reindex()
    logger.info("重建结果: %s", result)

    if result.get("status") in ("ok", "partial"):
        # 再次 VACUUM
        store.vacuum()

        # 清理后统计
        import os
        from pathlib import Path as P

        memdb = P("D:/DevSpace/MEMOS/memdb")
        dirs = [d for d in memdb.iterdir() if d.is_dir()]
        sqlite_size = memdb.joinpath("chroma.sqlite3").stat().st_size

        logger.info("\n清理后状态：")
        logger.info("  chroma.sqlite3: %.1f MB", sqlite_size / 1024 / 1024)
        logger.info("  索引目录数: %d", len(dirs))
        logger.info("  总空间: %.1f MB", sum(d.stat().st_size for d in memdb.rglob("*") if d.is_file()) / 1024 / 1024)


if __name__ == "__main__":
    main()
