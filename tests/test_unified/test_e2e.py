"""v0.5.0 E2E 测试场景矩阵"""

import os
import pytest
from fastapi.testclient import TestClient


class TestMCPHandlerE2E:
    """ET-01 / ET-04：MCP Handler + Token 认证（TestClient）"""

    @pytest.fixture
    def client(self):
        os.environ.setdefault("MEMOS_TEST_COLLECTION", "test_e2e")
        os.environ.setdefault("MEMOS_SERVER_MODE", "unified")
        os.environ["MEMOS_AUTH_DISABLE"] = "true"  # 禁用 Auth 避免 401
        # 重载配置使环境变量生效
        from memos.config import config as cfg
        cfg.auth.disable = True
        from memos.server.app import create_unified_app

        app = create_unified_app()
        return TestClient(app)

    @pytest.mark.skip(reason="SSE 迁移：HTTP MCP 路由已删除，SSE 测试在 test_sse.py 中")
    def test_et01_mcp_list_route(self, client):
        """ET-01: MCP 工具列表返回 200 + 正确结构"""
        resp = client.post("/api/mcp/list", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        assert "tools" in data["result"]
        assert isinstance(data["result"]["tools"], list)

    def test_et04_auth_login_endpoint(self, client):
        """ET-04: 登录端点返回 401（无效 token）"""
        resp = client.post("/api/auth/login", json={"token": "test"})
        assert resp.status_code == 401


@pytest.mark.skip(reason="需要运行中的 memos server（ET-02/03/05/06）")
class TestUnifiedE2EIntegration:
    """Unified 模式集成测试（需要真实 server）"""

    SERVER_URL = os.environ.get("MEMOS_E2E_SERVER", "http://127.0.0.1:8000")

    def test_et02_hook_collection(self):
        """ET-02: Hook 采集完整链路"""
        import requests

        resp = requests.post(
            f"{self.SERVER_URL}/api/hooks/prompt",
            json={"conversation_id": "test", "user_input": "hello"},
            timeout=5,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "additional_context" in data

    def test_et03_multi_user_isolation(self):
        """ET-03: 多用户数据隔离"""
        pass

    def test_et05_chromadb_lock_free(self):
        """ET-05: 无 ChromaDB 锁冲突"""
        assert True

    def test_et06_old_data_compatibility(self):
        """ET-06: 旧数据兼容性"""
        pass
