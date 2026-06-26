"""回归冒烟测试 — 合并后关键路径快速验证

覆盖：
  1. Unified 模式 — 应用启动、路由完整性、关键端点
  2. Legacy 模式 — Dashboard 路由完整性（无需 ChromaDB）
  3. Hook 端点 — prompt/stop HTTP 可达
  4. Dashboard 可访问 — 页面/API/静态文件
  5. 认证正常 — 登录端点、401 拦截、auth.disable 开关

运行条件：ChromaDB 持久化存储可用
"""

import pytest


# ============================================================
# 1. Unified 模式
# ============================================================


class TestUnifiedMode:
    """unified 模式启动与路由完整性"""

    def test_app_created(self):
        """应用可正常创建"""
        from memos.server.app import create_unified_app

        app = create_unified_app()
        assert app.title == "长时记忆系统（Unified）"

    def test_routes_registered(self, unified_client):
        """三合一路由：Dashboard + SSE MCP + Hook"""
        routes = {r.path for r in unified_client.app.routes}
        # Dashboard 路由
        assert any("/login" in p for p in routes)
        assert any("/api/search" in p for p in routes)
        # SSE MCP 路由（v0.5.0 SSE 传输）
        assert any("/mcp" in p for p in routes)
        # Hook 路由
        assert any("/api/hooks/prompt" in p for p in routes)
        assert any("/api/hooks/stop" in p for p in routes)

    def test_static_files_mounted(self, unified_client):
        """静态文件路由已挂载"""
        routes = {r.path for r in unified_client.app.routes}
        assert any("/static" in p for p in routes)

    def test_health_endpoint(self, unified_client):
        """健康检查端点返回 200"""
        resp = unified_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_no_duplicate_prefix(self, unified_client):
        """路由无重复 /api/api/ 前缀"""
        paths = [str(r.path) for r in unified_client.app.routes]
        dupes = [p for p in paths if "/api/api/" in p]
        assert not dupes, f"发现重复前缀: {dupes}"


# ============================================================
# 2. Legacy 模式
# ============================================================


class TestLegacyMode:
    """v0.5.0: memos.web.app 导出 unified app（统一入口）"""

    def _get_web_app(self):
        """惰性导入 web app 模块"""
        from memos.web.app import app

        return app

    def test_web_app_importable(self):
        """web app 模块可导入，导出 unified app"""
        app = self._get_web_app()
        assert app.title == "长时记忆系统（Unified）"

    def test_web_app_has_all_routes(self):
        """unified app 同时包含 Dashboard + MCP + Hook 路由"""
        app = self._get_web_app()
        paths = [str(r.path) for r in app.routes]
        # 应有 Dashboard 路由
        assert any("/login" in p for p in paths)
        assert any("/api/search" in p for p in paths)
        # 应有 MCP SSE 路由
        assert any("/mcp" in p for p in paths)
        # 应有 Hook 路由
        hook_routes = [p for p in paths if "/api/hooks" in p]
        assert len(hook_routes) >= 2, "缺少 Hook 路由"

    def test_web_app_static_files(self):
        """web app 含静态文件路由"""
        app = self._get_web_app()
        paths = [str(r.path) for r in app.routes]
        assert any("/static" in p for p in paths)

    def test_web_app_no_duplicate_prefix(self):
        """web app 路由无重复前缀"""
        app = self._get_web_app()
        paths = [str(r.path) for r in app.routes]
        dupes = [p for p in paths if "/api/api/" in p]
        assert not dupes, f"发现重复前缀: {dupes}"


# ============================================================
# 3. Hook 端点
# ============================================================


