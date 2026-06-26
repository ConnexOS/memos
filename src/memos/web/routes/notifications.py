from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...config import config
from ..app import templates

# 本模块特有导入
from ..auth import verify_session_token
from ..dependencies import get_project_id
from ..services.helpers import _format_time_ago, _get_notification_context

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request):
    """通知列表页"""
    if not config.auth.disable:
        token_str = request.cookies.get("memos_session")
        if not token_str or not verify_session_token(token_str, config.auth.secret_key):
            return RedirectResponse("/login")
    from ...features.notifications import get_notification_logger

    notifier = get_notification_logger()
    type_filter = request.query_params.getlist("type")
    status_filter = request.query_params.get("status")
    page = int(request.query_params.get("page", 1))
    limit = 20

    notif_list, total = notifier.list_notifications(
        type_filter=type_filter if type_filter else None,
        status=status_filter,
        limit=limit,
        offset=(page - 1) * limit,
    )
    for n in notif_list:
        n["_time_ago"] = _format_time_ago(n.get("timestamp", 0))

    # 过期检查（按需）
    _check_expiry_notify(request)

    notif_ctx = _get_notification_context()
    return templates.TemplateResponse(
        request,
        "notifications.html",
        {
            "notifications": notif_ctx,
            "notif_list": notif_list,
            "total": total,
            "page": page,
            "limit": limit,
            "type_filter": type_filter,
            "status_filter": status_filter,
        },
    )


def _check_expiry_notify(request: Request):
    """按需过期检查，发现即将过期/已过期记忆时触发通知。"""
    try:
        from ...features.notifications import get_notification_logger

        notifier = get_notification_logger()
        mem = request.app.state.context_memory
        expiry = mem.get_expiry_status()
        expiring = expiry.get("expiring_soon", 0)
        expired = expiry.get("expired", 0)
        if expiring > 0 or expired > 0:
            notifier.notify(
                type="expiry_alert",
                title=f"知识过期提醒 — {expiring} 条即将过期 / {expired} 条已过期",
                message=f"系统检测到 {expiring} 条记忆即将过期，{expired} 条已过期。请及时审查。",
                link="/?tab=daily-review",
            )
    except Exception:
        logger.debug("过期检查失败（不影响页面加载）", exc_info=True)


@router.post("/api/notifications/{notif_id}/read")
def mark_notification_read(
    request: Request,
    notif_id: str,
    project_id: str = Depends(get_project_id),  # P0-3: 预留 project_id，保持 API 契约一致
):
    from ...features.notifications import get_notification_logger

    notifier = get_notification_logger()
    ok = notifier.mark_read(notif_id)
    # 表单提交则重定向，AJAX 则返回 JSON
    if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        return RedirectResponse(request.headers.get("referer", "/notifications"), 302)
    if ok:
        return {"ok": True}
    raise HTTPException(404, "通知不存在")


@router.post("/api/notifications/{notif_id}/dismiss")
def dismiss_notification(
    request: Request,
    notif_id: str,
    project_id: str = Depends(get_project_id),  # P0-3: 预留 project_id，保持 API 契约一致
):
    from ...features.notifications import get_notification_logger

    notifier = get_notification_logger()
    ok = notifier.dismiss(notif_id)
    if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        return RedirectResponse(request.headers.get("referer", "/notifications"), 302)
    if ok:
        return {"ok": True}
    raise HTTPException(404, "通知不存在")


@router.get("/api/notifications/unread-count")
def unread_notification_count(request: Request):
    from ...features.notifications import get_notification_logger

    notifier = get_notification_logger()
    return notifier.get_unread_counts()


@router.post("/api/notifications/renew-all-expired")
def renew_all_expired(
    request: Request,
    project_id: str = Depends(get_project_id),  # P0-1: 新增 project_id，防止跨项目泄漏
):
    """续期所有已过期/即将过期的记忆。"""
    mem = request.app.state.context_memory
    expiry = mem.get_expiry_status()
    total = 0
    renewed = False

    # 查找并续期即将过期的
    if expiry.get("expiring_soon", 0) > 0:
        now = time.time()
        archive_sec = config.memory.archive_days * 86400
        warn_sec = config.memory.expiry_warn_days * 86400
        records = mem.store.get(
            where={
                "$and": [
                    {"timestamp": {"$gte": now - archive_sec}},
                    {"timestamp": {"$lt": now - archive_sec + warn_sec}},
                    {"status": "active"},
                    {"project_id": project_id},  # P0-1: 限定当前项目
                ]
            },
            include=["metadatas"],
        )
        for mid in records.get("ids", []):
            mem.renew_memory(mid)
            total += 1
            renewed = True

    # 查找并续期已过期的
    if expiry.get("expired", 0) > 0:
        expired_cutoff = time.time() - config.memory.archive_days * 86400
        records = mem.store.get(
            where={
                "$and": [
                    {"timestamp": {"$lt": expired_cutoff}},
                    {"status": "active"},
                    {"project_id": project_id},  # P0-1: 限定当前项目
                ]
            },
            include=["metadatas"],
        )
        for mid in records.get("ids", []):
            mem.renew_memory(mid)
            total += 1
            renewed = True

    logger.info("批量续期: %d 条记忆", total)

    # 忽略所有过期提醒通知
    if renewed:
        try:
            from ...features.notifications import get_notification_logger

            notifier = get_notification_logger()
            all_notifs, _ = notifier.list_notifications(type_filter=["expiry_alert"], limit=1000)
            for n in all_notifs:
                notifier.dismiss(n["id"])
        except Exception:
            logger.debug("批量关闭过期提醒通知失败", exc_info=True)

    return {"ok": True, "renewed": total, "message": f"已续期 {total} 条记忆"}
