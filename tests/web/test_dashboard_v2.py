"""F8: L5 Dashboard v2 基础集成测试。"""

import json
import os


class TestV2AllEndpoints:
    """Phase 1 E6: 全部 v2 API 端点健康检查"""

    def test_all_v2_endpoints_healthy(self, unified_client):
        """E6: 全部 12 个 v2 端点返回 200/400/404（不抛 5xx）"""
        endpoints = [
            ("GET", "/dashboard/v2/", {}),
            ("GET", "/api/v2/activity-log?page=1&page_size=5", {}),
            ("GET", "/api/v2/watchlist?page=1&page_size=5", {}),
            ("GET", "/api/v2/task/current", {}),
            ("GET", "/api/v2/review?days=7&page=1&page_size=10", {}),
            ("GET", "/api/v2/config/behavior-guide", {}),
            ("PUT", "/api/v2/config/behavior-guide", {"enabled": True, "text": "test"}),
            ("POST", "/api/v2/config/restore-default", {}),
            ("POST", "/api/v2/briefing/generate", {}),
            ("POST", "/api/v2/watchlist/fake-id/structurize", {"type": "solution"}),
            ("POST", "/api/v2/watchlist/fake-id/to-knowledge", {"type": "solution"}),
            ("POST", "/api/v2/watchlist/fake-id/ignore", {}),
            ("POST", "/api/v2/watchlist/fake-id/note", {"note": "test"}),
        ]
        for method, path, body in endpoints:
            if method == "GET":
                resp = unified_client.get(path)
            elif method == "PUT":
                resp = unified_client.put(path, json=body)
            elif method == "POST":
                resp = unified_client.post(path, json=body)
            assert resp.status_code in (200, 400, 404), f"{method} {path} 返回 {resp.status_code}"


class TestV2Routes:
    """v2 API 端点集成测试。"""

    def test_v2_behavior_guide_config_gone(self, unified_client):
        """GET /api/v2/config/behavior-guide — F13 已移除，返回 404"""
        resp = unified_client.get("/api/v2/config/behavior-guide")
        assert resp.status_code == 404

    def test_v2_task_current(self, unified_client):
        """GET /api/v2/task/current 应返回 task 或空"""
        resp = unified_client.get("/api/v2/task/current")
        assert resp.status_code == 200
        data = resp.json()
        assert "task" in data or "message" in data

    def test_v2_activity_log(self, unified_client):
        """GET /api/v2/activity-log 应返回日志"""
        resp = unified_client.get("/api/v2/activity-log?page=1&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_v2_review(self, unified_client):
        """GET /api/v2/review 应返回列表"""
        resp = unified_client.get("/api/v2/review?days=7&page=1&page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    def test_v2_restore_default_config_gone(self, unified_client):
        """POST /api/v2/config/restore-default — F13 已移除，返回 404"""
        resp = unified_client.post("/api/v2/config/restore-default")
        assert resp.status_code == 404

    def test_v2_behavior_guide_update_gone(self, unified_client):
        """PUT /api/v2/config/behavior-guide — F13 已移除，返回 404"""
        resp = unified_client.put("/api/v2/config/behavior-guide", json={"enabled": False, "text": "test"})
        assert resp.status_code == 404


class TestPhase4Dashboard:
    """Phase 4: Dashboard 数据流（E1-E5）"""

    def _inject_memory(self, unified_client):
        from memos.server.mcp import set_memory
        set_memory(unified_client.app.state.context_memory)

    def test_remember_appears_in_watchlist(self, unified_client):
        """E1: MCP remember → watchlist 面板可见"""
        self._inject_memory(unified_client)
        from memos.server.mcp import remember
        import json

        mid = json.loads(remember("集成测试待关注内容", metadata={"project_id": "e2e-test"}))["id"]
        assert mid is not None

        resp = unified_client.get("/api/v2/watchlist?page=1&page_size=20")
        assert resp.status_code == 200
        data = resp.json()
        ids = [item["id"] for item in data["items"]]
        assert mid in ids, f"remember 写入的 id {mid} 未出现在 watchlist 中"

    def test_watchlist_to_knowledge_flow(self, unified_client):
        """E2: watchlist → to-knowledge 转换流程"""
        self._inject_memory(unified_client)
        from memos.server.mcp import remember
        import json

        result = json.loads(remember("待转为知识的测试内容", metadata={"project_id": "e2e-convert"}))
        mid = result["id"]

        resp = unified_client.post(f"/api/v2/watchlist/{mid}/to-knowledge", json={
            "type": "solution",
            "preview_edit": "转为知识的测试解决方案",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "converted"

        mem = unified_client.app.state.context_memory
        original = mem.get_memory(mid)
        assert original is not None
        assert original["metadata"].get("processed") is True

    def test_watchlist_note(self, unified_client):
        """E3: watchlist → note 备注流程"""
        self._inject_memory(unified_client)
        from memos.server.mcp import remember
        import json

        result = json.loads(remember("添加备注的测试", metadata={"project_id": "e2e-note"}))
        mid = result["id"]

        resp = unified_client.post(f"/api/v2/watchlist/{mid}/note", json={"note": "这是测试备注"})
        assert resp.status_code == 200

        mem = unified_client.app.state.context_memory
        updated = mem.get_memory(mid)
        assert updated["metadata"].get("note") == "这是测试备注"

    def test_watchlist_ignore(self, unified_client):
        """E4: watchlist → ignore 忽略流程"""
        self._inject_memory(unified_client)
        from memos.server.mcp import remember
        import json

        result = json.loads(remember("将被忽略的测试", metadata={"project_id": "e2e-ignore"}))
        mid = result["id"]

        resp = unified_client.post(f"/api/v2/watchlist/{mid}/ignore")
        assert resp.status_code == 200

        mem = unified_client.app.state.context_memory
        updated = mem.get_memory(mid)
        assert updated["metadata"].get("processed") is True

    def test_behavior_guide_persist_gone(self, unified_client):
        """E5: 行为引导配置持久化 — F13 已移除，返回 404"""
        resp = unified_client.put("/api/v2/config/behavior-guide", json={
            "enabled": True,
            "text": "集成测试行为引导文本",
        })
        assert resp.status_code == 404
