"""Phase 3 烟雾测试 — 增强层 (F8+F9+F10+F11) ~4min"""


class TestPhase3Smoke:
    """验证 Phase 3 (F8+F9+F10+F11) 基本可用"""

    def test_f9_sse_endpoint(self, unified_client):
        """[Phase3-F9] SSE 端点路由已注册"""
        app = unified_client.app
        found = False
        for route in app.routes:
            if hasattr(route, "path") and "/api/v2/events" in route.path:
                found = True
                break
        assert found, "SSE 端点路由未注册在 app 中"

    def test_f8_briefing_fallback(self, unified_client):
        """[Phase3-F8] 简报兜底不抛异常"""
        from memos.features.briefing import build_fallback_briefing
        # 必须使用关键字参数 memory_instance=
        briefing = build_fallback_briefing(
            memory_instance=unified_client.app.state.context_memory
        )
        assert briefing is not None
        assert "summary" in briefing
        assert "quality" in briefing

    def test_f10_feedback(self, unified_client):
        """[Phase3-F10] 反馈反哺公式可用"""
        mem = unified_client.app.state.context_memory
        boost = mem._compute_reuse_boost({"reuse_count": 1, "useful_feedback_count": 1})
        assert boost > 0, f"反馈加分应为正: {boost}"

    def test_f11_prompt_manager(self, unified_client):
        """[Phase3-F11] 提示词管理端点可达"""
        resp = unified_client.get("/api/prompts")
        # 认证未开时返回 200；已开且未登录返回 401
        assert resp.status_code in (200, 401), f"Unexpected status: {resp.status_code}"
