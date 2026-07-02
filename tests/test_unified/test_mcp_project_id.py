"""集成测试：验证带 project_id 路径的 MCP 请求正确路由"""

import pytest


class TestMcpProjectIdRouting:
    """测试 project_id 路径的路由正确性"""

    def test_tools_list_with_project_id_path(self, unified_client):
        """带 project_id 的路径 -> tools/list 正常路由"""
        resp = unified_client.post(
            "/mcp/a1b2c3d4/messages/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        # 非 404 即路径路由正确（无 session 时返回 4xx 是 MCP 内部行为）
        assert resp.status_code != 404

    def test_different_project_ids_route(self, unified_client):
        """不同的 project_id 都能正常路由"""
        for pid in ["a1b2c3d4", "e5f6g7h8", "test001"]:
            resp = unified_client.post(
                f"/mcp/{pid}/messages/",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert resp.status_code != 404, f"pid={pid} 路由失败"

    def test_normal_url_backward_compat(self, unified_client):
        """无 project_id 的消息端向后兼容"""
        resp = unified_client.post(
            "/mcp/messages/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert resp.status_code != 404

    @pytest.mark.skip(reason="TestClient 不支持同步 SSE 流，SSE 挂载验证在 test_sse.py 中")
    def test_project_id_path_in_sse(self, unified_client):
        """GET /mcp/{pid}/sse -> SSE 路由正确（端点已挂载）"""
        with unified_client.stream("GET", "/mcp/a1b2c3d4/sse") as resp:
            assert resp.status_code != 404

    def test_path_traversal_rejected(self, unified_client):
        """路径遍历请求不应被解析为 project_id"""
        resp = unified_client.get("/mcp/../../../etc/passwd/sse")
        # pid ".." 格式校验不通过，wrapper 回退透传
        # SSE app 无匹配路由，返回 404
        assert resp.status_code == 404
