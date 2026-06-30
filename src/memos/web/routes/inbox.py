"""v0.7.2: 收件箱 API — 三区聚合查询 + 未读轮询 + 批量操作"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ...features.notifications import get_notification_logger
from ..dependencies import get_project_id
from ..services.helpers import _format_time_ago

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inbox")


@router.get("/items")
def get_inbox_items(request: Request, project_id: str = Depends(get_project_id)):
    """聚合查询：系统通知 + 待关注 + 待修正三区数据。"""
    notifier = get_notification_logger()
    all_notifs, _ = notifier.list_notifications(limit=1000)

    # 1. 系统通知（全部未读通知 — watchlist_update 也在系统通知区渲染，点击"查看"切换至待关注 tab）
    system = []
    for n in all_notifs:
        if n.get("read") or n.get("dismissed"):
            continue
        n["_time_ago"] = _format_time_ago(n.get("timestamp", 0))
        system.append(n)

    # 2. 待修正（quality_alert + conflict_detected 子集）
    pending_review = [n for n in system if n.get("type") in ("quality_alert", "conflict_detected")]

    # 3. 待关注（ChromaDB type=watchlist）
    watchlist = []
    mem = getattr(request.app.state, "context_memory", None)
    if mem:
        try:
            results = mem.store.get(
                where={"$and": [{"type": "watchlist"}, {"project_id": project_id}]},
                include=["metadatas", "documents"],
            )
            for i, mid in enumerate(results.get("ids", [])):
                doc = (results["documents"] or [""] * len(results["ids"]))[i]
                meta = (results["metadatas"] or [{}] * len(results["ids"]))[i]
                watchlist.append({"id": mid, "text": str(doc)[:200], "metadata": meta or {}})
        except Exception as e:
            logger.debug("watchlist 查询失败: %s", e)

    return {
        "system_notifications": system,
        "pending_review": pending_review,
        "watchlist": watchlist,
    }


@router.get("/unread-count")
def inbox_unread_count(request: Request):
    """统一未读数（仅 JSONL 通知区）。"""
    notifier = get_notification_logger()
    return notifier.get_unread_counts()


@router.post("/dismiss/{notif_id}")
def dismiss_inbox_item(notif_id: str, project_id: str = Depends(get_project_id)):
    """忽略/已读单条通知。"""
    notifier = get_notification_logger()
    ok = notifier.dismiss(notif_id)
    if ok:
        return {"ok": True}
    raise HTTPException(404, "通知不存在")


@router.post("/dismiss-all")
def dismiss_all_inbox(project_id: str = Depends(get_project_id)):
    """全部已读（仅 JSONL 通知，不操作 ChromaDB watchlist）。"""
    notifier = get_notification_logger()
    all_notifs, _ = notifier.list_notifications(limit=10000)
    for n in all_notifs:
        if not n.get("read") and not n.get("dismissed"):
            notifier.dismiss(n["id"])
    return {"ok": True}