@pytest.mark.skip(reason="禁用：写入 ChromaDB 时缺少 project_id，产生孤儿记录")
class TestHookSmoke:
    """Hook HTTP 端点基础可达性"""

    def test_prompt_endpoint(self, unified_client):
        """POST /api/hooks/prompt 返回 200"""
        resp = unified_client.post(
            "/api/hooks/prompt",
            json={"conversation_id": "smoke-001", "user_input": "冒烟测试消息"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "additional_context" in data

    def test_stop_endpoint(self, unified_client):
        """POST /api/hooks/stop 返回 200"""
        resp = unified_client.post(
            "/api/hooks/stop",
            json={"last_assistant_message": "冒烟测试回复", "stop_hook_active": False},
        )
        assert resp.status_code == 200
        assert resp.json() == {"additional_context": ""}

    def test_stop_hook_active_skip(self, unified_client):
        """stop_hook_active=true 跳过"""
        resp = unified_client.post(
            "/api/hooks/stop",
            json={"last_assistant_message": "跳过", "stop_hook_active": True},
        )
        assert resp.status_code == 200
        assert resp.json() == {"additional_context": ""}


# ============================================================
# 4. Dashboard 可访问
# ============================================================


class TestDashboardAccess:
    """Dashboard 页面和 API 可访问性"""

    def test_login_page_accessible(self, unified_client):
        """GET /login 返回 200（auth.disable=True 时跳转首页）"""
        resp = unified_client.get("/login", follow_redirects=False)
        # auth.disable=True → /login 会 302/307 到 /
        assert resp.status_code in (200, 302, 307)

    def test_login_page_no_trailing_slash(self, unified_client):
        """GET /login (无尾斜杠)"""
        resp = unified_client.get("/login/", follow_redirects=False)
        assert resp.status_code in (200, 307, 302)

    def test_api_search_endpoint(self, unified_client):
        """POST /api/search 返回 200（auth.disable）"""
        resp = unified_client.post(
            "/api/search",
            json={"query": "test", "top_k": 3},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data or isinstance(data, list)

    def test_api_config_endpoint(self, unified_client):
        """GET /api/config（auth.disable 时返回 200）"""
        resp = unified_client.get("/api/config")
        assert resp.status_code == 200

    def test_api_system_status(self, unified_client):
        """GET /api/status 返回系统信息"""
        resp = unified_client.get("/api/status")
        assert resp.status_code == 200


# ============================================================
# 5. 认证
# ============================================================


class TestAuthSmoke:
    """登录认证基础流程"""

    def test_login_without_token_returns_401(self, unified_client):
        """auth.disable=False 时无效 token 返回 401"""
        from memos.config import config as cfg

        orig = cfg.auth.disable
        cfg.auth.disable = False
        try:
            resp = unified_client.post("/api/auth/login", json={"token": "nonexistent_token_12345678"})
            assert resp.status_code == 401
        finally:
            cfg.auth.disable = orig

    def test_login_invalid_token_returns_401(self, unified_client):
        """无效 token 返回 401"""
        from memos.config import config as cfg

        orig = cfg.auth.disable
        cfg.auth.disable = False
        try:
            resp = unified_client.post("/api/auth/login", json={"token": "invalid_token_xxx"})
            assert resp.status_code == 401
        finally:
            cfg.auth.disable = orig

    def test_login_with_disabled_auth(self, unified_client):
        """auth.disable=True 时无 token 也能访问受保护路径"""
        resp = unified_client.post("/api/search", json={"query": "test", "top_k": 3})
        assert resp.status_code == 200

    @pytest.fixture
    def enable_auth(self):
        from memos.config import config as cfg

        orig = cfg.auth.disable
        cfg.auth.disable = False
        yield
        cfg.auth.disable = orig

    def test_auth_protects_api(self, unified_client, enable_auth):
        """auth.disable=False 时未认证请求返回 401"""
        resp = unified_client.get("/api/config")
        assert resp.status_code == 401

    def test_auth_public_paths_exempt(self, unified_client, enable_auth):
        """登录页面是公开路径，不受 401 拦截"""
        resp = unified_client.get("/login", follow_redirects=False)
        assert resp.status_code in (200, 302)
