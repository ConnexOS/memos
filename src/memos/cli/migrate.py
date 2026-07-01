"""
memos migrate types — 旧 7 类数据迁移工具 (F4)

旧 7 类型 (v0.5.1):    fact, decision, preference, bug_fix, feature_design, code_optimize, tech_knowledge
新 6 类型 (v0.6.0):    solution, decision, lesson, process, task, briefing

映射规则:
  bug_fix        → solution              (自动)
  code_optimize  → lesson                (自动)
  fact           → solution 或 lesson     (手动确认)
  feature_design → solution              (手动确认)
  tech_knowledge → lesson                (手动确认)
  preference     → DELETE                (无新版本等效类型)
  decision       → KEEP                  (新旧类型一致)
"""

import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from ..engine.memory import ContextMemory

# 旧 7 类型 (v0.5.1)
OLD_TYPES = {"fact", "decision", "preference", "bug_fix", "feature_design", "code_optimize", "tech_knowledge"}

# 新 6 类型 (v0.6.0)
NEW_TYPES = {"solution", "decision", "lesson", "process", "task", "briefing"}

# 自动迁移映射 (无需人工确认)
AUTO_MAP = {
    "bug_fix": "solution",
    "code_optimize": "lesson",
}

# 需要手动确认的类型及可选目标
MANUAL_CONFIRM_TYPES = {
    "fact": ["solution", "lesson"],  # 用户决定 → solution 或 → lesson
    "feature_design": ["solution"],  # → solution
    "tech_knowledge": ["lesson"],  # → lesson
}

# 删除类型 (无新版本等效类型)
DELETE_TYPES = {"preference"}

# 保留类型 (新旧一致)
KEEP_TYPES = {"decision"}

# 迁移状态标记 (写入 metadata)
MIGRATED_FROM_KEY = "_migrated_from"
MIGRATED_AT_KEY = "_migrated_at"

# 默认每批加载大小
_BATCH_SIZE = 500


def _get_all_memories(mem: ContextMemory) -> list[dict]:
    """分批获取所有记忆（使用 list_memories 分页）。"""
    all_items = []
    offset = 0
    while True:
        batch = mem.list_memories(
            limit=_BATCH_SIZE,
            offset=offset,
            include_archived=True,
        )
        if not batch:
            break
        all_items.extend(batch)
        offset += _BATCH_SIZE
    return all_items


def _get_old_type_store_data(mem: ContextMemory) -> dict:
    """分批获取所有迁移相关的原始存储数据（含文档和元数据）。

    包括:
    - 类型仍为旧类型的记录 (type in OLD_TYPES)
    - 已迁移的记录 (metadata 中含有 _migrated_from 标记)

    使用 ChromaDB 原生 limit/offset 分页，避免大数据量下 get() 截断。
    在 Python 层做过滤，因为 ChromaDB where 无法表达 "$exists" 条件。
    """
    all_ids: list[str] = []
    all_docs: list[str] = []
    all_metas: list[dict] = []
    offset = 0
    while True:
        batch = mem.store.get(
            limit=_BATCH_SIZE,
            offset=offset,
            include=["documents", "metadatas"],
        )
        ids = batch.get("ids", [])
        if not ids:
            break
        docs = batch.get("documents", [])
        metas = batch.get("metadatas", [])
        for i in range(len(ids)):
            m = metas[i] if i < len(metas) else {}
            m = m or {}
            t = m.get("type", "")
            if t in OLD_TYPES or MIGRATED_FROM_KEY in m:
                all_ids.append(ids[i])
                all_docs.append(docs[i] if i < len(docs) else "")
                all_metas.append(m)
        offset += len(ids)
    return {"ids": all_ids, "documents": all_docs, "metadatas": all_metas}


def _categorize_by_type(items: list[dict]) -> dict[str, list[dict]]:
    """按旧类型归类记忆列表。"""
    categories: dict[str, list[dict]] = {}
    for item in items:
        t = item.get("metadata", {}).get("type", "unknown")
        categories.setdefault(t, []).append(item)
    return categories


