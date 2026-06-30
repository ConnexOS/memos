# src/memos/web/dependencies.py
from fastapi import HTTPException, Request

from ..config import config


async def get_project_id(request: Request) -> str:
    return request.state.project_id


async def require_auth(request: Request):
    """写操作端点鉴权，未登录返回 401。

    当 config.auth.disable=True 时跳过鉴权。
    配合 AuthASGIMiddleware 提供双重保护（中间件 + 路由依赖）。
    """
    if config.auth.disable:
        return True
    token_str = request.cookies.get("memos_session")
    if not token_str:
        # 兼容自定义 Header（API 调用场景）
        token_str = request.headers.get("X-Memos-Token", "")
    if not token_str:
        raise HTTPException(status_code=401, detail="未登录，缺少认证凭据")
    from ..auth import verify_session_token

    if not verify_session_token(token_str, config.auth.secret_key):
        # 回退至 users.json 多用户 Token 校验
        from ..auth import verify_token_against_users

        if not verify_token_against_users(token_str):
            raise HTTPException(status_code=401, detail="未登录，认证失败")
    return True
