"""认证路由 — POST /api/auth/login"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...config import config
from ...errors import ConfigCorruptedError
from ..auth import create_session_token, hash_token

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    token: str = Field(min_length=1)


@router.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    """验证 token 并签发 session"""
    if not config.auth.token_hash:
        raise ConfigCorruptedError("服务端未配置认证", detail="运行 memos init 完成初始化")
    if not config.auth.secret_key:
        raise HTTPException(500, "服务端未配置密钥（请运行 memos init）")

    if hash_token(req.token) != config.auth.token_hash:
        logger.warning("登录失败: Token 无效（来源: %s）", request.client.host if request.client else "unknown")
        raise HTTPException(401, "Token 无效")

    session_token = create_session_token(
        config.auth.token_hash,
        config.auth.secret_key,
        config.auth.session_ttl,
    )
    response = JSONResponse({"message": "登录成功", "expires_in": config.auth.session_ttl})
    response.set_cookie(
        "memos_session",
        session_token,
        httponly=True,
        samesite="lax",
        max_age=config.auth.session_ttl,
        secure=True,
    )
    return response
