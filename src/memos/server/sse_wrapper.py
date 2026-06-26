"""ASGI wrapper — 从 MCP SSE URL 路径中提取 project_id 和 auth token"""

import asyncio
import logging
import re
import threading
import time as _time
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

from ..web.auth import verify_token_against_users
from .mcp import (
    _PID_PATTERN,
    _auth_token_ctx,
    _session_auth_store,
    _set_session_project_id,
)

logger = logging.getLogger(__name__)

KNOWN_SUB_PATHS = frozenset({"sse", "messages"})
# 用于从 SSE endpoint 事件中提取 session_id
_SESSION_ID_RE = re.compile(r"session_id=([a-zA-Z0-9_-]+)")
# pending auth 超时：客户端断开后自动清理，避免内存泄漏
_PENDING_AUTH_TTL = 60


class ProjectAwareSSEWrapper:
    """从 SSE URL 提取 project_id + token，将消息请求的 session_id 映射到 token。"""

    def __init__(self, mcp_app: ASGIApp):
        self.mcp_app = mcp_app
        self._pending_auth: dict[str, str] = {}  # scope_id → token（SSE 连接建立后待映射）
        self._pending_timestamps: dict[str, float] = {}  # scope_id → time.monotonic()
        # 单 worker ASGI 场景锁保护 check-then-pop 原子性
        self._pending_lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.mcp_app(scope, receive, send)
            return

        # 惰性清理过期 pending auth（客户端断开后残留的 scope_key）
        now = _time.monotonic()
        stale = [k for k, ts in self._pending_timestamps.items() if now - ts > _PENDING_AUTH_TTL]
        if stale:
            with self._pending_lock:
                for k in stale:
                    self._pending_auth.pop(k, None)
                    self._pending_timestamps.pop(k, None)

        path = scope.get("path", "")
        root_path = scope.get("root_path", "")

        if root_path and path.startswith(root_path):
            effective_path = path[len(root_path) :]
        else:
            effective_path = path

        slash_idx = effective_path.find("/", 1)
        if slash_idx != -1:
            first_segment = effective_path[1:slash_idx]
        else:
            first_segment = effective_path[1:]

        if slash_idx != -1 and first_segment not in KNOWN_SUB_PATHS:
            pid = first_segment
            if not _PID_PATTERN.match(pid):
                logger.warning("ProjectAwareSSEWrapper: 非法 project_id 格式, 跳过: %s", pid)
                await self.mcp_app(scope, receive, send)
                return

            qs = scope.get("query_string", b"")
            params = parse_qs(qs.decode("utf-8", errors="replace")) if qs else {}

            project_name = (params.get("name") or [None])[0]
            _set_session_project_id(pid, project_name)

            # 提取 token：优先 X-Auth-Token header（MCP SSE 也支持），回落 URL query param
            headers = {k.decode("utf-8", errors="replace").lower(): v.decode("utf-8", errors="replace")
                       for k, v in scope.get("headers", [])}
            token = headers.get("x-auth-token") or (params.get("token") or [None])[0]
            if token:
                user = verify_token_against_users(token)
                if user:
                    scope_key = str(id(scope))
                    with self._pending_lock:
                        self._pending_auth[scope_key] = token
                        self._pending_timestamps[scope_key] = _time.monotonic()
                    logger.info("SSE token 验证通过: creator_id=%s", user["creator_id"])

            # 对于 messages 请求，按 session_id 查 token
            if slash_idx != -1 and effective_path[slash_idx:].startswith("/messages"):
                session_id = (params.get("session_id") or [None])[0]
                if session_id:
                    stored_token = _session_auth_store.get(session_id)
                    if stored_token:
                        _auth_token_ctx.set(stored_token)

            scope["root_path"] = root_path.rstrip("/") + "/" + pid

            # 包装 send 以拦截 SSE endpoint 事件，建立 session_id → token 映射
            scope_key = str(id(scope))
            with self._pending_lock:
                pending_token = self._pending_auth.pop(scope_key, None)
                self._pending_timestamps.pop(scope_key, None)
            if pending_token:
                # 在 SSE handler task context 中设置 token，子任务（工具调用）继承此值
                _auth_token_ctx.set(pending_token)

                _resp_started = False

                async def send_wrapper(message):
                    nonlocal _resp_started
                    # 防重复响应头：MCP 子应用异常时 Starlette 错误中间件
                    # 会尝试发送新的 http.response.start，与已发送的 SSE 冲突
                    if message["type"] == "http.response.start":
                        if _resp_started:
                            return
                        _resp_started = True
                    if message["type"] == "http.response.body":
                        body = message.get("body", b"")
                        text = body.decode("utf-8", errors="replace")
                        match = _SESSION_ID_RE.search(text)
                        if match and "event: endpoint" in text:
                            session_id = match.group(1)
                            _session_auth_store.put(session_id, pending_token)
                            logger.debug("SessionAuthStore: sid=%s 已映射", session_id[:8])
                    await send(message)

                try:
                    await self.mcp_app(scope, receive, send_wrapper)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("ProjectAwareSSEWrapper: SSE 子应用异常", exc_info=True)
                return

        try:
            await self.mcp_app(scope, receive, send)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("ProjectAwareSSEWrapper: SSE 子应用异常", exc_info=True)
