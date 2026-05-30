"""Phase 1: PromptVersion / PromptTemplate 模型单元测试（适配瘦身后的数据模型）"""

import pytest
from memos.config import PromptVersion, PromptTemplate


# 辅助：创建带首版本的模板
def _new_tpl(**kw):
    t = PromptTemplate(id="test", **kw)
    t.draft = PromptVersion(version="1.0.0", system_prompt=t.system_prompt_text)
    t.upgrade("1.0.0", "初始版本")
    return t


class TestPromptVersion:
    def test_default_creation(self):
        pv = PromptVersion()
        assert pv.version == "1.0.0"
        assert "senior technical analyst" in pv.system_prompt
        assert pv.changelog == ""
        assert pv.created_at == ""

    def test_serialization_roundtrip(self):
        pv = PromptVersion(
            version="2.1.0",
            system_prompt="提取知识",
            changelog="新增反幻觉规则",
            created_at="2026-05-11T10:00:00",
        )
        data = pv.model_dump()
        restored = PromptVersion.model_validate(data)
        assert restored.version == "2.1.0"
        assert restored.system_prompt == "提取知识"
        assert restored.changelog == "新增反幻觉规则"


class TestPromptTemplate:
    def test_default_creation(self):
        t = PromptTemplate(id="test")
        assert t.id == "test"
        assert t.active_version == "1.0.0"
        assert t.versions == []
        assert t.user_template == "{conversation_text}"
        assert t.chat_style == "openai"
        assert t.parameters == {}

    def test_sync_from_legacy_creates_v1(self):
        """_sync_from_legacy 不再自动创建版本，只做 chat_style 推导"""
        t = PromptTemplate(
            id="test",
            system_prompt_text="旧提示词内容",
        )
        t._sync_from_legacy()
        assert len(t.versions) == 0  # 不再自动创建

    def test_sync_from_legacy_detects_chatml(self):
        t = PromptTemplate(
            id="test",
            prompt="<|im_start|>system\n{prompt}\n<|im_end|>",
        )
        t._sync_from_legacy()
        assert t.chat_style == "chatml"

    def test_sync_from_legacy_idempotent(self):
        t = PromptTemplate(id="test", system_prompt_text="原始")
        t._sync_from_legacy()
        t._sync_from_legacy()
        assert len(t.versions) == 0

    def test_effective_prompt_returns_draft(self):
        t = PromptTemplate(id="test")
        ep = t.effective_prompt()
        assert ep is t.draft

    def test_save_draft_updates_system_prompt_only(self):
        t = _new_tpl()
        t.save_draft(system_prompt="新提示词")
        assert t.draft.system_prompt == "新提示词"
        assert t.system_prompt_text == "新提示词"
        assert len(t.versions) == 1

    def test_save_draft_updates_common_attrs(self):
        t = _new_tpl()
        t.save_draft(parameters={"temperature": 0.9}, chat_style="chatml")
        assert t.parameters["temperature"] == 0.9
        assert t.chat_style == "chatml"

    def test_upgrade_creates_new_version(self):
        t = _new_tpl()
        t.save_draft(system_prompt="v2 提示词")
        new_ver = t.upgrade("1.1.0", "升级到v2")
        assert new_ver.system_prompt == "v2 提示词"
        assert new_ver.version == "1.1.0"
        assert new_ver.changelog == "升级到v2"
        assert len(t.versions) == 2
        assert t.active_version == "1.1.0"

    def test_rollback_creates_new_version_from_history(self):
        t = _new_tpl()
        original_prompt = t.versions[0].system_prompt
        t.save_draft(system_prompt="v2 提示词")
        t.upgrade("2.0.0", "大版本")
        result = t.rollback_to("1.0.0", "回滚测试")
        assert result is not None
        assert result.system_prompt == original_prompt
        assert result.version == "1.0.1"
        assert "回滚测试" in result.changelog
        assert len(t.versions) == 3
        assert t.active_version == "1.0.1"

    def test_rollback_nonexistent_version_returns_none(self):
        t = _new_tpl()
        result = t.rollback_to("99.99.99")
        assert result is None

    def test_get_version(self):
        t = _new_tpl()
        v = t.get_version("1.0.0")
        assert v is not None
        assert v.version == "1.0.0"
        assert t.get_version("nonexistent") is None

    def test_build_payload_uses_template_common_attrs(self):
        t = _new_tpl(chat_style="openai", parameters={"temperature": 0.3}, prompt="{system_prompt}")
        t.save_draft(system_prompt="自定义提示词")
        payload = t.build_payload("对话内容")
        assert payload["temperature"] == 0.3
        msgs = payload["messages"]
        assert msgs[0] == {"role": "system", "content": "自定义提示词"}
        assert msgs[1]["role"] == "user"
        assert "Conversation:" in msgs[1]["content"]
        assert "对话内容" in msgs[1]["content"]

    def test_build_payload_with_version_override(self):
        t = _new_tpl(prompt="{system_prompt}")
        original_sp = t.versions[0].system_prompt
        t.save_draft(system_prompt="v2")
        t.upgrade("2.0.0", "升级")
        t.save_draft(system_prompt="v3 draft")
        payload = t.build_payload("对话", version_override="1.0.0")
        msgs = payload["messages"]
        assert msgs[0] == {"role": "system", "content": original_sp}

    def test_build_payload_chatml_style(self):
        t = _new_tpl(chat_style="chatml")
        t.save_draft(system_prompt="你是一个助手")
        payload = t.build_payload("你好")
        msgs = payload["messages"]
        assert len(msgs) == 1
        assert "<|im_start|>system" in msgs[0]["content"]
        assert "你是一个助手" in msgs[0]["content"]

    def test_multiple_upgrades_accumulate_versions(self):
        t = _new_tpl()
        for i in range(5):
            t.save_draft(system_prompt=f"版本 {i + 2} 提示词")
            t.upgrade(f"1.{i + 1}.0", f"升级 {i + 2}")
        assert len(t.versions) == 6  # 1 个初始 + 5 个升级
        assert t.active_version == "1.5.0"

    def test_backward_compat_fields_preserved(self):
        t = _new_tpl(created_at=12345.0)
        t.save_draft(system_prompt="新")
        t.upgrade("1.0.1", "升级")
        assert t.created_at == 12345.0
        assert t.updated_at > 0
        assert t.id == "test"
        assert t.name == ""

    def test_delete_version_removes_non_active(self):
        t = _new_tpl()
        t.save_draft(system_prompt="v2")
        t.upgrade("2.0.0", "升级")
        assert t.delete_version("1.0.0") is True
        assert len(t.versions) == 1
        assert t.versions[0].version == "2.0.0"

    def test_delete_active_version_fails(self):
        t = _new_tpl()
        assert t.delete_version("1.0.0") is False

    def test_delete_last_version_fails(self):
        t = _new_tpl()
        assert t.delete_version("1.0.0") is False  # 唯一版本

    def test_build_payload_excludes_reserved(self):
        t = _new_tpl(parameters={"messages": "bad", "stream": True, "temperature": 0.5})
        payload = t.build_payload("hello")
        assert "messages" in payload
        assert "stream" not in payload
        assert payload["temperature"] == 0.5