def _print_stats(categories: dict[str, list[dict]]):
    """打印分类统计信息。"""
    total = sum(len(v) for v in categories.values())
    print(f"总记忆数: {total}")
    print()
    print("按类型分布:")
    for t in sorted(categories.keys()):
        items = categories[t]
        if not items:
            continue
        print(f"  {t}: {len(items)} 条")
        if t in AUTO_MAP:
            print(f"    → 自动迁移至: {AUTO_MAP[t]}")
        elif t in MANUAL_CONFIRM_TYPES:
            print(f"    → 需手动确认: 可映射至 {', '.join(MANUAL_CONFIRM_TYPES[t])}")
        elif t in DELETE_TYPES:
            print("    → 将删除（无新版本等效类型）")
        elif t in KEEP_TYPES:
            print("    → 保留（新旧类型一致）")
        else:
            print("    → 未知类型")


# ============================================================
# 13.1 --dry-run
# ============================================================


def _do_dry_run(mem: ContextMemory):
    """扫描统计所有旧类型记忆，不修改数据。"""
    print("正在扫描记忆...")
    items = _get_all_memories(mem)
    old_items = [it for it in items if it.get("metadata", {}).get("type") in OLD_TYPES]
    categories = _categorize_by_type(old_items)
    _print_stats(categories)

    # 列出需手动确认的项
    confirm_items: list[dict] = []
    for t in MANUAL_CONFIRM_TYPES:
        confirm_items.extend(categories.get(t, []))

    if confirm_items:
        print(f"\n需手动确认的项 ({len(confirm_items)} 条):")
        for item in confirm_items[:20]:  # 最多显示 20 条
            meta = item.get("metadata", {})
            doc = item.get("document", "")
            print(f"  [{meta.get('type')}] {item['id'][:12]}...: {doc[:80]}")
        if len(confirm_items) > 20:
            print(f"  ... 共 {len(confirm_items)} 条 (仅展示前 20 条)")
    else:
        print("\n无需手动确认的项。")


# ============================================================
# 13.2 --apply
# ============================================================


def _do_apply(mem: ContextMemory):
    """执行自动迁移: bug_fix→solution, code_optimize→lesson。"""
    data = _get_old_type_store_data(mem)
    if not data["ids"]:
        print("没有旧类型记忆需要迁移。")
        return

    # 收集可自动迁移的记录下标
    auto_indices = []
    for i, meta in enumerate(data["metadatas"]):
        t = meta.get("type", "")
        if t in AUTO_MAP:
            auto_indices.append(i)

    if not auto_indices:
        print("没有需要自动迁移的记录。")
        return

    print(f"发现 {len(auto_indices)} 条可自动迁移的记录:")
    for idx in auto_indices:
        old_t = data["metadatas"][idx].get("type", "?")
        new_t = AUTO_MAP[old_t]
        doc = data["documents"][idx][:60]
        print(f"  [{old_t}→{new_t}] {data['ids'][idx][:12]}...: {doc}")

    confirm = input(f"\n确认迁移上述 {len(auto_indices)} 条记录? (y/N): ").strip().lower()
    if confirm != "y":
        print("已取消。")
        return

    count = 0
    now_ts = time.time()
    for idx in auto_indices:
        mid = data["ids"][idx]
        old_t = data["metadatas"][idx].get("type", "?")
        new_t = AUTO_MAP[old_t]
        meta = dict(data["metadatas"][idx])
        meta["type"] = new_t
        meta[MIGRATED_FROM_KEY] = old_t
        meta[MIGRATED_AT_KEY] = now_ts
        mem.store.update(ids=[mid], metadatas=[meta])
        count += 1

    print(f"[OK] 已迁移 {count} 条记录。")


# ============================================================
# 13.3 --confirm
# ============================================================


