from __future__ import annotations

import logging
import time as _time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..._version import __version__ as memos_version
from ...config import config
from ..app import templates
from ..auth import verify_session_token
from ..services.helpers import _get_notification_context

# 开发模式：用启动时间戳做缓存爆裂，防止 JS/CSS 缓存
memos_version = f"{memos_version}-{int(_time.time())}"

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/favicon.ico")
async def favicon():
    """返回内嵌 SVG favicon，避免浏览器 404 日志噪音。"""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
    <rect width="32" height="32" rx="6" fill="#4f46e5"/>
    <text x="16" y="22" font-size="18" fill="white" text-anchor="middle" font-family="Arial">M</text>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """登录页面。P3-4: 已登录用户自动跳转首页。auth.disable=True 时直接跳转首页。"""
    if config.auth.disable:
        return RedirectResponse("/")
    token_str = request.cookies.get("memos_session")
    if token_str and verify_session_token(token_str, config.auth.secret_key):
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "login.html")


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    notif_ctx = _get_notification_context()
    hdrs = {"Cache-Control": "no-cache, no-store, must-revalidate"}
    user_name = request.session.get("name", "")
    user_role = request.session.get("role", "")
    ctx = {
        "notifications": notif_ctx,
        "version": memos_version,
        "user_name": user_name,
        "user_role": user_role,
        "auth_disabled": config.auth.disable,
    }
    if config.auth.disable:
        return templates.TemplateResponse(request, "dashboard.html", ctx, headers=hdrs)
    token_str = request.cookies.get("memos_session")
    if not token_str or not verify_session_token(token_str, config.auth.secret_key):
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "dashboard.html", ctx, headers=hdrs)


# --- API: 记忆列表 ---


# trigger-reload
