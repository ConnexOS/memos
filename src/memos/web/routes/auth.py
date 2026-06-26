"""认证路由 — POST /api/auth/login, POST /api/auth/logout"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ...config import config
from ...web.auth import create_session_token, verify_token_against_users

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    token: str = Field(min_length=1)


class LoginResponse(BaseModel):
    success: bool
    creator_id: str | None = None
    role: str | None = None


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    """验证 token 并创建 session（优先 users.json，兼容旧版单 Token）。"""
    creator_id = None
    role = None

    # 验证 users.json 中的 Token
    user = verify_token_against_users(req.token)
    if user:
        creator_id = user["creator_id"]
        role = user["role"]
    else:
        raise HTTPException(status_code=401, detail="无效的 Token")

    request.session["creator_id"] = creator_id
    request.session["role"] = role
    request.session["name"] = user["name"]

    # 设置 memos_session cookie（JWT），兼容 Web UI 的 cookie 认证
    token_hash_val = hashlib.sha256(req.token.encode()).hexdigest()
    session_token = create_session_token(token_hash_val, config.auth.secret_key, config.auth.session_ttl)
    response.set_cookie(
        key="memos_session",
        value=session_token,
        httponly=True,
        samesite="strict",
        max_age=config.auth.session_ttl,
    )

    return LoginResponse(success=True, creator_id=creator_id, role=role)


@router.post("/logout")
async def logout(request: Request, response: Response):
    """清除 session 并过期 cookie。"""
    request.session.clear()
    response.delete_cookie(key="memos_session", path="/")
    return {"success": True}
