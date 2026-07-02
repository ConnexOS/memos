"""清理 ChromaDB 中无 project_id 的孤儿记录。

分两步：
  1. 按 project_id='' 过滤查询所有 type（user_input, assistant_output, briefing）
  2. 遍历全部记录（分批）找出无 project_id 的记录（兜底，以防过滤不全）

用法：
    python tools/clean_orphan_project.py          # 预览
    python tools/clean_orphan_project.py --delete  # 删除
"""

import argparse
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memos.storage.chroma import create_store
from memos.config import get_config


def get_all_orphans(store) -> list[dict]:
    """从 ChromaDB 中找出所有无 project_id 的记录。"""
    seen_ids = set()
    orphans = []

    # 方式一：按 type + 空 project_id 精确查询
    for typ in ("user_input", "assistant_output", "briefing", "task", "solution", "decision", "lesson", "process"):
        try:
            r = store.get(where={"type": typ, "project_id": ""}, include=["metadatas", "documents"], limit=100)
            for i, mid in enumerate(r.get("ids", [])):
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
                meta = r["metadatas"][i] if i < len(r["metadatas"]) else {}
                doc = r["documents"][i] if i < len(r["documents"]) else ""
                orphans.append({"id": mid, "type": typ, "meta": meta, "doc": (doc or "")[:120]})
        except Exception as e:
            print(f"  [{typ}] 查询出错: {e}")

    # 方式二：分批扫描全量记录，兜底找无 project_id 的记录
    BATCH = 1000
    offset = 0
    while True:
        try:
            r = store.get(limit=BATCH, offset=offset, include=["metadatas", "documents"])
            ids = r.get("ids", [])
            if not ids:
                break
            for i, mid in enumerate(ids):
                if mid in seen_ids:
                    continue
                meta = r["metadatas"][i] if i < len(r["metadatas"]) else {}
                if not meta:
                    meta = {}
                pid = meta.get("project_id", "")
                if not pid:
                    seen_ids.add(mid)
                    doc = r["documents"][i] if i < len(r["documents"]) else ""
                    orphans.append({"id": mid, "type": meta.get("type", "unknown"), "meta": meta, "doc": (doc or "")[:120]})
            offset += len(ids)
            if len(ids) < BATCH:
                break
        except Exception as e:
            print(f"  分批扫描出错 (offset={offset}): {e}")
            break

    return orphans


def main():
    parser = argparse.ArgumentParser(description="清理 orphan 项目记录")
    parser.add_argument("--delete", action="store_true", help="传入则执行删除，否则仅预览")
    args = parser.parse_args()

    store = create_store()
    orphans = get_all_orphans(store)

    print(f"\n=== 孤儿记录（无 project_id）: {len(orphans)} ===")
    if not orphans:
        print("无孤儿记录，无需清理。")
        return

    type_counts = {}
    for o in orphans:
        type_counts[o["type"]] = type_counts.get(o["type"], 0) + 1
    print("\n按类型统计:")
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")

    print("\n记录明细:")
    for o in orphans:
        ts = o["meta"].get("timestamp", o["meta"].get("generated_at", ""))
        if isinstance(ts, (int, float)):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        print(f"  [{o['type']:>15}] {o['id']}  [{ts}]  {o['doc']}")

    if args.delete:
        orphan_ids = [o["id"] for o in orphans]
        print(f"\n正在删除 {len(orphan_ids)} 条记录...")
        store.delete(orphan_ids)
        print("删除完成。")
    else:
        print(f'\n预览模式结束。确认删除请执行: python tools/clean_orphan_project.py --delete')


if __name__ == "__main__":
    main()
