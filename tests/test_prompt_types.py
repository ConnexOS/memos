"""测试 F3 - 提示词模板类型（template_type 字段 + 按类型查询 + 默认模板预设 + 迁移兼容）"""

import json
import tempfile
from pathlib import Path

import pytest

from memos.config import (
    PROMPT_TEMPLATE_TYPES,
    _DEFAULT_DAILY_REVIEW_PROMPT,
    _DEFAULT_SYSTEM_PROMPT,
    PromptManager,
    PromptTemplate,
    PromptVersion,
    _get_prompts_index,
    _get_template_file,
)


@pytest.fixture
def temp_home(monkeypatch):
    """临时 MEMOS_HOME 隔离测试"""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "etc" / "prompts").mkdir(parents=True, exist_ok=True)
        (home / "etc").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("MEMOS_HOME", str(home))
        yield home


class TestTemplateTypeEnum:
    """PROMPT_TEMPLATE_TYPES 枚举"""

    def test_three_types_defined(self):
        assert "extract" in PROMPT_TEMPLATE_TYPES
        assert "daily-review" in PROMPT_TEMPLATE_TYPES
        assert "conflict" in PROMPT_TEMPLATE_TYPES
        assert "default" in PROMPT_TEMPLATE_TYPES

    def test_default_value(self):
        t = PromptTemplate(id="test")
        assert t.template_type == "default"


class TestDefaultDailyReviewPrompt:
    """_DEFAULT_DAILY_REVIEW_PROMPT 内容"""

    def test_not_empty(self):
        assert len(_DEFAULT_DAILY_REVIEW_PROMPT) > 100

    def test_contains_markdown_sections(self):
        assert "## 今日概要" in _DEFAULT_DAILY_REVIEW_PROMPT
        assert "## 技术决策" in _DEFAULT_DAILY_REVIEW_PROMPT
        assert "## Bug 修复" in _DEFAULT_DAILY_REVIEW_PROMPT
        assert "## 待办事项" in _DEFAULT_DAILY_REVIEW_PROMPT

    def test_differs_from_extract_prompt(self):
        assert _DEFAULT_DAILY_REVIEW_PROMPT != _DEFAULT_SYSTEM_PROMPT


class TestPromptTemplateType:
    """PromptTemplate.template_type 字段行为"""

    def test_new_template_defaults_to_default_type(self):
        t = PromptTemplate(id="my-template")
        assert t.template_type == "default"

    def test_explicit_type_extract(self):
        t = PromptTemplate(id="extract-tpl", template_type="extract")
        assert t.template_type == "extract"

    def test_explicit_type_daily_review(self):
        t = PromptTemplate(id="daily-tpl", template_type="daily-review")
        assert t.template_type == "daily-review"


class TestPromptManagerGetByType:
    """PromptManager.get_by_type() 按类型查询"""

    def test_returns_extract_templates(self, temp_home):
        mgr = PromptManager()
        mgr.templates = [
            PromptTemplate(id="a", template_type="extract"),
            PromptTemplate(id="b", template_type="daily-review"),
            PromptTemplate(id="c", template_type="extract"),
            PromptTemplate(id="d", template_type="default"),
        ]
        result = mgr.get_by_type("extract")
        assert len(result) == 2
        assert {t.id for t in result} == {"a", "c"}

    def test_returns_daily_review_templates(self, temp_home):
        mgr = PromptManager()
        mgr.templates = [
            PromptTemplate(id="a", template_type="extract"),
            PromptTemplate(id="b", template_type="daily-review"),
        ]
        result = mgr.get_by_type("daily-review")
        assert len(result) == 1
        assert result[0].id == "b"

    def test_returns_empty_for_unknown_type(self, temp_home):
        mgr = PromptManager()
        mgr.templates = [PromptTemplate(id="a", template_type="extract")]
        assert mgr.get_by_type("unknown_type") == []


class TestEnsureDefaultTemplates:
    """ensure_default_template() 创建 3 种类型的默认模板"""

    def test_creates_three_defaults(self, temp_home):
        mgr = PromptManager()
        mgr.ensure_default_template()
        assert len(mgr.templates) >= 6  # v0.4.1: 6 个默认/回退模板
        ids = {t.id for t in mgr.templates}
        assert ids.issuperset({"fallback", "fallback@extract", "default@extract", "default@conflict"})

    def test_types_match_ids(self, temp_home):
        mgr = PromptManager()
        mgr.ensure_default_template()
        assert mgr.get("fallback").template_type == "default"
        assert mgr.get("default@extract").template_type == "extract"
        assert mgr.get("default@daily-review").template_type == "daily-review"

    def test_extract_default_uses_extract_prompt(self, temp_home):
        mgr = PromptManager()
        mgr.ensure_default_template()
        t = mgr.get("default@extract")
        assert t is not None
        assert "solution" in t.draft.system_prompt
        assert "decision" in t.draft.system_prompt

    def test_daily_review_default_uses_daily_prompt(self, temp_home):
        mgr = PromptManager()
        mgr.ensure_default_template()
        t = mgr.get("default@daily-review")
        assert "今日概要" in t.draft.system_prompt
        assert "技术决策" in t.draft.system_prompt

    def test_ensure_idempotent(self, temp_home):
        mgr = PromptManager()
        mgr.ensure_default_template()
        count = len(mgr.templates)
        mgr.ensure_default_template()
        assert len(mgr.templates) == count

    def test_each_template_has_one_version(self, temp_home):
        mgr = PromptManager()
        mgr.ensure_default_template()
        for t in mgr.templates:
            assert len(t.versions) == 1, f"{t.id} 应有 1 个初始版本"
            assert t.versions[0].version == "1.0.0"


