from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ...config import config

# 本模块特有导入
from ...features.backup import clean_stale_lock

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/backup/trigger")
def trigger_backup(request: Request):
    """触发全量物理备份（在后台线程执行，立即返回）。"""
    from ...features.backup import start_async_backup

    # 先清理可能残留的过期锁
    clean_stale_lock()
    result = start_async_backup()
    if result["ok"]:
        return {"ok": True, "message": result["message"]}
    raise HTTPException(409, result["error"])


@router.get("/api/backup/progress")
def backup_progress(request: Request):
    """获取当前备份进度（前端轮询用）。"""
    from ...features.backup import get_backup_status

    return get_backup_status()


@router.get("/api/backup/status")
def backup_status(request: Request):
    """获取备份状态：上次备份时间、备份总数、健康状态。"""
    from ...features.backup import list_backups

    result = list_backups()
    backups = result.get("backups", [])

    # 判断健康状态
    health = "normal"
    if not backups:
        health = "no_backups"
    else:
        latest = backups[0]
        if latest.get("status") == "partial":
            health = "partial"
        elif latest.get("status") == "missing":
            health = "missing"
        # 检查是否有部分备份
        partial_count = sum(1 for b in backups if b.get("status") == "partial")
        if partial_count > 0:
            health = "warning"

    # 计算距离上次备份天数
    days_since = None
    if backups:
        latest_ts = backups[0].get("timestamp", 0)
        if latest_ts:
            days_since = int((__import__("time").time() - latest_ts) / 86400)

    return {
        "total": result["total"],
        "max_backups": result["max_backups"],
        "target_dir": result["target_dir"],
        "days_since_export": result.get("days_since_export"),
        "days_since_backup": days_since,
        "health": health,
        "latest": backups[0] if backups else None,
        "remind_after_days": config.backup.remind_after_days,
    }


@router.get("/api/backups/list")
def list_backups_api(request: Request):
    """获取备份列表（含详细信息）。"""
    from ...features.backup import list_backups

    result = list_backups()
    for b in result.get("backups", []):
        bp = b.get("path", "")
        p = __import__("pathlib").Path(bp) if bp else None
        if p and p.exists():
            file_count = 0
            total_size = 0
            for entry in p.rglob("*"):
                if entry.is_file():
                    file_count += 1
                    total_size += entry.stat().st_size
            b["file_count"] = file_count
            b["size_mb"] = round(total_size / (1024 * 1024), 2)
            b["path"] = str(p)
        else:
            b["file_count"] = 0
            b["size_mb"] = 0
        ts = b.get("timestamp", 0)
        if ts:
            b["date"] = __import__("datetime").datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    return result


@router.delete("/api/backups/{backup_name}")
def delete_backup_api(request: Request, backup_name: str):
    """删除指定备份。"""
    from ...features.backup import delete_backup

    result = delete_backup(backup_name)
    if result["ok"]:
        return {"ok": True, "message": result["message"]}
    raise HTTPException(404, result["error"])
