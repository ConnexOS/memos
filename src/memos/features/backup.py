"""ChromaDB 物理备份与恢复模块

F2 数据备份与恢复——文件级全量快照。备份内容包含 ChromaDB 完整数据（含嵌入向量），
与 F1 逻辑导出（.memos JSONL）互补。

备份策略：
- 按个数保留（max_backups，默认 10），超出自动清理最旧备份
- 备份锁（backup.lock）防止并发，5 分钟自动过期
- 完整性校验：文件数 + 总大小对比
- manifest 索引：backup_manifest.json
- 后台异步执行：Dashboard 触发后返回立即响应，前端轮询进度
"""

import datetime
import json
import logging
import shutil
import time
from pathlib import Path
from threading import Lock, Thread

logger = logging.getLogger(__name__)

_backup_lock = Lock()

# 默认值（无配置时使用）
_DEFAULT_TARGET_DIR = "backups"  # 项目根目录下的 backups/（而非 memdb/backups，避免递归嵌套）
_DEFAULT_MAX_BACKUPS = 10
_MANIFEST_FILENAME = "backup_manifest.json"
_LOCK_FILENAME = "backup.lock"
_LOCK_TIMEOUT = 600  # 锁过期时间：10 分钟（足够大库完整备份）
_STALE_LOCK_TIMEOUT = 300  # 启动时清理阈值：5 分钟（开发期 --reload 残留通常在此范围内）

# --- 后台状态跟踪 ---
_backup_status = {
    "running": False,
    "progress": "",  # 当前阶段描述
    "result": None,  # 成功时：dict；失败时：{"error": str}
    "started_at": None,
    "elapsed": None,
}


def get_backup_status() -> dict:
    """获取当前备份状态的快照。"""
    s = dict(_backup_status)
    if s["running"] and s["started_at"]:
        s["elapsed"] = round(time.time() - s["started_at"], 1)
    return s


def start_async_backup(target: str = None) -> dict:
    """在后台线程启动备份，立即返回状态。

    返回 {"ok": True, "message": "备份已启动"} 或 {"ok": False, "error": "已有备份运行中"}。
    """
    if _backup_status["running"]:
        return {"ok": False, "error": "已有备份任务正在执行"}

    # 重置状态
    _backup_status["running"] = True
    _backup_status["progress"] = "初始化..."
    _backup_status["result"] = None
    _backup_status["started_at"] = time.time()
    _backup_status["elapsed"] = None

    t = Thread(target=_run_backup, args=(target,), daemon=True)
    t.start()
    return {"ok": True, "message": "备份已启动"}


def _run_backup(target: str = None) -> None:
    """后台线程包装：调用 backup_memdb 并捕获结果。"""
    try:
        _backup_status["progress"] = "正在复制数据..."
        result = backup_memdb(target)
        _backup_status["result"] = result
        _backup_status["progress"] = "完成"
    except Exception as e:
        logger.exception("后台备份失败")
        _backup_status["result"] = {"error": str(e)}
        _backup_status["progress"] = f"失败: {e}"
    finally:
        _backup_status["running"] = False
        if _backup_status["started_at"]:
            _backup_status["elapsed"] = round(time.time() - _backup_status["started_at"], 1)


# --- 锁管理 ---


