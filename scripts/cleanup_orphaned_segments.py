"""
删除 ChromaDB 孤立索引段目录（UUID 目录），释放磁盘空间。

安全策略：只删除 SQLite segments 表中未引用的目录。
用法：python scripts/cleanup_orphaned_segments.py
"""
import logging
import os
import sqlite3
import shutil
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MEMDB = Path("D:/DevSpace/MEMOS/memdb")
DB_PATH = MEMDB / "chroma.sqlite3"


def main():
    if not DB_PATH.exists():
        logger.error("chroma.sqlite3 不存在: %s", DB_PATH)
        return

    # 1. 读取活跃 segment UUID
    conn = sqlite3.connect(str(DB_PATH))
    active = set(row[0] for row in conn.execute("SELECT id FROM segments").fetchall())
    conn.close()
    logger.info("SQLite segments 表中活跃 UUID: %s 个", len(active))

    # 2. 扫描磁盘 UUID 目录
    orphans = []
    for d in sorted(MEMDB.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        if len(name) != 36 or name.count("-") != 4:
            continue  # 不是 UUID 格式的目录
        if name in active:
            continue
        orphans.append(d)

    if not orphans:
        logger.info("没有需要清理的孤立目录")
        return

    logger.info("孤立目录: %s 个", len(orphans))

    # 3. 删除孤立目录
    deleted_size = 0
    failed = 0
    for d in orphans:
        try:
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d)
            deleted_size += size
        except PermissionError as e:
            logger.warning("  跳过(被占用): %s — %s", d.name, e)
            failed += 1

    logger.info("已删除 %s 个孤立目录，回收 %.1f MB", len(orphans) - failed, deleted_size / 1024 / 1024)
    if failed:
        logger.info("因文件占用跳过 %s 个（重启后重试即可）", failed)

    # 4. 统计
    dirs_left = [d for d in MEMDB.iterdir() if d.is_dir()]
    total_size = sum(sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) for d in dirs_left)
    sqlite_size = DB_PATH.stat().st_size
    logger.info("\n清理后状态：")
    logger.info("  chroma.sqlite3: %.1f MB", sqlite_size / 1024 / 1024)
    logger.info("  索引目录: %s 个", len(dirs_left))
    logger.info("  合计: %.1f MB", (total_size + sqlite_size) / 1024 / 1024)
    logger.info("  约回收: %.1f GB", (len(orphans) - failed) * 1.9 / len(orphans) if orphans else 0)


if __name__ == "__main__":
    main()
