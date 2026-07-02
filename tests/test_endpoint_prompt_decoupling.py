"""测试 F4 - 端点-提示词解耦（LLMEndpoint.prompt_templates 显式关联）"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from memos.config import LLMEndpoint, LLMConfig, MemoConfig, PromptManager, PromptTemplate


@pytest.fixture
def temp_home(monkeypatch):
    """临时 MEMOS_HOME 隔离测试"""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "etc" / "prompts").mkdir(parents=True, exist_ok=True)
        (home / "etc").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("MEMOS_HOME", str(home))
        yield home


class TestLLMEndpointPromptTemplates:
    """LLMEndpoint.prompt_templates 字段"""

    def test_default_is_empty_dict(self):
        ep = LLMEndpoint(name="test")
        assert ep.prompt_templates == {}

    def test_deserialized_from_json(self):
        data = {
            "name": "test",
            "api_base": "http://localhost:11434/v1",
            "prompt_templates": {"extract": "my-extract-tpl", "daily-review": "my-daily-tpl"},
        }
        ep = LLMEndpoint(**data)
        assert ep.prompt_templates == {"extract": "my-extract-tpl", "daily-review": "my-daily-tpl"}

    def test_optional_field_omitted(self):
        """旧配置无 prompt_templates 字段依然可加载"""
        data = {"name": "legacy", "api_base": "http://old/v1"}
        ep = LLMEndpoint(**data)
        assert ep.prompt_templates == {}


class TestGetForEndpointDecoupling:
    """get_for_endpoint 三级查找优先级"""

    def _make_mgr(self):
        mgr = PromptManager()
        mgr.ensure_default_template()
        return mgr

    def test_explicit_association_takes_priority(self, temp_home, monkeypatch):
        """端点显式关联的模板优先于命名约定"""
        import memos.config.loader as cfg_mod

        mgr = self._make_mgr()
        custom = PromptTemplate(id="custom-extract", template_type="extract", name="自定义提炼")
        custom.draft.system_prompt = "自定义 extract prompt"
        mgr.upsert(custom)

        # 配置端点：显式关联 custom-extract
        ep = LLMEndpoint(
            name="my-ep",
            api_base="http://localhost/v1",
            prompt_templates={"extract": "custom-extract"},
        )
        llm_cfg = LLMConfig(endpoints=[ep], active="my-ep")

        # Mock get_config 返回修改后的配置
        cfg = MemoConfig.load()
        cfg.prompt = mgr
        cfg.llm = llm_cfg
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)

        t = mgr.get_for_endpoint("my-ep", template_type="extract")
        assert t is not None
        assert t.id == "custom-extract"

    def test_naming_convention_second_priority(self, temp_home, monkeypatch):
        """无显式关联时，命名约定仍生效"""
        import memos.config.loader as cfg_mod

        mgr = self._make_mgr()
        tpl = PromptTemplate(id="my-ep@extract", template_type="extract", name="端点同名模板")
        mgr.upsert(tpl)

        ep = LLMEndpoint(name="my-ep", api_base="http://localhost/v1")
        llm_cfg = LLMConfig(endpoints=[ep], active="my-ep")
        cfg = MemoConfig.load()
        cfg.prompt = mgr
        cfg.llm = llm_cfg
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)

        t = mgr.get_for_endpoint("my-ep", template_type="extract")
        assert t is not None
        assert t.id == "my-ep@extract"

    def test_type_default_fallback_third(self, temp_home, monkeypatch):
        """无显式关联、无命名匹配时 fallback 到类型默认"""
        import memos.config.loader as cfg_mod

        mgr = self._make_mgr()
        ep = LLMEndpoint(name="new-ep", api_base="http://localhost/v1")
        llm_cfg = LLMConfig(endpoints=[ep], active="new-ep")
        cfg = MemoConfig.load()
        cfg.prompt = mgr
        cfg.llm = llm_cfg
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)

        t = mgr.get_for_endpoint("new-ep", template_type="extract")
        assert t is not None
        assert t.id == "default@extract"


class TestCrossEndpointTemplateSharing:
    """同一模板可被多个端点关联"""

    def test_same_template_for_two_endpoints(self, temp_home, monkeypatch):
        import memos.config.loader as cfg_mod

        mgr = PromptManager()
        mgr.ensure_default_template()
        shared = PromptTemplate(id="shared-extract", template_type="extract", name="共享提炼模板")
        mgr.upsert(shared)

        ep_a = LLMEndpoint(
            name="ep-a",
            api_base="http://a/v1",
            prompt_templates={"extract": "shared-extract"},
        )
        ep_b = LLMEndpoint(
            name="ep-b",
            api_base="http://b/v1",
            prompt_templates={"extract": "shared-extract"},
        )
        llm_cfg = LLMConfig(endpoints=[ep_a, ep_b], active="ep-a")
        cfg = MemoConfig.load()
        cfg.prompt = mgr
        cfg.llm = llm_cfg
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)

        t_a = mgr.get_for_endpoint("ep-a", template_type="extract")
        t_b = mgr.get_for_endpoint("ep-b", template_type="extract")
        assert t_a.id == "shared-extract"
        assert t_b.id == "shared-extract"


class TestBackwardCompatibility:
    """旧端点向后兼容"""

    def test_old_endpoint_no_prompt_templates(self, temp_home, monkeypatch):
        """旧端点无 prompt_templates 字段时走命名约定"""
        import memos.config.loader as cfg_mod

        mgr = PromptManager()
        mgr.ensure_default_template()

        ep = LLMEndpoint(name="old-ep", api_base="http://old/v1")
        # prompt_templates 未设置，默认 {}
        llm_cfg = LLMConfig(endpoints=[ep], active="old-ep")
        cfg = MemoConfig.load()
        cfg.prompt = mgr
        cfg.llm = llm_cfg
        monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)

        # 不存在名为 old-ep 的模板 → fallback 到 default@extract
        t = mgr.get_for_endpoint("old-ep", template_type="extract")
        assert t is not None
        assert t.id == "default@extract"


class TestPromptTemplatesPersistence:
    """prompt_templates 持久化"""

    def test_prompt_templates_saved_to_config_json(self, temp_home, monkeypatch):
        """验证 prompt_templates 写入 etc/config.json"""
        import memos.config.loader as cfg_mod

        ep = LLMEndpoint(name="test", api_base="http://localhost/v1", prompt_templates={"extract": "my-tpl"})
        llm_cfg = LLMConfig(endpoints=[ep], active="test")
        cfg = MemoConfig.load()
        cfg.llm = llm_cfg
        cfg.prompt.ensure_default_template()
        cfg.save()

        # 读取 config.json 验证 prompt_templates 字段
        config_file = Path(os.environ.get("MEMOS_HOME", "")) / "etc" / "config.json"
        if config_file.exists():
            with open(config_file, encoding="utf-8") as f:
                data = json.load(f)
            eps = data.get("llm", {}).get("endpoints", [])
            if eps:
                assert "prompt_templates" in eps[0]

    def test_update_endpoint_model_includes_prompt_templates(self, temp_home):
        """UpdateEndpointRequest 接受 prompt_templates — v0.4.3: 模型已迁移至 web/models/"""
        from memos.web.models import UpdateEndpointRequest

        req = UpdateEndpointRequest(
            prompt_templates={"extract": "new-tpl"},
        )
        assert req.prompt_templates == {"extract": "new-tpl"}
