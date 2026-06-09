"""测试 ProjectAwareSSEWrapper — ASGI 级 project_id 提取"""

import pytest
from starlette.responses import Response


class MockSSEApp:
    """模拟 FastMCP.sse_app() 返回的 Starlette 子应用"""

    def __init__(self):
        self.received_scope = None

    async def __call__(self, scope, receive, send):
        self.received_scope = dict(scope)  # 快照
        response = Response("ok")
        await response(scope, receive, send)


def _make_scope(path: str, root_path: str = "", query_string: bytes = b""):
    return {
        "type": "http",
        "path": path,
        "root_path": root_path,
        "headers": [],
        "method": "GET",
        "query_string": query_string,
    }


async def _call_wrapper(wrapper, scope):
    messages = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(msg):
        messages.append(msg)

    await wrapper(scope, receive, send)
    return messages, scope


class TestProjectAwareSSEWrapper:
    """测试 wrapper 的 path/root_path 改写逻辑"""

    @pytest.fixture
    def wrapper(self):
        from memos.server.sse_wrapper import ProjectAwareSSEWrapper

        return ProjectAwareSSEWrapper(MockSSEApp())

    @pytest.mark.asyncio
    async def test_without_project_id_passes_through(self, wrapper):
        """/sse（无 project_id）→ scope 不动"""
        scope = _make_scope("/sse", "/mcp")
        await _call_wrapper(wrapper, scope)
        assert wrapper.mcp_app.received_scope["path"] == "/sse"
        assert wrapper.mcp_app.received_scope["root_path"] == "/mcp"

    @pytest.mark.asyncio
    async def test_extract_project_id_from_sse(self, wrapper):
        """/a1b2c3d4/sse → 提取 pid, 只改 root_path（path 不变）"""
        scope = _make_scope("/a1b2c3d4/sse", "/mcp")
        await _call_wrapper(wrapper, scope)
        # Starlette 1.0.0: Mount 不剥离 path，仅在 root_path 传递挂载信息
        assert wrapper.mcp_app.received_scope["path"] == "/a1b2c3d4/sse"
        assert wrapper.mcp_app.received_scope["root_path"] == "/mcp/a1b2c3d4"

    @pytest.mark.asyncio
    async def test_extract_project_id_from_messages(self, wrapper):
        """/a1b2c3d4/messages/ → 提取 pid, 只改 root_path（path 不变）"""
        scope = _make_scope("/a1b2c3d4/messages/", "/mcp")
        await _call_wrapper(wrapper, scope)
        assert wrapper.mcp_app.received_scope["path"] == "/a1b2c3d4/messages/"
        assert wrapper.mcp_app.received_scope["root_path"] == "/mcp/a1b2c3d4"

    @pytest.mark.asyncio
    async def test_non_http_passthrough(self, wrapper):
        """非 HTTP 请求透传"""
        scope = {"type": "websocket", "path": "/ws"}
        messages = []

        async def receive():
            return {}

        async def send(msg):
            messages.append(msg)

        await wrapper(scope, receive, send)
        # MockSSEApp 应当被调用
        assert wrapper.mcp_app.received_scope is not None

    @pytest.mark.asyncio
    async def test_short_path_no_extraction(self, wrapper):
        """单段路径不触发提取"""
        scope = _make_scope("/sse", "")
        await _call_wrapper(wrapper, scope)
        assert wrapper.mcp_app.received_scope["path"] == "/sse"

    @pytest.mark.asyncio
    async def test_contextvar_is_set(self, wrapper):
        """project_id 被设置到 _project_id_ctx"""
        from memos.server.mcp import _project_id_ctx

        scope = _make_scope("/e5f6g7h8/sse", "/mcp")
        token = _project_id_ctx.set("old_pid")
        await _call_wrapper(wrapper, scope)
        assert _project_id_ctx.get() == "e5f6g7h8"
        _project_id_ctx.reset(token)

    @pytest.mark.asyncio
    async def test_invalid_pid_format_skipped(self, wrapper):
        """非法格式的 pid 不触发提取"""
        from memos.server.mcp import _project_id_ctx

        # "../../etc/passwd" 不通过 pid 格式校验
        scope = _make_scope("/../../../etc/passwd/sse", "/mcp")
        token = _project_id_ctx.set("original")
        await _call_wrapper(wrapper, scope)
        # ContextVar 不应被改写（保持 original）
        assert _project_id_ctx.get() == "original"
        # 子 app 收到的 path 保持不变（未被剥离）
        assert wrapper.mcp_app.received_scope["path"] == "/../../../etc/passwd/sse"
        _project_id_ctx.reset(token)

    @pytest.mark.asyncio
    async def test_long_pid_rejected(self, wrapper):
        """超过 64 字符的 pid 不触发提取"""
        from memos.server.mcp import _project_id_ctx

        long_pid = "a" * 65
        scope = _make_scope(f"/{long_pid}/sse", "/mcp")
        token = _project_id_ctx.set("original")
        await _call_wrapper(wrapper, scope)
        assert _project_id_ctx.get() == "original"
        _project_id_ctx.reset(token)

    @pytest.mark.asyncio
    async def test_contextvar_isolation_across_requests(self, wrapper):
        """并发请求的 ContextVar 互不干扰（验证异步隔离）"""
        from memos.server.mcp import _project_id_ctx

        scope_a = _make_scope("/proj_a/sse", "/mcp")
        scope_b = _make_scope("/proj_b/sse", "/mcp")

        await _call_wrapper(wrapper, scope_a)
        pid_a = _project_id_ctx.get()

        await _call_wrapper(wrapper, scope_b)
        pid_b = _project_id_ctx.get()

        assert pid_a == "proj_a"
        assert pid_b == "proj_b"

    @pytest.mark.asyncio
    async def test_concurrent_contextvar_isolation(self):
        """真正的并发请求：asyncio.gather 验证 ContextVar 互不干扰"""
        import asyncio

        from memos.server.mcp import _project_id_ctx
        from memos.server.sse_wrapper import ProjectAwareSSEWrapper

        captured = {}

        class TrackingSSEApp:
            async def __call__(self, scope, receive, send):
                captured[scope["root_path"]] = _project_id_ctx.get()
                from starlette.responses import Response

                response = Response("ok")
                await response(scope, receive, send)

        wrapper = ProjectAwareSSEWrapper(TrackingSSEApp())

        async def request(pid):
            scope = _make_scope(f"/{pid}/sse", "/mcp")
            msgs = []

            async def receive():
                return {"type": "http.disconnect"}

            async def send(msg):
                msgs.append(msg)

            await wrapper(scope, receive, send)
            return msgs, scope

        # 3 个请求并发执行
        await asyncio.gather(
            request("proj_a"),
            request("proj_b"),
            request("proj_c"),
        )

        # 每个请求内部的 ContextVar 值互不干扰
        assert captured == {
            "/mcp/proj_a": "proj_a",
            "/mcp/proj_b": "proj_b",
            "/mcp/proj_c": "proj_c",
        }

    @pytest.mark.asyncio
    async def test_project_name_from_query_string(self, wrapper):
        """?name=SemSSE → 注册到 _project_name_registry"""
        from memos.server.mcp import _project_name_registry

        scope = _make_scope("/a1b2c3d4/sse", "/mcp", query_string=b"name=SemSSE")
        await _call_wrapper(wrapper, scope)
        assert _project_name_registry.get("a1b2c3d4") == "SemSSE"