def _do_confirm(mem: ContextMemory):
    """交互式手动确认: 逐条展示模糊类型，让用户选择目标。"""
    data = _get_old_type_store_data(mem)
    if not data["ids"]:
        print("没有旧类型记忆需要处理。")
        return

    # 收集需要手动确认的记录下标
    confirm_indices = []
    for i, meta in enumerate(data["metadatas"]):
        t = meta.get("type", "")
        if t in MANUAL_CONFIRM_TYPES:
            confirm_indices.append(i)

    if not confirm_indices:
        print("没有需要手动确认的记录。")
        return

    print(f"需要手动确认 {len(confirm_indices)} 条记录\n")

    skip_count = 0
    mapped_count = 0
    deleted_count = 0
    now_ts = time.time()

    for idx in confirm_indices:
        mid = data["ids"][idx]
        t = data["metadatas"][idx].get("type", "?")
        doc = data["documents"][idx]
        options = MANUAL_CONFIRM_TYPES[t]

        print(f"[{t}] {mid[:12]}...")
        print(f"  内容: {doc[:120]}")
        print("  选项:")
        print("    1) 跳过 (不处理)")
        for j, opt in enumerate(options, 2):
            print(f"    {j}) 映射至 {opt}")
        print(f"    {len(options) + 2}) 删除")

        choice = input("  请选择 (默认 1): ").strip()
        if not choice or choice == "1":
            skip_count += 1
            continue

        if choice == str(len(options) + 2):
            mem.store.delete(ids=[mid])
            deleted_count += 1
            print("  → 已删除")
        elif choice.isdigit() and 2 <= int(choice) <= len(options) + 1:
            target = options[int(choice) - 2]
            meta = dict(data["metadatas"][idx])
            meta["type"] = target
            meta[MIGRATED_FROM_KEY] = t
            meta[MIGRATED_AT_KEY] = now_ts
            mem.store.update(ids=[mid], metadatas=[meta])
            mapped_count += 1
            print(f"  → 已映射至 {target}")
        else:
            skip_count += 1
            print("  → 无效输入，已跳过")
        print()

    print(f"处理完成: 映射 {mapped_count} 条, 删除 {deleted_count} 条, 跳过 {skip_count} 条")


# ============================================================
# 13.4 --mapping-file
# ============================================================


def _do_mapping_file(mem: ContextMemory, mapping_file: str):
    """从 JSON 文件读取 ID→类型映射并执行。"""
    path = Path(mapping_file)
    if not path.exists():
        print(f"[ERROR] 文件不存在: {mapping_file}", file=sys.stderr)
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        mappings = json.load(f)

    if not isinstance(mappings, dict):
        print("[ERROR] 映射文件必须是一个 JSON 对象 {记忆ID: 新类型}", file=sys.stderr)
        sys.exit(1)

    valid_types = NEW_TYPES | {"_delete_"}
    count = 0
    not_found = 0
    invalid_type = 0
    now_ts = time.time()

    for mem_id, new_type in mappings.items():
        if new_type not in valid_types:
            print(f"  [!] 跳过 {mem_id[:12]}...: 无效类型 '{new_type}'")
            invalid_type += 1
            continue

        existing = mem.get_memory(mem_id)
        if existing is None:
            not_found += 1
            continue

        meta = dict(existing["metadata"])
        old_t = meta.get("type", "")

        if new_type == "_delete_":
            mem.store.delete(ids=[mem_id])
            print(f"  [DEL] {mem_id[:12]}... ({old_t})")
        else:
            meta["type"] = new_type
            meta[MIGRATED_FROM_KEY] = old_t if old_t else ""
            meta[MIGRATED_AT_KEY] = now_ts
            mem.store.update(ids=[mem_id], metadatas=[meta])
            print(f"  [OK] {mem_id[:12]}...: {old_t} → {new_type}")
        count += 1

    print(f"\n处理完成: 映射 {count} 条, 未找到 {not_found} 条, 无效类型 {invalid_type} 条")


# ============================================================
# 13.5 --export-backup
# ============================================================


def _do_export_backup(mem: ContextMemory, backup_file: str):
    """导出旧类型记忆及已迁移记录到 JSON 备份文件，供回滚使用。

    包括:
    - 当前类型仍为旧类型的记录
    - 已迁移的记录（metadata 中含有 _migrated_from 标记）
    """
    data = _get_old_type_store_data(mem)
    if not data["ids"]:
        print("没有旧类型记忆需要备份。")
        return

    path = Path(backup_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    records = []
    for i in range(len(data["ids"])):
        records.append(
            {
                "id": data["ids"][i],
                "document": data["documents"][i],
                "metadata": data["metadatas"][i],
            }
        )

    if not records:
        print("没有旧类型记忆需要备份。")
        return

    export = {
        "format_version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "total": len(records),
        "records": records,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)

    print(f"[OK] 已导出 {len(records)} 条记录至: {path.resolve()}")

    # 按类型统计
    type_counts: dict[str, int] = {}
    for r in records:
        t = r["metadata"].get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    print("导出类型分布:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c} 条")


# ============================================================
# 13.6 --cleanup
# ============================================================


def _auto_backup_before_cleanup(mem: ContextMemory, backup_path: Path) -> bool:
    """清理前自动创建备份。返回是否成功。"""
    data = _get_old_type_store_data(mem)
    if not data["ids"]:
        return False

    records = []
    for i in range(len(data["ids"])):
        records.append(
            {
                "id": data["ids"][i],
                "document": data["documents"][i],
                "metadata": data["metadatas"][i],
            }
        )

    export = {
        "format_version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "total": len(records),
        "records": records,
    }

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)
    print(f"[OK] 自动备份已创建: {backup_path.resolve()} ({len(records)} 条)")
    return True


