# src/memos/web/middleware/project_context.py
from fastapi import Request

from ..utils import detect_project_id


class ProjectContextMiddleware:
    """ASGI middleware: extracts project_id from query params, injects into request.state.

    Priority chain: query param → CWD fallback
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        pid = request.query_params.get("project_id") or detect_project_id()
        request.state.project_id = pid
        await self.app(scope, receive, send)
