#!/usr/bin/env python
"""
清理 ChromaDB 中除 MEMOS (d0ff92fa) 和 SemSSE (7f3114f5) 外的所有测试数据。

两步操作:
  1. 清理 project_memory 集合中非保留项目的数据
  2. 删除测试相关的独立集合 (test_*, intg_*, bench_*)

用法:
    python scripts/cleanup_chromadb_projects.py            # 预览模式
    python scripts/cleanup_chromadb_projects.py --exec     # 实际执行清理
    python scripts/cleanup_chromadb_projects.py --exec --vacuum  # 清理+VACUUM
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

# ── 配置 ──────────────────────────────────────────────────────────────
CHROMA_PATH = Path("D:/DevSpace/MEMOS/memdb")
COLLECTION_NAME = "project_memory"
KEEP_PROJECTS = {
    "d0ff92fa": "MEMOS",
    "7f3114f5": "SemSSE",
}
BATCH_SIZE = 500
# ──────────────────────────────────────────────────────────────────────

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def get_all_project_stats(col) -> dict:
    """遍历集合，统计各 project_id 的文档数和 project_name。"""
    stats: dict[str, dict] = {}
    offset = 0
    while True:
        batch = col.get(limit=BATCH_SIZE, offset=offset, include=["metadatas"])
        ids = batch.get("ids", [])
        if not ids:
            break
        metas = batch.get("metadatas", []) or []
        for i, meta in enumerate(metas):
            m = meta or {}
            pid = m.get("project_id", "未知")
            pname = m.get("project_name", "") or ""
            if pid not in stats:
                stats[pid] = {"count": 0, "name": pname, "delete_ids": []}
            stats[pid]["count"] += 1
            if pname and not stats[pid]["name"]:
                stats[pid]["name"] = pname
            stats[pid]["delete_ids"].append(ids[i])
        offset += len(ids)
    return stats


def clean_collection(col, exec_mode: bool) -> tuple[list[tuple[str, str, int]], int]:
    """清理集合中非保留项目的数据。返回 (删除项目列表, 删除文档数)。"""
    stats = get_all_project_stats(col)
    to_delete_ids = []
    delete_projects = []
    delete_count = 0

    print(f"{'project_id':<38} {'project_name':<20} {'文档数':>8} {'操作':>10}")
    print("-" * 80)
    for pid, info in sorted(stats.items(), key=lambda x: x[1]["count"], reverse=True):
        if pid in KEEP_PROJECTS:
            action = "保留"
        elif pid == "未知" and info["count"] <= 5:
            action = "删除" if exec_mode else "[待删除]"
            to_delete_ids.extend(info["delete_ids"])
            delete_count += info["count"]
            delete_projects.append((pid, info["name"], info["count"]))
        else:
            action = "删除" if exec_mode else "[待删除]"
            to_delete_ids.extend(info["delete_ids"])
            delete_count += info["count"]
            delete_projects.append((pid, info["name"], info["count"]))
        print(f"{pid:<38} {(info['name'] or ''):<20} {info['count']:>8} {action:>10}")

    print()
    if not delete_projects:
        print(">> 没有需要清理的项目数据。")
        return [], 0

    print(f"待删除项目数: {len(delete_projects)}, 待删除文档数: {delete_count}")
    if not exec_mode:
        print(">> 预览模式，未执行删除。添加 --exec 参数执行清理。")
        return [], 0

    # 分批删除
    print("执行删除...")
    deleted = 0
    for i in range(0, len(to_delete_ids), BATCH_SIZE):
        batch_ids = to_delete_ids[i : i + BATCH_SIZE]
        try:
            col.delete(ids=batch_ids)
            deleted += len(batch_ids)
            print(f"  进度: {deleted}/{delete_count}")
        except Exception as e:
            print(f"  批次 {i}-{i+BATCH_SIZE} 删除失败: {e}", file=sys.stderr)

    print(f"  删除完成！集合剩余文档数: {col.count()}")
    return delete_projects, delete_count


def delete_test_collections(client, exec_mode: bool) -> list[str]:
    """删除测试相关的独立集合。返回已删除的集合名列表。"""
    collections = client.list_collections()
    test_prefixes = ("test_", "intg_", "bench_")
    to_drop = [c for c in collections if c.name.startswith(test_prefixes)]

    if not to_drop:
        print(">> 没有测试集合需要清理。")
        return []

    print(f"\n待删除的测试集合 ({len(to_drop)} 个):")
    for c in sorted(to_drop, key=lambda x: x.name):
        print(f"  - {c.name}")

    if not exec_mode:
        print(">> 预览模式，未执行删除。添加 --exec 参数执行清理。")
        return []

    print("开始删除测试集合...")
    for c in to_drop:
        try:
            client.delete_collection(c.name)
            print(f"  [OK] {c.name}")
        except Exception as e:
            print(f"  [ERR] {c.name}: {e}", file=sys.stderr)

    return [c.name for c in to_drop]


def run_vacuum():
    """对 chroma.sqlite3 执行 VACUUM 回收空间。"""
    db_path = CHROMA_PATH / "chroma.sqlite3"
    if not db_path.exists():
        print("chroma.sqlite3 不存在，跳过 VACUUM。")
        return

    before = db_path.stat().st_size
    print(f"VACUUM 前大小: {before/1024/1024:.1f} MB")
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("VACUUM")
        conn.close()
        after = db_path.stat().st_size
        saved = (before - after) / 1024 / 1024
        print(f"VACUUM 完成: {before/1024/1024:.1f}MB -> {after/1024/1024:.1f}MB (回收 {saved:.1f}MB)")
    except Exception as e:
        print(f"VACUUM 失败: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="清理 ChromaDB 测试项目数据")
    parser.add_argument("--exec", action="store_true", help="实际执行删除（默认仅预览）")
    parser.add_argument("--vacuum", action="store_true", help="删除后执行 VACUUM 回收空间")
    parser.add_argument("--skip-collections", action="store_true", help="跳过测试集合的清理")
    args = parser.parse_args()

    # 连接
    client = chromadb.PersistentClient(
        path=str(CHROMA_PATH),
        settings=ChromaSettings(anonymized_telemetry=False),
    )

    # 列出所有集合
    all_collections = client.list_collections()
    print(f"ChromaDB 路径: {CHROMA_PATH}")
    print(f"集合总数: {len(all_collections)}")
    for c in all_collections:
        marker = ""
        if c.name == COLLECTION_NAME:
            marker = "  <-- 主集合"
        elif c.name.startswith("test_") or c.name.startswith("intg_") or c.name.startswith("bench_"):
            marker = "  [测试]"
        if c.name == COLLECTION_NAME:
            try:
                cnt = c.count()
                print(f"  {c.name:<45} {cnt} 条{marker}")
            except Exception:
                print(f"  {c.name:<45} ?{marker}")
        else:
            print(f"  {c.name:<45}{marker}")

    # Step 1: 清理 project_memory 集合中的非保留项目
    print(f"\n{'='*60}")
    print(f"Step 1: 清理集合 '{COLLECTION_NAME}' 中的非保留项目")
    print(f"{'='*60}")
    col = client.get_or_create_collection(COLLECTION_NAME)
    total_before = col.count()
    print(f"集合 '{COLLECTION_NAME}' 总文档数: {total_before}\n")
    deleted_projects, deleted_count = clean_collection(col, args.exec)

    # Step 2: 删除测试集合
    if not args.skip_collections:
        print(f"\n{'='*60}")
        print("Step 2: 清理测试独立集合")
        print(f"{'='*60}")
        deleted_test_cols = delete_test_collections(client, args.exec)
    else:
        deleted_test_cols = []

    # Vacuum
    if args.exec and args.vacuum:
        print(f"\n{'='*60}")
        print("Step 3: VACUUM 回收空间")
        print(f"{'='*60}")
        run_vacuum()

    # 摘要
    print(f"\n{'='*60}")
    print("清理摘要")
    print(f"{'='*60}")
    if deleted_projects:
        for pid, pname, cnt in deleted_projects:
            print(f"  [删除] 项目 {pname or pid:<20} ({pid}): {cnt} 条")
    if args.exec and deleted_test_cols:
        print(f"  [删除] {len(deleted_test_cols)} 个测试集合")
    for pid, label in KEEP_PROJECTS.items():
        print(f"  [保留] {label:<20} ({pid})")
    if args.exec:
        print(f"\n集合 '{COLLECTION_NAME}' 最终文档数: {col.count()}")
    else:
        print("\n预览模式结束，添加 --exec 执行清理。")


if __name__ == "__main__":
    main()