def _acquire_lock(target_dir: Path) -> Path:
    """获取备份锁。返回锁文件路径。锁已存在且未过期时抛出 RuntimeError。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    lock_path = target_dir / _LOCK_FILENAME
    if lock_path.exists():
        try:
            lock_age = time.time() - lock_path.stat().st_mtime
            if lock_age > _LOCK_TIMEOUT:
                logger.warning("备份锁已过期（%.0f 分钟），强制释放", lock_age / 60)
                lock_path.unlink()
            else:
                raise RuntimeError("已有备份任务正在执行，请稍后再试")
        except FileNotFoundError:
            pass
    lock_path.touch()
    return lock_path


def _release_lock(lock_path: Path) -> None:
    """释放备份锁。最多重试 3 次，应对 Windows 文件锁定短暂延迟。"""
    for attempt in range(3):
        try:
            lock_path.unlink()
            return
        except FileNotFoundError:
            return  # 已被删除，视为成功
        except OSError as e:
            if attempt < 2:
                time.sleep(0.3)
            else:
                logger.warning("释放备份锁失败（重试 %d 次后放弃）: %s", attempt + 1, e)


def clean_stale_lock() -> bool:
    """清理过期的备份锁文件（使用较短超时 _STALE_LOCK_TIMEOUT，适合重启时清理）。返回是否清理了锁。"""
    target_dir = _get_target_dir(None)
    lock_path = target_dir / _LOCK_FILENAME
    if not lock_path.exists():
        return False
    try:
        lock_age = time.time() - lock_path.stat().st_mtime
        if lock_age > _STALE_LOCK_TIMEOUT:
            lock_path.unlink()
            logger.info("已清理过期备份锁（%.0f 分钟）", lock_age / 60)
            return True
        return False
    except (FileNotFoundError, OSError):
        return False


# --- 备份目录 / 配置辅助 ---


def _get_memdb_dir() -> Path:
    """获取 memdb 源目录路径。"""
    from ..config import config

    chroma_path = config.chroma.path
    if chroma_path:
        return Path(chroma_path)
    import os as _os

    home = _os.environ.get("MEMOS_HOME")
    if home:
        return Path(home) / "memdb"
    return Path.cwd() / "memdb"


def _get_target_dir(target: str = None) -> Path:
    """获取备份目标目录（优先参数，其次配置，最后默认值）。"""
    if target:
        return Path(target)
    try:
        from ..config import config

        if hasattr(config, "backup") and config.backup.target_dir:
            return Path(config.backup.target_dir)
    except Exception:
        pass
    return Path(_DEFAULT_TARGET_DIR)


def _get_max_backups() -> int:
    """获取最大备份保留个数。"""
    try:
        from ..config import config

        if hasattr(config, "backup"):
            return config.backup.max_backups
    except Exception:
        pass
    return _DEFAULT_MAX_BACKUPS


def _get_verify_flag() -> bool:
    """获取是否启用完整性校验。"""
    try:
        from ..config import config

        if hasattr(config, "backup"):
            return config.backup.verify_after_backup
    except Exception:
        pass
    return True


# --- Manifest 管理 ---


def _read_manifest(target_dir: Path) -> dict:
    """读取备份 manifest 文件。"""
    manifest_path = target_dir / _MANIFEST_FILENAME
    if not manifest_path.exists():
        return {"backups": [], "last_export_at": None}
    try:
        with open(manifest_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"backups": [], "last_export_at": None}


def _write_manifest(target_dir: Path, manifest: dict) -> None:
    """写入备份 manifest 文件。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = target_dir / _MANIFEST_FILENAME
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def _count_files_and_size(dir_path: Path) -> tuple[int, int]:
    """统计目录下的文件数和总大小（bytes）。"""
    file_count = 0
    total_size = 0
    for entry in dir_path.rglob("*"):
        if entry.is_file():
            file_count += 1
            total_size += entry.stat().st_size
    return file_count, total_size


# --- 核心备份逻辑 ---


def _clean_orphaned_backups(target_dir: Path) -> int:
    """清理未在 manifest 中登记的孤儿备份目录。返回清理数量。"""
    manifest = _read_manifest(target_dir)
    tracked = {Path(b["path"]).resolve() for b in manifest.get("backups", []) if b.get("path")}
    removed = 0
    if not target_dir.exists():
        return 0
    for entry in target_dir.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("memdb_") and entry.resolve() not in tracked:
            logger.info("清理孤儿备份目录: %s", entry)
            try:
                shutil.rmtree(entry)
                removed += 1
            except OSError as e:
                logger.warning("清理孤儿备份目录失败: %s", e)
    return removed


