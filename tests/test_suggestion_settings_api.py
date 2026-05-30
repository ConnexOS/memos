"""测试 Phase 1 — 建议设置面板 API 4 端点 (v0.4.4 增强版)

覆盖范围：获取/更新/一键恢复/阈值预览。
每个测试独立创建 mock TestClient，避免跨测试状态污染。
"""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def _make_mem():
    """创建预配置默认值的 mock ContextMemory。"""
    mem = MagicMock()
    mem.store.count.return_value = 0
    mem.store.get.return_value = {"ids": [], "documents": [], "metadatas": []}
    return mem


def _make_client(mem, auth_disabled=True):
    """创建 TestClient，用 patch 确保 lifespan 的 ContextMemory 返回 mock。"""
    from memos.config import config as memos_config

    old_disable = memos_config.auth.disable
    memos_config.auth.disable = auth_disabled
    from memos.web.app import app

    with patch("memos.web.app.ContextMemory", return_value=mem):
        with TestClient(app) as c:
            yield c
    memos_config.auth.disable = old_disable


class TestSuggestionSettingsAPI:
    """建议设置面板 API 测试。"""

    # --- GET /api/settings/suggestions ---

    def test_get_settings_returns_fields(self):
        """获取当前值 + 默认值 + 字段说明。"""
        mem = _make_mem()
        for c in _make_client(mem):
            resp = c.get("/api/settings/suggestions")
        assert resp.status_code == 200
        data = resp.json()
        fields = data.get("fields", {})
        # 检查关键字段
        assert "active_suggestion_threshold" in fields
        assert fields["active_suggestion_threshold"]["value"] == 0.65
        assert fields["active_suggestion_threshold"]["default"] == 0.65
        assert "type" in fields["active_suggestion_threshold"]
        assert fields["active_suggestion_threshold"]["type"] == "slider"

    def test_get_settings_contains_all_fields(self):
        """返回所有预期字段。"""
        mem = _make_mem()
        expected = [
            "active_suggestion_threshold",
            "context_injection_threshold",
            "suggestion_max_per_day",
            "suggestion_max_pending",
            "suggestion_display_limit",
            "suggestion_manual_daily_limit",
            "max_injection_per_round",
            "system_suggestion_enabled",
            "system_suggestion_daily_limit",
            "system_suggestion_cooldown_hours",
            "system_suggestion_triggers",
        ]
        for c in _make_client(mem):
            resp = c.get("/api/settings/suggestions")
        fields = resp.json().get("fields", {})
        for key in expected:
            assert key in fields, f"缺少字段: {key}"

    # --- PUT /api/settings/suggestions ---

    def test_partial_update_works(self):
        """部分更新生效。"""
        mem = _make_mem()
        for c in _make_client(mem):
            resp = c.put("/api/settings/suggestions", json={"active_suggestion_threshold": 0.80})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["fields"]["active_suggestion_threshold"]["value"] == 0.80

    def test_threshold_cross_validation(self):
        """active < context 时阻止保存并报 422。"""
        mem = _make_mem()
        for c in _make_client(mem):
            resp = c.put(
                "/api/settings/suggestions",
                json={
                    "active_suggestion_threshold": 0.30,
                    "context_injection_threshold": 0.60,
                },
            )
        assert resp.status_code == 422
        assert "必须 >=" in resp.text

    def test_pending_limit_cross_validation(self):
        """max_pending < max_per_day * 2 时报 422。"""
        mem = _make_mem()
        for c in _make_client(mem):
            resp = c.put(
                "/api/settings/suggestions",
                json={
                    "suggestion_max_pending": 10,
                    "suggestion_max_per_day": 10,
                },
            )
        assert resp.status_code == 422
        assert "必须 >=" in resp.text

    # --- POST /api/settings/suggestions/reset ---

    def test_reset_restores_defaults(self):
        """一键恢复默认值（active_suggestion_threshold → 0.65）。"""
        mem = _make_mem()
        for c in _make_client(mem):
            # 先修改
            c.put("/api/settings/suggestions", json={"active_suggestion_threshold": 0.90})
            # 再恢复
            resp = c.post("/api/settings/suggestions/reset")
        assert resp.status_code == 200
        assert resp.json()["fields"]["active_suggestion_threshold"]["value"] == 0.65

    # --- GET /api/suggestions/preview ---

    def test_preview_returns_counts(self):
        """预览 API 返回正确计数。"""
        mem = _make_mem()
        mem.store.count.return_value = 20
        mem.store.get.return_value = {
            "ids": ["1", "2"],
            "documents": ["a", "b"],
            "metadatas": [
                {"similarity": 0.80, "type": "fact"},
                {"similarity": 0.40, "type": "decision"},
            ],
        }
        for c in _make_client(mem):
            resp = c.get("/api/suggestions/preview?threshold=0.60")
        assert resp.status_code == 200
        data = resp.json()
        assert data["threshold"] == 0.60
        assert data["total_knowledge"] == 20

    # --- 未登录访问 ---

    def test_requires_auth_when_enabled(self):
        """未登录访问返回 401。"""
        mem = _make_mem()
        for c in _make_client(mem, auth_disabled=False):
            resp = c.get("/api/settings/suggestions")
        assert resp.status_code == 401
