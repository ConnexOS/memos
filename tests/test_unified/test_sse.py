# tests/test_unified/test_sse.py

"""测试 SSE MCP 挂载"""

import os

os.environ["MEMOS_AUTH_DISABLE"] = "true"

import pytest


class TestHealthEndpoint:
    """健康检查端点测试"""

    def test_api_health(self, unified_client):
        """/api/health 返回正常"""
        resp = unified_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_old_mcp_health(self, unified_client):
        """旧路径 /api/mcp/health 不再可用"""
        resp = unified_client.get("/api/mcp/health")
        # mcp_handler 路由已移除，应返回 404 或其他
        assert resp.status_code != 200


class TestSSEMount:
    """SSE 端点测试"""

    def test_mcp_messages_post_accepts(self, unified_client):
        """/mcp/messages/ POST 端点已挂载（非 404，无 session 时返回 421）"""
        resp = unified_client.post(
            "/mcp/messages/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
        )
        # 无活跃 SSE session 时返回 421 Misdirected Request（SSE 传输标准行为）
        # 关键：不是 404，说明端点已挂载
        assert resp.status_code != 404, "/mcp/messages/ 未挂载"

    @pytest.mark.skip(reason="SSE 流式端点需独立 E2E 测试（Task 7），TestClient 不支持同步流")
    def test_mcp_sse_endpoint_exists(self, unified_client):
        """/mcp/sse GET 端点存在"""
        pass


class TestMCPMessages:
    """MCP 消息端点功能测试

    注意：/mcp/messages/ 需要活跃 SSE session，TestClient 无法模拟完整 SSE 连接。
    此处仅验证端点挂载正确（非 404），完整握手测试在 E2E 验证（Task 7）中。
    """

    @pytest.mark.skip(reason="需要活跃 SSE session，无法用 TestClient 模拟")
    def test_messages_endpoint_mounted(self, unified_client):
        """/mcp/messages/ 端点已挂载（非 404）"""
        pass


class TestNoMCPHttpRoutes:
    """确认旧 HTTP MCP 路由已移除"""

    def test_no_list_route(self, unified_client):
        """/api/mcp/list 已移除"""
        resp = unified_client.post("/api/mcp/list", json={})
        assert resp.status_code != 200

    def test_no_method_call_route(self, unified_client):
        """/api/mcp/{method} 已移除"""
        resp = unified_client.post("/api/mcp/remember", json={"jsonrpc": "2.0", "id": "1", "method": "remember"})
        assert resp.status_code != 200