def backup_memdb(target: str = None) -> dict:
    """执行全量物理备份。

    返回 dict: {path, timestamp, size_bytes, file_count, elapsed_seconds, status}
    """
    with _backup_lock:
        memdb = _get_memdb_dir()
        if not memdb.exists():
            raise FileNotFoundError(f"memdb 目录不存在: {memdb}")

        target_dir = _get_target_dir(target)
        target_dir.mkdir(parents=True, exist_ok=True)

        lock_path = _acquire_lock(target_dir)
        try:
            # 清理孤儿备份（因进程中断残留的未登记备份目录）
            _clean_orphaned_backups(target_dir)

            # 清理超出数量限制的旧备份（保留 max_backups-1 个空位给新备份）
            max_backups = _get_max_backups()
            _cleanup_old_backups(target_dir, max(max_backups - 1, 1))

            # 备份目录命名
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"memdb_{ts}"
            backup_path = target_dir / backup_name

            logger.info("开始备份: %s → %s", memdb, backup_path)
            start = time.time()

            shutil.copytree(memdb, backup_path, ignore=shutil.ignore_patterns("backups"))

            elapsed = time.time() - start
            file_count, total_size = _count_files_and_size(backup_path)
            size_mb = total_size / (1024 * 1024)

            logger.info(
                "备份完成: %s (%d 文件, %.2f MB, 耗时 %.1f 秒)",
                backup_path,
                file_count,
                size_mb,
                elapsed,
            )

            # 完整性校验
            status = "complete"
            verify_flag = _get_verify_flag()
            if verify_flag:
                src_count, src_size = _count_files_and_size(memdb)
                if src_count != file_count or not _size_within_tolerance(src_size, total_size):
                    status = "partial"
                    logger.warning(
                        "备份完整性校验失败: 源=%d文件/%d字节, 备份=%d文件/%d字节",
                        src_count,
                        src_size,
                        file_count,
                        total_size,
                    )

            # 更新 manifest
            manifest = _read_manifest(target_dir)
            manifest["backups"].append(
                {
                    "id": backup_name,
                    "path": str(backup_path),
                    "timestamp": start,
                    "size_bytes": total_size,
                    "file_count": file_count,
                    "status": status,
                }
            )
            _write_manifest(target_dir, manifest)

            return {
                "path": str(backup_path),
                "timestamp": start,
                "size_bytes": total_size,
                "size_mb": round(size_mb, 2),
                "file_count": file_count,
                "elapsed_seconds": round(elapsed, 1),
                "status": status,
            }
        finally:
            _release_lock(lock_path)


def _size_within_tolerance(src_size: int, backup_size: int, tolerance: float = 0.01) -> bool:
    """检查源和备份大小差异是否在容差范围内（ChromaDB 文件可能有微小差异）。"""
    if src_size == 0:
        return backup_size == 0
    diff = abs(src_size - backup_size) / src_size
    return diff <= tolerance


def _cleanup_old_backups(target_dir: Path, max_backups: int) -> int:
    """清理超出数量限制的旧备份。返回清理数量。"""
    manifest = _read_manifest(target_dir)
    backups = manifest.get("backups", [])
    if len(backups) <= max_backups:
        return 0

    backups.sort(key=lambda b: b.get("timestamp", 0))
    to_remove = backups[: len(backups) - max_backups]
    removed = 0

    for entry in to_remove:
        bp = Path(entry["path"])
        if bp.exists():
            logger.info("清理旧备份: %s", bp)
            shutil.rmtree(bp)
            removed += 1
        else:
            logger.info("备份路径已不存在，从 manifest 移除: %s", bp)

    remaining = [b for b in backups if b not in to_remove]
    manifest["backups"] = remaining
    _write_manifest(target_dir, manifest)
    return removed


def list_backups(target: str = None) -> dict:
    """列出所有备份。返回按时间倒序排列的备份列表。"""
    target_dir = _get_target_dir(target)
    manifest = _read_manifest(target_dir)
    backups = manifest.get("backups", [])

    last_export_at = manifest.get("last_export_at")
    days_since_export = None
    if last_export_at:
        days_since_export = int((time.time() - last_export_at) / 86400)

    for b in backups:
        b.setdefault("status", "unknown")
        bp = Path(b["path"]) if "path" in b else None
        if bp and not bp.exists():
            b["status"] = "missing"

    backups.sort(key=lambda b: b.get("timestamp", 0), reverse=True)
    return {
        "backups": backups,
        "total": len(backups),
        "target_dir": str(target_dir),
        "max_backups": _get_max_backups(),
        "days_since_export": days_since_export,
    }


def mark_export_time(target: str = None) -> None:
    """更新 manifest 中最后一次 F1 export 时间。"""
    target_dir = _get_target_dir(target)
    manifest = _read_manifest(target_dir)
    manifest["last_export_at"] = time.time()
    _write_manifest(target_dir, manifest)


def delete_backup(backup_name: str, target: str = None) -> dict:
    """按备份名称删除指定备份（从 manifest + 文件系统）。"""
    target_dir = _get_target_dir(target)
    manifest = _read_manifest(target_dir)
    backups = manifest.get("backups", [])
    found = None
    for i, b in enumerate(backups):
        if b.get("id") == backup_name or b.get("name") == backup_name or b.get("timestamp") == backup_name:
            found = i
            break
    if found is None:
        return {"ok": False, "error": f"备份未找到: {backup_name}"}
    entry = backups.pop(found)
    bp = Path(entry["path"]) if entry.get("path") else None
    if bp and bp.exists():
        shutil.rmtree(bp)
        logger.info("已删除备份目录: %s", bp)
    manifest["backups"] = backups
    _write_manifest(target_dir, manifest)
    return {"ok": True, "message": f"已删除备份: {entry.get('name', backup_name)}"}


