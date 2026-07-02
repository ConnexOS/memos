"""Phase 1 F5: 提示词模板优化 — 单元测试
覆盖：新版 default@extract 含四种类型指引、fallback 链查找正确、
旧目录迁移无损、用户自定义不被覆盖、conflict 模板预置
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from memos.config import (
    PROMPT_TEMPLATE_TYPES,
    PromptManager,
    PromptTemplate,
    PromptVersion,
    _DEFAULT_CONFLICT_PROMPT,
    _DEFAULT_SYSTEM_PROMPT,
    _get_prompts_index,
    _get_template_dir,
    _get_template_file,
    get_memos_home,
)
from memos.config.prompts import _get_default_extract_prompt


@pytest.fixture
def temp_home(monkeypatch):
    """临时 MEMOS_HOME 隔离测试"""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "etc" / "prompts").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("MEMOS_HOME", str(home))
        yield home


@pytest.fixture
def fresh_manager(temp_home):
    """全新 PromptManager（初始化后调用 ensure_default_template）"""
    mgr = PromptManager()
    mgr.ensure_default_template()
    return mgr


class TestNewExtractTemplate:
    """验证新版 default@extract 模板内容"""

    def test_new_extract_contains_four_types(self, fresh_manager):
        """新版 default@extract 含 solution/decision/lesson/process 四种类型指引"""
        t = fresh_manager.get("default@extract")
        assert t is not None, "default@extract 模板应存在"
        prompt = t.draft.system_prompt
        assert "solution" in prompt
        assert "decision" in prompt
        assert "lesson" in prompt
        assert "process" in prompt
        assert "quality_score" in prompt

    def test_new_extract_has_type_classification_criteria(self, fresh_manager):
        """新版 default@extract 包含类型枚举和 quality_score"""
        t = fresh_manager.get("default@extract")
        prompt = t.draft.system_prompt
        assert 'solution" | "decision" | "lesson" | "process' in prompt
        assert "quality_score" in prompt

    def test_new_extract_differs_from_old_default(self, fresh_manager):
        """新版系统提示词与旧版内容不同"""
        t = fresh_manager.get("default@extract")
        assert t.draft.system_prompt.strip() != _DEFAULT_SYSTEM_PROMPT.strip()

    def test_old_extract_becomes_fallback(self, fresh_manager):
        """旧版系统提示词保留为 fallback@extract"""
        t = fresh_manager.get("fallback@extract")
        assert t is not None, "fallback@extract 应存在"
        assert t.draft.system_prompt.strip() == _DEFAULT_SYSTEM_PROMPT.strip()


class TestConflictTemplate:
    """验证 conflict 模板预置"""

    def test_conflict_template_exists(self, fresh_manager):
        t = fresh_manager.get("default@conflict")
        assert t is not None, "default@conflict 模板应存在"
        assert t.template_type == "conflict"

    def test_conflict_prompt_is_binary_judgment(self, fresh_manager):
        t = fresh_manager.get("default@conflict")
        prompt = t.draft.system_prompt
        assert "has_conflict" in prompt
        assert "conflict_with" in prompt

    def test_conflict_in_prompt_template_types(self):
        assert "conflict" in PROMPT_TEMPLATE_TYPES


class TestFallbackChain:
    """验证 fallback 链查找正确"""

    def test_get_for_endpoint_finds_endpoint_specific(self, temp_home):
        """端点专属模板优先返回"""
        t = PromptTemplate(id="my-endpoint@extract", template_type="extract")
        t._sync_from_legacy()
        t.save_draft(system_prompt="端点专属提示词")
        mgr = PromptManager(templates=[t])
        found = mgr.get_for_endpoint("my-endpoint", "extract")
        assert found is not None
        assert found.id == "my-endpoint@extract"

    def test_get_for_endpoint_falls_back_to_fallback_extract(self, temp_home):
        """无端点专属模板 → default@extract → fallback@extract"""
        # 仅创建 fallback@extract，不创建 default@extract
        t = PromptTemplate(id="fallback@extract", template_type="extract")
        t._sync_from_legacy()
        t.save_draft(system_prompt="fallback extract 提示词")
        mgr = PromptManager(templates=[t])
        # 先尝试 default@extract → 不存在 → fallback@extract
        found = mgr.get_for_endpoint("unknown-endpoint", "extract")
        assert found is not None
        assert found.id == "fallback@extract"

    def test_get_for_endpoint_prefers_default_over_fallback(self, temp_home):
        """当两者都存在时，default@extract 优先于 fallback@extract"""
        t_default = PromptTemplate(id="default@extract", template_type="extract")
        t_default._sync_from_legacy()
        t_default.save_draft(system_prompt="新版默认提示词")
        t_fallback = PromptTemplate(id="fallback@extract", template_type="extract")
        t_fallback._sync_from_legacy()
        t_fallback.save_draft(system_prompt="旧版回退提示词")
        mgr = PromptManager(templates=[t_default, t_fallback])
        found = mgr.get_for_endpoint("unknown-endpoint", "extract")
        assert found is not None
        assert found.id == "default@extract"

    def test_get_for_endpoint_ultimate_fallback(self, temp_home):
        """所有查找失败 → fallback"""
        t = PromptTemplate(id="fallback", template_type="default")
        t._sync_from_legacy()
        mgr = PromptManager(templates=[t])
        found = mgr.get_for_endpoint("unknown-endpoint", "extract")
        assert found is not None
        assert found.id == "fallback"

    def test_get_for_endpoint_daily_review_fallback(self, temp_home):
        t = PromptTemplate(id="fallback@daily-review", template_type="daily-review")
        t._sync_from_legacy()
        mgr = PromptManager(templates=[t])
        found = mgr.get_for_endpoint("unknown-endpoint", "daily-review")
        assert found is not None
        assert found.id == "fallback@daily-review"


class TestMigration:
    """验证旧目录迁移无损"""

    def _create_old_template_on_disk(self, temp_home, old_id, sys_prompt=None):
        """模拟旧命名目录存在的情况"""
        tpl_dir = temp_home / "etc" / "prompts" / old_id
        tpl_dir.mkdir(parents=True, exist_ok=True)
        (tpl_dir / "versions").mkdir(exist_ok=True)
        if sys_prompt is None:
            sys_prompt = _DEFAULT_SYSTEM_PROMPT
        template_data = {
            "id": old_id,
            "name": f"旧模板 {old_id}",
            "description": "旧版模板",
            "template_type": "extract",
            "user_template": "{conversation_text}",
            "chat_style": "chatml",
            "parameters": {},
            "active_version": "1.0.0",
            "created_at": 0.0,
            "updated_at": 0.0,
            "draft": {
                "version": "1.0.0",
                "system_prompt": sys_prompt,
                "changelog": "",
                "created_at": "2026-01-01T00:00:00",
            },
        }
        with open(tpl_dir / "template.json", "w", encoding="utf-8") as f:
            json.dump(template_data, f, indent=2, ensure_ascii=False)
        return tpl_dir

    def test_rename_old_default_extract_to_fallback(self, temp_home):
        """旧 default-extract 目录迁移到 fallback@extract"""
        self._create_old_template_on_disk(temp_home, "default-extract")
        # 创建内存中的旧模板
        t = PromptTemplate(id="default-extract", name="旧知识提炼", template_type="extract")
        t._sync_from_legacy()
        mgr = PromptManager(templates=[t])
        mgr.ensure_default_template()
        # 旧 id 不再存在
        assert mgr.get("default-extract") is None
        # 新 id 存在
        new_t = mgr.get("fallback@extract")
        assert new_t is not None
        # 目录已迁移
        assert not (temp_home / "etc" / "prompts" / "default-extract").exists()
        # 新目录存在（如果磁盘上有的话，内存迁移会触发磁盘迁移）

    def test_rename_old_default_to_fallback(self, temp_home):
        """旧 default 迁移到 fallback"""
        self._create_old_template_on_disk(temp_home, "default", sys_prompt="旧通用提示词")
        t = PromptTemplate(id="default", name="通用默认", template_type="default")
        t._sync_from_legacy()
        mgr = PromptManager(templates=[t])
        mgr.ensure_default_template()
        assert mgr.get("default") is None
        assert mgr.get("fallback") is not None

    def test_migration_no_overwrite_existing_new(self, temp_home):
        """如果新命名的模板已存在，不覆盖"""
        # 预置 fallback@extract
        existing = PromptTemplate(id="fallback@extract", template_type="extract")
        existing._sync_from_legacy()
        existing.save_draft(system_prompt="已存在的 fallback@extract 提示词")
        # 同时有旧 default-extract
        old = PromptTemplate(id="default-extract", template_type="extract")
        old._sync_from_legacy()
        old.save_draft(system_prompt="旧 default-extract 提示词")
        mgr = PromptManager(templates=[existing, old])
        mgr.ensure_default_template()
        # fallback@extract 保留原有内容不被覆盖
        t = mgr.get("fallback@extract")
        assert t.draft.system_prompt == "已存在的 fallback@extract 提示词"


class TestUserCustomizationProtection:
    """验证用户自定义不被覆盖"""

    def test_user_customized_not_overwritten(self, temp_home):
        """用户自定义过 default@extract，升级时不覆盖"""
        t = PromptTemplate(id="default@extract", template_type="extract")
        t._sync_from_legacy()
        t.save_draft(system_prompt="用户自定义的专属提炼提示词")
        mgr = PromptManager(templates=[t])
        mgr.ensure_default_template()
        updated = mgr.get("default@extract")
        assert updated.draft.system_prompt == "用户自定义的专属提炼提示词"

    def test_uncustomized_gets_upgraded(self, temp_home):
        """未自定义的 default@extract 自动升级为新版"""
        t = PromptTemplate(id="default@extract", template_type="extract")
        t._sync_from_legacy()
        # system_prompt 仍然是旧默认值
        t.save_draft(system_prompt=_DEFAULT_SYSTEM_PROMPT)
        mgr = PromptManager(templates=[t])
        mgr.ensure_default_template()
        updated = mgr.get("default@extract")
        assert updated.draft.system_prompt.strip() == _get_default_extract_prompt().strip()

    def test_is_user_customized_detects_modification(self):
        """_is_user_customized 正确检测修改"""
        t = PromptTemplate(id="test", template_type="extract")
        t._sync_from_legacy()
        t.save_draft(system_prompt=_DEFAULT_SYSTEM_PROMPT)
        mgr = PromptManager(templates=[t])
        assert mgr._is_user_customized(t) is False
        t.save_draft(system_prompt="修改后的提示词")
        assert mgr._is_user_customized(t) is True


class TestEnsureAllDefaultsExist:
    """验证所有必要的模板都会被创建"""

    def test_all_required_templates_created(self, fresh_manager):
        required = [
            "fallback",
            "fallback@extract",
            "fallback@daily-review",
            "default@extract",
            "default@daily-review",
            "default@conflict",
        ]
        for tid in required:
            assert fresh_manager.get(tid) is not None, f"模板 {tid} 应存在"

    def test_ensure_default_template_idempotent(self, fresh_manager):
        count = len(fresh_manager.templates)
        fresh_manager.ensure_default_template()
        assert len(fresh_manager.templates) == count

    def test_fallback_template_has_old_prompt(self, fresh_manager):
        t = fresh_manager.get("fallback")
        assert t is not None
        assert t.draft.system_prompt.strip() == _DEFAULT_SYSTEM_PROMPT.strip()

    def test_fallback_extract_has_old_prompt(self, fresh_manager):
        t = fresh_manager.get("fallback@extract")
        assert t is not None
        assert t.draft.system_prompt.strip() == _DEFAULT_SYSTEM_PROMPT.strip()