def _find_existing_backup() -> Path | None:
    """检查是否存在已有的手动备份文件。

    扫描 etc/ 目录下形如 migration-backup-*.json 的文件，取最新的。
    """
    from ..config import get_memos_home

    etc_dir = Path(get_memos_home()) / "etc"
    if not etc_dir.exists():
        return None
    backups = sorted(etc_dir.glob("migration-backup-*.json"))
    return backups[-1] if backups else None


def _do_cleanup(mem: ContextMemory):
    """清理 preference 和未映射的旧类型记忆。

    先检查是否存在已有备份，若无则自动创建后再执行清理。
    """
    # 加载全量旧类型数据
    data = _get_old_type_store_data(mem)
    if not data["ids"]:
        print("没有旧类型记忆需要清理。")
        return

    # 确定要删除的记录
    to_delete: list[tuple[str, str, str]] = []  # (id, type, reason)
    for i, mid in enumerate(data["ids"]):
        t = data["metadatas"][i].get("type", "")
        meta = data["metadatas"][i]
        if t in DELETE_TYPES:
            to_delete.append((mid, t, f"{t} (无新版本等效类型)"))
        elif t in MANUAL_CONFIRM_TYPES and MIGRATED_FROM_KEY not in meta:
            to_delete.append((mid, t, f"未映射的 {t}"))

    if not to_delete:
        print("没有需要清理的记录。")
        return

    print(f"发现 {len(to_delete)} 条需要清理的记录:")
    for mid, t, reason in to_delete[:30]:
        print(f"  {mid[:12]}...: {t} ({reason})")
    if len(to_delete) > 30:
        print(f"  ... 共 {len(to_delete)} 条 (仅展示前 30 条)")

    # Guard: 确保有可用备份
    existing_backup = _find_existing_backup()
    if existing_backup:
        print(f"发现已有备份: {existing_backup}")
        backup_ok = True
    else:
        print("未找到已有备份，将在清理前自动创建...")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        from ..config import get_memos_home

        auto_backup = Path(get_memos_home()) / "etc" / f"migration-backup-{ts}.json"
        backup_ok = _auto_backup_before_cleanup(mem, auto_backup)

    if not backup_ok:
        print("[ERROR] 备份失败，终止清理。", file=sys.stderr)
        sys.exit(1)

    confirm = input(f"\n确认删除上述 {len(to_delete)} 条记录? (y/N): ").strip().lower()
    if confirm != "y":
        print("已取消。")
        return

    for mid, t, _ in to_delete:
        mem.store.delete(ids=[mid])

    print(f"[OK] 已删除 {len(to_delete)} 条记录。")


# ============================================================
# 13.9 --purge
# ============================================================

# 要彻底清除的旧类型（OLD_TYPES - KEEP_TYPES，即不含 decision）
PURGE_TYPES = (
    OLD_TYPES - KEEP_TYPES
)  # {"fact", "preference", "bug_fix", "feature_design", "code_optimize", "tech_knowledge"}

# 旧类型转移标记键
MIGRATED_MARKERS = {MIGRATED_FROM_KEY, MIGRATED_AT_KEY}


def _scan_purgeable(mem: ContextMemory) -> list[dict]:
    """分批扫描所有旧类型记录（跨项目），返回完整记录列表。"""
    all_records = []
    offset = 0
    batch_size = _BATCH_SIZE
    while True:
        batch = mem.store.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        ids = batch.get("ids", [])
        if not ids:
            break
        docs = batch.get("documents", []) or []
        metas = batch.get("metadatas", []) or []
        for i, mid in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            meta = meta or {}
            t = meta.get("type", "")
            if t in PURGE_TYPES:
                all_records.append(
                    {
                        "id": mid,
                        "document": docs[i] if i < len(docs) else "",
                        "metadata": meta,
                    }
                )
        offset += len(ids)
    return all_records


