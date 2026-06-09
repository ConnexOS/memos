"""迁移旧记录：补充 scope 和 creator_id。

v0.5.1 用户级数据隔离要求每条记录有 scope/creator_id。
旧记录缺少这些字段，查询时虽已通过 {"scope": {"$ne": "personal"}} 兼容，
但仍建议迁移以获得干净数据。

处理规则：
  - creator_id="unknown" 且 scope 缺失/为空 → scope="team", creator_id 不动
  - scope 缺失 → scope="team"
  - 已有合法 scope/creator_id → 跳过
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memos.engine.memory import ContextMemory
from memos.config import config


def migrate():
    mem = ContextMemory()
    mem.warmup()

    # 全量拉取（不含 active=false，不排除任何类型）
    result = mem.store.get(include=["metadatas"])
    total = len(result["ids"])
    if total == 0:
        print("数据库为空，无需迁移。")
        return

    updated = 0
    skipped = 0
    for i in range(total):
        mid = result["ids"][i]
        meta = result["metadatas"][i] or {}
        scope = meta.get("scope", "")
        creator = meta.get("creator_id", "")

        needs_update = False
        new_meta = {}

        if not scope:
            new_meta["scope"] = "team"
            needs_update = True

        if creator == "unknown" and not scope:
            # unknown + 无 scope → 补充 scope=team
            pass  # 已在上方处理

        if creator == "unknown" and scope == "personal":
            # 理论上不应发生：unknown 的记录不可能是 personal
            # 保险起见改为 team
            new_meta["scope"] = "team"
            needs_update = True

        if needs_update:
            try:
                mem.store.update(ids=[mid], metadatas=[new_meta])
                updated += 1
            except Exception as e:
                print(f"  更新失败 {mid[:8]}: {e}")
                skipped += 1

        if (i + 1) % 500 == 0:
            print(f"  进度: {i+1}/{total}")

    print(f"\n迁移完成: 共 {total} 条, 更新 {updated} 条, 跳过 {skipped} 条")


if __name__ == "__main__":
    migrate()
