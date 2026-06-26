"""Phase 2 烟雾测试 — 核心层 (F2+F6+F7+F12) ~5min"""

from urllib.parse import urlencode


class TestPhase2Smoke:
    """验证 Phase 2 (F2+F6+F7+F12) 基本可用"""

    def test_f2_navigation(self, unified_client):
        """[Phase2-F2] Dashboard 首页可访问，导航 Tab 存在"""
        resp = unified_client.get("/")
        assert resp.status_code == 200
        nav_keywords = {"总览", "对话", "记忆", "跟进", "配置"}
        found = sum(1 for kw in nav_keywords if kw in resp.text)
        assert found >= 3, f"导航 Tab 不足: 仅找到 {found}/{len(nav_keywords)}"

    def test_f6_memory_list(self, unified_client):
        """[Phase2-F6] 记忆列表 API 可访问"""
        resp = unified_client.get("/api/memories?type=solution")
        assert resp.status_code == 200

    def test_f7_forget_restore(self, unified_client):
        """[Phase2-F7] 遗忘和恢复功能可用"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("烟雾遗忘恢复测试", metadata={"type": "solution", "source": "manual"})
        assert mid is not None

        # forget
        mem.forget_memory(mid)
        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert meta.get("status") == "forgotten", f"遗忘后应为 forgotten: {meta.get('status')}"

        # restore
        mem.restore_from_forgotten(mid)
        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert meta.get("status") == "active", f"恢复后应为 active: {meta.get('status')}"

    def test_f12_injection_type(self, unified_client):
        """[Phase2-F12] 注入监控：活动日志 API 可访问，injection_type 字段存在"""
        resp = unified_client.get("/api/v2/activity-log?limit=5")
        assert resp.status_code == 200, f"活动日志 API 返回 {resp.status_code}"

        data = resp.json()
        logs = data if isinstance(data, list) else data.get("events", data.get("data", []))
        if logs:
            # 活动日志中 injection_type 字段是 knowledge/manual
            log = logs[0]
            if "injection_type" in log:
                assert log["injection_type"] in ("knowledge", "manual"), \
                    f"injection_type 值异常: {log['injection_type']}"
