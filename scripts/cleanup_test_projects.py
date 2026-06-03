"""
清理 ChromaDB 中除指定项目外的所有数据。

保留项目：
  - MEMOS  (project_id: d0ff92fa)
  - SemSSE (project_id: 7f3114f5)

用法：python scripts/cleanup_test_projects.py
"""
import logging
import sys
from pathlib import Path

# 确保能找到 src
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memos.storage.chroma import create_store, ChromaDBPersistentStore

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

KEEP_PROJECTS = {"d0ff92fa", "7f3114f5"}
COLLECTION = "project_memory"
BATCH_SIZE = 500


def main():
    store = create_store(COLLECTION)
    if not isinstance(store, ChromaDBPersistentStore):
        logger.error("仅支持 persistent 模式")
        return

    # 1. 获取全量数据（分批，只取 metadatas 和 ids）
    all_ids = []
    all_metas = []
    offset = 0
    while True:
        batch = store.get(
            limit=BATCH_SIZE,
            offset=offset,
            include=["metadatas"],
        )
        ids = batch.get("ids", [])
        if not ids:
            break
        all_ids.extend(ids)
        all_metas.extend(batch.get("metadatas", []))
        offset += len(ids)

    total = len(all_ids)
    logger.info("集合 '%s' 中共 %s 条记录", COLLECTION, total)

    if total == 0:
        logger.info("无需清理")
        return

    # 2. 按 project_id 分组统计
    project_counts = {}
    for meta in all_metas:
        pid = meta.get("project_id", "unknown") if meta else "unknown"
        project_counts[pid] = project_counts.get(pid, 0) + 1

    logger.info("\n现有项目分布：")
    for pid, count in sorted(project_counts.items(), key=lambda x: -x[1]):
        keep_tag = " ✓ 保留" if pid in KEEP_PROJECTS else ""
        logger.info("  %s: %s 条%s", pid, count, keep_tag)

    # 3. 确认后删除
    to_delete_ids = [
        all_ids[i]
        for i in range(total)
        if (all_metas[i] or {}).get("project_id", "unknown") not in KEEP_PROJECTS
    ]

    if not to_delete_ids:
        logger.info("\n没有需要删除的记录")
        return

    logger.info("\n准备删除 %s 条记录（保留 %s 条）", len(to_delete_ids), total - len(to_delete_ids))

    # 分批删除
    for i in range(0, len(to_delete_ids), BATCH_SIZE):
        batch = to_delete_ids[i : i + BATCH_SIZE]
        store.delete(batch)
        logger.info("  已删除批次 %d-%d (%d 条)", i + 1, min(i + BATCH_SIZE, len(to_delete_ids)), len(batch))

    logger.info("删除完成")

    # 4. VACUUM 回收磁盘空间
    logger.info("\n执行 VACUUM 回收磁盘空间...")
    store.vacuum()

    # 5. 验证
    remaining = store.count()
    logger.info("\n清理后集合 '%s' 中剩余 %s 条记录", COLLECTION, remaining)

    logger.info("\n完成！")


if __name__ == "__main__":
    main()