def _create_purge_backup(mem: ContextMemory, records: list[dict], backup_path: Path) -> int:
    """创建清理备份，返回备份的记录数。"""
    if not records:
        return 0
    export = {
        "format_version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "operation": "purge_legacy_types",
        "total": len(records),
        "records": records,
    }
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)
    return len(records)


def _do_purge(mem: ContextMemory):
    """彻底删除残留的旧 6 类数据（自动备份后执行）。

    删除范围（OLD_TYPES - KEEP_TYPES）:
      fact, preference, bug_fix, feature_design, code_optimize, tech_knowledge
    decision 保留（新旧类型一致）。

    清理前自动备份到 MEMOS_HOME/etc/purge-backup-*.json。
    """
    from ..config import get_memos_home

    # ---- Step 1: 扫描 ----
    print("正在扫描旧 6 类数据...")
    records = _scan_purgeable(mem)

    if not records:
        print("✅ 未发现残留的旧类型数据，无需清理。")
        return

    # ---- Step 2: 统计 ----
    type_counts: dict[str, int] = {}
    project_counts: dict[str, int] = {}
    for r in records:
        t = r["metadata"].get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
        pid = r["metadata"].get("project_id", "（无）")
        project_counts[pid] = project_counts.get(pid, 0) + 1

    print(f"\n📊 共发现 {len(records)} 条旧类型记录：")
    print("\n按类型分布：")
    for t in sorted(type_counts):
        print(f"  {t}: {type_counts[t]} 条")
    print("\n按项目分布：")
    for pid in sorted(project_counts):
        print(f"  {pid}: {project_counts[pid]} 条")

    # 展示前 20 条样本
    print(f"\n前 {min(20, len(records))} 条样本：")
    for r in records[:20]:
        t = r["metadata"].get("type", "?")
        pid = r["metadata"].get("project_id", "?")
        doc = (r["document"] or "")[:60]
        print(f"  [{t}] project={pid}  {r['id'][:12]}...: {doc}")
    if len(records) > 20:
        print(f"  ... 共 {len(records)} 条")

    # ---- Step 3: 自动备份 ----
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = Path(get_memos_home()) / "etc" / f"purge-backup-{ts}.json"
    backed = _create_purge_backup(mem, records, backup_path)
    if backed == 0:
        print("[ERROR] 备份失败，终止清理。")
        return
    print(f"\n💾 自动备份已创建: {backup_path.resolve()} ({backed} 条)")

    # ---- Step 4: 确认 ----
    print(f"\n⚠️  即将从 ChromaDB 中永久删除上述 {len(records)} 条记录。")
    confirm = input("确认删除? 此操作不可逆，但可通过备份恢复 (y/N): ").strip().lower()
    if confirm != "y":
        print("已取消。")
        return

    # ---- Step 5: 执行删除 ----
    ids_to_delete = [r["id"] for r in records]
    mem.store.delete(ids=ids_to_delete)
    print(f"✅ 已删除 {len(ids_to_delete)} 条旧类型记录。")

    # ---- Step 6: 可选清理迁移标记 ----
    # 扫描残留的 _migrated_from 标记（旧记录已删除，但部分已迁移记录的标记可能残留）
    print("\n正在检查残留的迁移标记...")
    marker_count = 0
    offset = 0
    while True:
        batch = mem.store.get(
            limit=_BATCH_SIZE,
            offset=offset,
            include=["metadatas"],
        )
        mids = batch.get("ids", [])
        if not mids:
            break
        metas = batch.get("metadatas", []) or []
        for i, mid in enumerate(mids):
            meta = metas[i] if i < len(metas) else {}
            meta = meta or {}
            if any(k in meta for k in MIGRATED_MARKERS):
                cleaned = dict(meta)
                for k in MIGRATED_MARKERS:
                    cleaned[k] = "" if k != MIGRATED_AT_KEY else 0
                mem.store.update(ids=[mid], metadatas=[cleaned])
                marker_count += 1
        offset += len(mids)

    if marker_count > 0:
        print(f"✅ 已清理 {marker_count} 条记录的迁移状态标记。")
    else:
        print("无残留迁移标记。")

    print(f"\n🎉 旧 6 类数据清理完成。备份位于: {backup_path.resolve()}")


# ============================================================
# 13.7 --verify
# ============================================================