class TestGetForEndpointTypeFallback:
    """get_for_endpoint 按类型 fallback"""

    def test_fallback_to_extract_default(self, temp_home):
        mgr = PromptManager()
        mgr.ensure_default_template()
        t = mgr.get_for_endpoint("unknown-ep", template_type="extract")
        assert t is not None
        assert t.id == "default@extract"

    def test_fallback_to_daily_review_default(self, temp_home):
        mgr = PromptManager()
        mgr.ensure_default_template()
        t = mgr.get_for_endpoint("unknown-ep", template_type="daily-review")
        assert t is not None
        assert t.id == "default@daily-review"

    def test_fallback_to_generic_default(self, temp_home):
        mgr = PromptManager()
        mgr.ensure_default_template()
        t = mgr.get_for_endpoint("unknown-ep", template_type="default")
        assert t is not None
        assert t.id == "fallback"

    def test_endpoint_specific_template_takes_priority(self, temp_home):
        mgr = PromptManager()
        mgr.ensure_default_template()
        custom = PromptTemplate(id="my-endpoint@extract", template_type="extract")
        mgr.upsert(custom)
        t = mgr.get_for_endpoint("my-endpoint", template_type="extract")
        assert t.id == "my-endpoint@extract"


class TestPersistenceWithType:
    """template_type 持久化到磁盘"""

    def test_save_and_load_preserves_type(self, temp_home):
        mgr = PromptManager()
        t = PromptTemplate(id="typed-tpl", template_type="daily-review", name="日报模板")
        t.draft = PromptVersion(
            version="1.0.0",
            system_prompt="测试日报提示词",
        )
        mgr.upsert(t)
        mgr.save()

        # 验证 template.json 包含 template_type
        tpl_file = _get_template_file("typed-tpl")
        with open(tpl_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data["template_type"] == "daily-review"

        # 验证 index.json 包含 template_type
        with open(_get_prompts_index(), encoding="utf-8") as f:
            idx = json.load(f)
        assert idx["templates"]["typed-tpl"]["template_type"] == "daily-review"

        # 重新加载后类型保留
        mgr2 = PromptManager.load()
        t2 = mgr2.get("typed-tpl")
        assert t2 is not None
        assert t2.template_type == "daily-review"

    def test_old_template_without_type_defaults_to_default(self, temp_home):
        """旧版模板文件（无 template_type 字段）加载后自动设为 default"""
        mgr = PromptManager()
        t = PromptTemplate(id="legacy-tpl", name="旧模板")
        t.draft = PromptVersion(version="1.0.0", system_prompt="旧提示词")
        mgr.upsert(t)
        mgr.save()

        # 手动从 template.json 删除 template_type 字段
        tpl_file = _get_template_file("legacy-tpl")
        with open(tpl_file, encoding="utf-8") as f:
            data = json.load(f)
        del data["template_type"]
        with open(tpl_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # 重新加载——自动补齐为 default
        mgr2 = PromptManager.load()
        t2 = mgr2.get("legacy-tpl")
        assert t2 is not None
        assert t2.template_type == "extract"  # 旧模板自动迁移为提炼知识类型


class TestExtractorPromptType:
    """提炼引擎按类型匹配模板"""

    def test_get_prompt_returns_extract_type_by_default(self, temp_home, monkeypatch):
        import memos.config as cfg_mod
        from memos.engine.extractor import MemoryExtractor

        mgr = PromptManager()
        mgr.ensure_default_template()
        monkeypatch.setattr(cfg_mod.config, "prompt", mgr, raising=False)

        ext = MemoryExtractor.__new__(MemoryExtractor)
        # 显式传 endpoint_name，跳过 active_endpoint 属性访问
        tpl = ext._get_prompt(endpoint_name="nonexistent", template_type="extract")
        assert tpl is not None
        assert tpl.id == "default@extract"

    def test_get_prompt_daily_review_type(self, temp_home, monkeypatch):
        import memos.config as cfg_mod
        from memos.engine.extractor import MemoryExtractor

        mgr = PromptManager()
        mgr.ensure_default_template()
        monkeypatch.setattr(cfg_mod.config, "prompt", mgr, raising=False)

        ext = MemoryExtractor.__new__(MemoryExtractor)
        tpl = ext._get_prompt(endpoint_name="nonexistent", template_type="daily-review")
        assert tpl is not None
        assert tpl.id == "default@daily-review"
