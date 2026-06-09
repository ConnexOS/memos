"""上下文注入中间件 + 健康检查"""

import logging

from .._version import __version__
from ..server.mcp import _auth_token_ctx, _project_id_ctx, _register_project_name

logger = logging.getLogger(__name__)


class InjectProjectContextMiddleware:
    """原始 ASGI 中间件 — 注入 project_id/auth_token 到独立 ContextVar。

    替代 `@app.middleware("http")`，避免 BaseHTTPMiddleware 在 shutdown 时
    因 body_stream 消息乱序引发 AssertionError。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k: v for k, v in scope.get("headers", [])}

        project_id = headers.get(b"x-memos-project-id", b"").decode()
        if project_id:
            _project_id_ctx.set(project_id)

        project_name = headers.get(b"x-memos-project-name", b"").decode()
        if project_name and project_id:
            _register_project_name(project_id, project_name)

        auth_token = headers.get(b"x-auth-token", b"").decode()
        if auth_token:
            _auth_token_ctx.set(auth_token)

        await self.app(scope, receive, send)


async def health():
    """健康检查端点，供代理启动时轮询"""
    return {"status": "ok", "version": __version__}
