"""Phase 6: Dashboard 设置向导测试"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_mem():
    mem = MagicMock()
    mem.list_memories.return_value = []
    mem.count_memories.return_value = 0
    mem.store.count.return_value = 0
    mem.store.get.return_value = {"ids": [], "metadatas": []}
    return mem


@pytest.fixture
def client(mock_mem, monkeypatch):
    # v0.4.3: 仪表板拆分后 verify_session_token 在各路由模块中直接导入，
    # 跨模块 patch 无法生效。使用 monkeypatch 禁用认证确保 teardown 恢复。
    from memos.config import config as memos_config

    monkeypatch.setattr(memos_config.auth, "disable", True)
    import sys

    monkeypatch.setattr(sys.modules["memos.web.app"], "ContextMemory", lambda *a, **kw: mock_mem)
    from memos.web.app import app

    with TestClient(app) as c:
        yield c


class TestWizardPresence:
    """向导模态框 HTML 存在性"""

    def test_wizard_modal_in_html(self, client):
        """Dashboard 页面包含设置向导模态框"""
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert "setupWizardModal" in html
        assert "wizard-step-0" in html
        assert "wizard-step-1" in html
        assert "wizard-step-2" in html

    def test_wizard_system_status_step(self, client):
        """步骤 0 包含系统状态检查区域"""
        resp = client.get("/")
        html = resp.text
        assert "wizard-status-cards" in html
        assert "系统状态" in html or "检查" in html

    def test_wizard_llm_config_step(self, client):
        """步骤 1 包含 LLM 配置表单"""
        resp = client.get("/")
        html = resp.text
        assert "wiz-api-base" in html
        assert "wiz-model" in html
        assert "wiz-test-conn-btn" in html

    def test_wizard_feature_intro_step(self, client):
        """步骤 2 包含功能介绍卡片"""
        resp = client.get("/")
        html = resp.text
        assert "自动记忆提炼" in html
        assert "知识检索" in html
        assert "今日回顾" in html

    def test_wizard_js_logic_present(self, client):
        """向导 JS 逻辑已包含 — v0.4.3: JS 外置到 /static/js/dashboard.js"""
        resp = client.get("/static/js/dashboard.js")
        assert resp.status_code == 200
        js_text = resp.text
        assert "isWizardCompleted" in js_text
        assert "markWizardCompleted" in js_text
        assert "memos_wizard_completed" in js_text
        assert "goWizardStep" in js_text

    def test_wizard_nav_buttons(self, client):
        """向导包含导航按钮"""
        resp = client.get("/")
        html = resp.text
        assert "wiz-skip-btn" in html
        assert "wiz-prev-btn" in html
        assert "wiz-next-btn" in html


class TestSettingsWizardEntry:
    """设置菜单中的向导入口"""

    def test_settings_rerun_button(self, client):
        """系统设置中包含"重新运行设置向导"按钮"""
        resp = client.get("/")
        html = resp.text
        assert "重新运行设置向导" in html
        assert "settings-wizard-btn" in html

    def test_settings_modal_contains_wizard_entry(self, client):
        """设置模态框 footer 中有向导重运行入口"""
        resp = client.get("/")
        html = resp.text
        # 按钮在 settingsModal 中
        assert "settings-wizard-btn" in html