def _do_verify(mem: ContextMemory):
    """验证迁移结果: 检查类型分布并抽样核对映射准确性。"""
    data = _get_old_type_store_data(mem)
    if not data["ids"]:
        print("没有旧类型记忆需要验证。")
        return

    # 统计当前类型分布
    type_counts: dict[str, int] = {}
    migrated_count = 0
    migrated_items: list[tuple[str, dict]] = []

    for i, mid in enumerate(data["ids"]):
        meta = data["metadatas"][i]
        t = meta.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
        if MIGRATED_FROM_KEY in meta:
            migrated_count += 1
            migrated_items.append((mid, meta))

    print("当前旧类型记忆分布:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c} 条")
    print(f"已迁移标记: {migrated_count} 条")
    print(f"未迁移: {len(data['ids']) - migrated_count} 条")

    # 按类型统计迁移状态
    # 收集未迁移的旧类型
    unmigrated: dict[str, int] = {}
    for i, mid in enumerate(data["ids"]):
        meta = data["metadatas"][i]
        t = meta.get("type", "unknown")
        if MIGRATED_FROM_KEY not in meta:
            unmigrated[t] = unmigrated.get(t, 0) + 1
    if unmigrated:
        print("\n未迁移的类型分布 (可能需 --confirm 或 --mapping-file):")
        for t, c in sorted(unmigrated.items()):
            print(f"  {t}: {c} 条")

    # 抽样验证 5%
    if not migrated_items:
        print("\n没有已迁移记录可供抽样验证。")
        return

    sample_count = max(1, len(migrated_items) * 5 // 100)
    sample = random.sample(migrated_items, min(sample_count, len(migrated_items)))

    print(f"\n随机抽样 {len(sample)}/{len(migrated_items)} 条验证 (5%):")
    mismatches: list[tuple[str, str, str, str | list[str]]] = []

    for mid, meta in sample:
        old_t = meta.get(MIGRATED_FROM_KEY, "?")
        new_t = meta.get("type", "?")

        if old_t in AUTO_MAP:
            expected = AUTO_MAP[old_t]
            if new_t != expected:
                mismatches.append((mid, old_t, new_t, expected))
                print(f"  [!!] {mid[:12]}...: {old_t} → {new_t} (期望: {expected})")
            else:
                print(f"  [OK] {mid[:12]}...: {old_t} → {new_t}")
        elif old_t in MANUAL_CONFIRM_TYPES:
            expected_options = MANUAL_CONFIRM_TYPES[old_t]
            if new_t not in expected_options:
                mismatches.append((mid, old_t, new_t, expected_options))
                print(f"  [!!] {mid[:12]}...: {old_t} → {new_t} (期望其中之一: {expected_options})")
            else:
                print(f"  [OK] {mid[:12]}...: {old_t} → {new_t}")
        elif old_t in DELETE_TYPES:
            # preference 类型本应被删除，不应该出现在抽样中
            mismatches.append((mid, old_t, new_t, "DELETE"))
            print(f"  [!!] {mid[:12]}...: {old_t} 应已被删除，但仍存在")
        else:
            print(f"  [-] {mid[:12]}...: {old_t} → {new_t} (保留类型)")

    if mismatches:
        print(f"\n[!!] 发现 {len(mismatches)} 处映射不匹配:")
        for mid, old_t, new_t, expected in mismatches:
            expected_str = expected if isinstance(expected, str) else str(expected)
            print(f"      {mid[:12]}...: {old_t} → {new_t} (期望: {expected_str})")
    else:
        print("\n[OK] 所有抽样验证通过。")


# ============================================================
# 13.8 --rollback
# ============================================================


def _clean_migration_state(mem: ContextMemory):
    """C3 约束: 清理迁移状态标记 (_migrated_from / _migrated_at)。

    ChromaDB update() 是部分更新（仅写入提供的字段，不能删除字段），
    因此需要显式将标记字段设为空值以覆盖旧值。
    """
    all_data = mem.store.get(include=["metadatas"])
    count = 0
    if all_data.get("ids"):
        for i, mid in enumerate(all_data["ids"]):
            meta = dict((all_data.get("metadatas") or [])[i] or {})
            if MIGRATED_FROM_KEY in meta or MIGRATED_AT_KEY in meta:
                # ChromaDB update 不能删除字段，必须显式设空
                meta[MIGRATED_FROM_KEY] = ""
                meta[MIGRATED_AT_KEY] = 0
                mem.store.update(ids=[mid], metadatas=[meta])
                count += 1
    return count


def _do_rollback(mem: ContextMemory, backup_file: str):
    """从备份文件恢复旧类型记忆，回滚前清理迁移状态标记。"""
    path = Path(backup_file)
    if not path.exists():
        print(f"[ERROR] 备份文件不存在: {backup_file}", file=sys.stderr)
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        backup = json.load(f)

    records = backup.get("records", [])
    if not records:
        print("备份文件为空，没有需要恢复的记录。")
        return

    print(f"将从备份恢复 {len(records)} 条记录。")

    # C3 约束: 回滚前清理迁移状态
    cleaned = _clean_migration_state(mem)
    if cleaned > 0:
        print(f"  [OK] 已清理 {cleaned} 条迁移状态标记。")

    confirm = input(f"\n确认回滚? 这将覆盖 {len(records)} 条记忆的当前数据 (y/N): ").strip().lower()
    if confirm != "y":
        print("已取消。")
        return

    restored = 0
    not_found = 0
    errors = 0
    for record in records:
        mid = record["id"]
        doc = record["document"]
        meta = dict(record["metadata"])

        existing = mem.get_memory(mid)
        if existing is None:
            not_found += 1
            continue

        # ChromaDB update() 不删除字段，须显式清空迁移标记
        meta[MIGRATED_FROM_KEY] = ""
        meta[MIGRATED_AT_KEY] = 0

        try:
            # 文档内容未变时复用现有 embedding，避免加载模型
            if existing["document"] == doc:
                mem.store.update(ids=[mid], metadatas=[meta])
            else:
                mem._ensure_encoder()
                embedding = mem._encoder.encode(doc).tolist()
                mem.store.update(ids=[mid], documents=[doc], embeddings=[embedding], metadatas=[meta])
            restored += 1
        except Exception as e:
            print(f"  [ERROR] 恢复失败 {mid[:12]}...: {e}")
            errors += 1

    print(f"[OK] 已恢复 {restored} 条记录。")
    if not_found:
        print(f"[!] {not_found} 条记录在数据库中不存在 (可能已被删除)。")
    if errors:
        print(f"[!] {errors} 条记录恢复失败。")


# ============================================================
# 入口
# ============================================================


def cmd_migrate_types(args):
    """迁移旧 7 类型到新 6 类型。"""
    mem = ContextMemory()

    if args.dry_run:
        _do_dry_run(mem)
    elif args.apply:
        _do_apply(mem)
    elif args.confirm:
        _do_confirm(mem)
    elif args.mapping_file:
        _do_mapping_file(mem, args.mapping_file)
    elif args.export_backup:
        _do_export_backup(mem, args.export_backup)
    elif args.cleanup:
        _do_cleanup(mem)
    elif args.purge:
        _do_purge(mem)
    elif args.verify:
        _do_verify(mem)
    elif args.rollback:
        _do_rollback(mem, args.rollback)
    elif args.help_types:
        _print_help()
    else:
        _print_help()


def _print_help():
    """打印 types 子命令的帮助信息。"""
    print("用法: memos migrate types [选项]")
    print()
    print("旧 7 类型 → 新 6 类型迁移工具 (F4)")
    print()
    print("选项:")
    print("  --dry-run                    扫描统计，不修改数据")
    print("  --apply                      执行自动迁移 (bug_fix→solution, code_optimize→lesson)")
    print("  --confirm                    交互式确认模糊类型 (fact/feature_design/tech_knowledge)")
    print("  --mapping-file <file>        从 JSON 文件读取 ID→类型映射并执行")
    print("  --export-backup <file>       导出所有旧类型记忆到 JSON 备份文件")
    print("  --cleanup                    清理 preference 和未映射的旧类型记忆 (自动备份)")
    print("  --purge                      彻底删除残留的旧 6 类数据 (自动备份，迁移完成后执行)")
    print("  --verify                     验证迁移结果 (类型分布 + 抽样核对)")
    print("  --rollback <file>            从备份文件回滚迁移")
    print()
    print("映射规则:")
    print("  bug_fix        → solution        (自动)")
    print("  code_optimize  → lesson          (自动)")
    print("  fact           → solution/lesson (手动确认)")
    print("  feature_design → solution        (手动确认)")
    print("  tech_knowledge → lesson          (手动确认)")
    print("  preference     → DELETE          (无新版本等效类型)")
    print("  decision       → KEEP            (新旧一致)")