def restore_backup(backup_path: str, force: bool = False, interactive: bool = True) -> dict:
    """从指定备份恢复。

    Args:
        backup_path: 备份目录路径
        force: True 时跳过交互确认
        interactive: True 时在控制台打印提示并等待输入

    Returns: {success, message, elapsed_seconds}
    """
    bp = Path(backup_path)
    if not bp.exists():
        return {"success": False, "message": f"备份目录不存在: {backup_path}"}

    try:
        _verify_backup_structure(bp)
    except ValueError as e:
        if not force:
            return {"success": False, "message": f"备份结构校验失败: {e}。使用 --force 可强制恢复"}
        logger.warning("备份结构校验失败但 force=True，继续恢复: %s", e)

    file_count, total_size = _count_files_and_size(bp)
    size_mb = total_size / (1024 * 1024)
    memdb = _get_memdb_dir()

    info_lines = [
        f"备份路径: {bp}",
        f"文件数: {file_count}",
        f"大小: {size_mb:.2f} MB",
        f"数据目录: {memdb}",
    ]

    if interactive and not force:
        for line in info_lines:
            print(f"  {line}")
        print()
        print("  ⚠ 警告：此操作将覆盖当前 memdb/ 目录中的所有数据！")
        try:
            answer = input("  确认恢复？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return {"success": False, "message": "用户取消恢复"}
        if answer != "y":
            return {"success": False, "message": "用户取消恢复"}

    start = time.time()

    rollback_path = None
    if memdb.exists():
        rollback_path = memdb.parent / f"memdb.bak.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        logger.info("创建回退点: %s", rollback_path)
        shutil.move(str(memdb), str(rollback_path))

    try:
        logger.info("恢复备份: %s → %s", bp, memdb)
        shutil.copytree(bp, memdb)

        _verify_chromadb(memdb)

        elapsed = time.time() - start
        logger.info("恢复完成，耗时 %.1f 秒", elapsed)

        if rollback_path and rollback_path.exists():
            logger.info("删除回退点: %s", rollback_path)
            shutil.rmtree(rollback_path)
            rollback_path = None

        return {
            "success": True,
            "message": f"恢复完成: {file_count} 文件, {size_mb:.2f} MB, 耗时 {elapsed:.1f} 秒",
            "elapsed_seconds": round(elapsed, 1),
        }

    except Exception as e:
        logger.error("恢复失败: %s", e)
        if memdb.exists():
            try:
                shutil.rmtree(memdb)
            except PermissionError:
                logger.warning("无法删除恢复失败的 memdb 目录（文件被占用），将在下次重启时清理: %s", memdb)
        rolled_back = False
        if rollback_path and rollback_path.exists():
            logger.info("回退到原始数据: %s", rollback_path)
            shutil.move(str(rollback_path), str(memdb))
            rolled_back = True
        suffix = "（已回退到原始数据）" if rolled_back else ""
        return {"success": False, "message": f"恢复失败{suffix}: {e}"}


def _verify_backup_structure(backup_path: Path) -> None:
    """校验备份目录结构（必须是 ChromaDB 目录）。"""
    chroma_sqlite = backup_path / "chroma.sqlite3"
    if not chroma_sqlite.exists():
        raise ValueError(f"备份目录缺少 chroma.sqlite3: {backup_path}")
    uuid_dirs = [d for d in backup_path.iterdir() if d.is_dir() and len(d.name) >= 32]
    if not uuid_dirs:
        raise ValueError(f"备份目录无有效 collection 数据: {backup_path}")


def _verify_chromadb(memdb_path: Path) -> None:
    """验证恢复后的 ChromaDB 可正常读写。"""
    import chromadb

    client = None
    try:
        client = chromadb.PersistentClient(path=str(memdb_path))
        collections = client.list_collections()
        if not collections:
            raise RuntimeError("恢复后 ChromaDB 无可用的 collection")
        for col in collections:
            count = col.count()
            logger.info("ChromaDB 连接验证通过: collection=%s, count=%d", col.name, count)
        logger.info("ChromaDB 连接验证全部通过，共 %d 个 collection", len(collections))
    finally:
        if client is not None:
            del client
