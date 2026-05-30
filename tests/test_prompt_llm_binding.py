"""Phase 2: LLM-提示词绑定自动匹配测试"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from memos.config import PromptManager, PromptTemplate, config as global_config


@pytest.fixture
def isolated_home(monkeypatch):
    """隔离 MEMOS_HOME，含两个端点的配置和提示词"""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "etc").mkdir(parents=True, exist_ok=True)
        (home / "memdb").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("MEMOS_HOME", str(home))

        # 写入 config.json
        config_data = {
            "chroma": {
                "mode": "persistent",
                "path": str(home / "memdb"),
                "collection_name": "test",
                "host": "localhost",
                "port": 8001,
                "timeout": 30,
            },
            "model": {"path": str(home / "model"), "vector_dim": 1024},
            "llm": {
                "endpoints": [
                    {"name": "deepseek-ai", "api_base": "http://ds/v1", "model": "deepseek-chat"},
                    {"name": "local-LLM", "api_base": "http://local/v1", "model": "llama3"},
                ],
                "active": "deepseek-ai",
            },
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "prompt": {},
            "auth": {},
        }
        with open(home / "etc" / "config.json", "w") as f:
            json.dump(config_data, f)

        # 为每个端点创建专属提示词模板
        mgr = PromptManager()
        for ep_name, prompt_text in [
            ("deepseek-ai", "DEEPSEEK_SPLIT_MARKER_专属提示词"),
            ("local-LLM", "LOCAL_LLM_SPLIT_MARKER_本地提示词"),
        ]:
            t = PromptTemplate(id=f"{ep_name}@extract", template_type="extract")
            t._sync_from_legacy()
            t.save_draft(system_prompt=prompt_text)
            mgr.upsert(t)
        mgr.save()

        # 重载配置
        from memos.config import MemoConfig

        cfg = MemoConfig.load()
        monkeypatch.setattr("memos.config.config", cfg)
        monkeypatch.setattr("memos.engine.extractor.config", cfg)
        yield home, cfg


class TestLLMPromptBinding:
    """FR1: LLM 端点 → 提示词自动匹配"""

    def test_active_endpoint_gets_correct_prompt(self, isolated_home):
        home, cfg = isolated_home
        prompt_text = cfg.prompt.get_active_prompt("deepseek-ai")
        assert "DEEPSEEK_SPLIT_MARKER" in prompt_text

    def test_switch_endpoint_gets_different_prompt(self, isolated_home):
        home, cfg = isolated_home
        ds_prompt = cfg.prompt.get_active_prompt("deepseek-ai")
        local_prompt = cfg.prompt.get_active_prompt("local-LLM")
        assert ds_prompt != local_prompt
        assert "LOCAL_LLM_SPLIT_MARKER" in local_prompt

    def test_fallback_when_no_template_for_endpoint(self, isolated_home):
        home, cfg = isolated_home
        prompt_text = cfg.prompt.get_active_prompt("nonexistent-endpoint")
        assert "senior technical analyst" in prompt_text

    def test_get_for_endpoint_returns_template(self, isolated_home):
        home, cfg = isolated_home
        t = cfg.prompt.get_for_endpoint("deepseek-ai")
        assert t is not None
        assert t.id == "deepseek-ai@extract"

    def test_get_for_endpoint_fallback_to_default(self, isolated_home):
        home, cfg = isolated_home
        t = cfg.prompt.get_for_endpoint("nonexistent")
        assert t is not None
        # fallback 到同类型的默认模板（extract → default@extract）
        assert t.id in ("fallback", "fallback@extract", "default@extract")


class TestDraftEffectivePrompt:
    """草稿即时生效 + 升级后切换"""

    def test_draft_changes_effective_immediately(self, isolated_home):
        home, cfg = isolated_home
        t = cfg.prompt.get_for_endpoint("deepseek-ai")
        t._sync_from_legacy()
        original = t.effective_prompt().system_prompt
        t.save_draft(system_prompt="修改后的草稿提示词")
        assert t.effective_prompt().system_prompt == "修改后的草稿提示词"
        assert t.effective_prompt().system_prompt != original

    def test_upgrade_preserves_draft_content(self, isolated_home):
        home, cfg = isolated_home
        t = cfg.prompt.get_for_endpoint("deepseek-ai")
        t._sync_from_legacy()
        t.save_draft(system_prompt="升级前的内容")
        new_ver = t.upgrade("2.0.0", "大版本升级")
        assert new_ver.system_prompt == "升级前的内容"
        # draft 保持不变
        assert t.effective_prompt().system_prompt == "升级前的内容"

    def test_build_payload_uses_effective_prompt(self, isolated_home):
        home, cfg = isolated_home
        t = cfg.prompt.get_for_endpoint("deepseek-ai")
        t._sync_from_legacy()
        t.save_draft(system_prompt="自定义系统指令")
        payload = t.build_payload("对话内容")
        # chatml 格式包含完整框架文本
        assert "自定义系统指令" in payload["messages"][0]["content"]
