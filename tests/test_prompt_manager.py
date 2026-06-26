"""Phase 1: PromptManager CRUD + 端点查询 + 目录存储 + 迁移 单元测试"""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from memos.config import (
    PromptManager,
    PromptTemplate,
    PromptVersion,
    _DEFAULT_SYSTEM_PROMPT,
    _get_prompts_file,
    _get_prompts_index,
    _get_template_file,
    _get_version_file,
    get_memos_home,
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


@pytest.fixture
def empty_manager(temp_home):
    """空 PromptManager（无模板）"""
    mgr = PromptManager()
    return mgr


@pytest.fixture
def manager_with_default(temp_home):
    """含 default 模板的 PromptManager"""
    t = PromptTemplate(
        id="default",
        name="默认",
    )
    t._sync_from_legacy()
    mgr = PromptManager(templates=[t])
    return mgr


class TestPromptManagerCRUD:
    """验证 PromptManager 核心 CRUD 操作"""

    def test_get_existing(self, manager_with_default):
        t = manager_with_default.get("default")
        assert t is not None
        assert t.id == "default"

    def test_get_nonexistent(self, manager_with_default):
        assert manager_with_default.get("不存在") is None

    def test_upsert_new(self, manager_with_default):
        t = PromptTemplate(id="new_one")
        t._sync_from_legacy()
        manager_with_default.upsert(t)
        assert manager_with_default.get("new_one") is not None
        assert t.created_at > 0

    def test_upsert_existing(self, manager_with_default):
        t = manager_with_default.get("default")
        t.name = "改名了"
        t._sync_from_legacy()
        manager_with_default.upsert(t)
        assert manager_with_default.get("default").name == "改名了"

    def test_delete_other(self, manager_with_default):
        t = PromptTemplate(id="可删除")
        t._sync_from_legacy()
        manager_with_default.upsert(t)
        assert manager_with_default.delete("可删除") is True
        assert manager_with_default.get("可删除") is None

    def test_delete_default_not_allowed(self, manager_with_default):
        assert manager_with_default.delete("default") is False

    def test_templates_iterable(self, manager_with_default):
        ids = [t.id for t in manager_with_default.templates]
        assert "default" in ids


class TestPromptManagerNewAPI:
    """验证 PromptManager 新增端点查询方法"""

    def test_get_for_endpoint_found(self, temp_home):
        t = PromptTemplate(id="deepseek-ai@extract", template_type="extract")
        t._sync_from_legacy()
        mgr = PromptManager(templates=[t])
        found = mgr.get_for_endpoint("deepseek-ai")
        assert found is not None
        assert found.id == "deepseek-ai@extract"

    def test_get_for_endpoint_not_found(self, temp_home):
        mgr = PromptManager()
        assert mgr.get_for_endpoint("nonexistent") is None

    def test_get_active_prompt_returns_text(self, temp_home):
        t = PromptTemplate(id="deepseek-ai@extract", template_type="extract")
        t._sync_from_legacy()
        t.save_draft(system_prompt="自定义DeepSeek提示词")
        mgr = PromptManager(templates=[t])
        prompt_text = mgr.get_active_prompt("deepseek-ai")
        assert prompt_text == "自定义DeepSeek提示词"

    def test_get_active_prompt_fallback(self, temp_home):
        mgr = PromptManager()
        prompt_text = mgr.get_active_prompt("nonexistent")
        assert "senior technical analyst" in prompt_text  # fallback 到内置默认

    def test_ensure_default_template_creates_if_missing(self, empty_manager):
        empty_manager.ensure_default_template()
        t = empty_manager.get("fallback")
        assert t is not None
        t._sync_from_legacy()
        assert len(t.versions) == 1

    def test_ensure_default_template_idempotent(self, manager_with_default):
        # ensure_default_template 会补齐缺失的 7 个默认/回退模板（含 default@briefing）
        manager_with_default.ensure_default_template()
        count_after_first = len(manager_with_default.templates)
        assert (
            count_after_first == 8
        )  # original "default" → "fallback" + 7 created (fallback@extract, fallback@daily-review, default@extract, default@daily-review, default@briefing, default@conflict, default@todo-extract)
        # 再次调用不变
        manager_with_default.ensure_default_template()
        assert len(manager_with_default.templates) == count_after_first


class TestPromptManagerStorage:
    """验证目录结构持久化和重新加载"""

    def test_save_and_reload(self, temp_home):
        t = PromptTemplate(id="deepseek-ai@extract", template_type="extract")
        t._sync_from_legacy()
        t.save_draft(system_prompt="DeepSeek专用提示词", parameters={"temperature": 0.1})
        t.upgrade("1.0.0", "初始版本")
        t.save_draft(system_prompt="v2提示词")
        t.upgrade("1.1.0", "优化了输出格式")

        mgr = PromptManager(templates=[t])
        mgr.save()

        # 验证目录结构
        assert _get_prompts_index().exists()
        assert _get_template_file("deepseek-ai@extract").exists()
        assert _get_version_file("deepseek-ai@extract", "1.0.0").exists()
        assert _get_version_file("deepseek-ai@extract", "1.1.0").exists()

        # 重新加载
        mgr2 = PromptManager.load()
        t2 = mgr2.get("deepseek-ai@extract")
        assert t2 is not None
        assert t2.id == "deepseek-ai@extract"
        assert t2.active_version == "1.1.0"
        assert len(t2.versions) == 2
        assert t2.draft.system_prompt == "v2提示词"

        # 版本内容一致
        v1 = t2.get_version("1.0.0")
        assert v1.system_prompt == "DeepSeek专用提示词"

    def test_save_multiple_templates(self, temp_home):
        mgr = PromptManager()
        for ep in ["deepseek-ai", "local-LLM", "modelscope"]:
            t = PromptTemplate(id=ep)
            t._sync_from_legacy()
            t.save_draft(system_prompt=f"{ep}提示词")
            mgr.upsert(t)
        mgr.save()

        # 验证 index.json
        with open(_get_prompts_index(), encoding="utf-8") as f:
            idx = json.load(f)
        assert len(idx["templates"]) == 3
        assert "deepseek-ai" in idx["templates"]
        # 未调用 upgrade() 时没有已发布版本，version_count 为 0
        assert idx["templates"]["deepseek-ai"]["version_count"] == 0

        # 重新加载（ensure_default_template 会自动补齐默认/回退模板）
        mgr2 = PromptManager.load()
        assert len(mgr2.templates) >= 6  # 3 个用户模板 + N 个默认/回退模板（含 v0.4.1 新增）
        for ep in ["deepseek-ai", "local-LLM", "modelscope"]:
            t = mgr2.get(ep)
            assert t is not None
            assert t.id == ep

    def test_template_dir_removed_when_deleted(self, temp_home):
        t = PromptTemplate(id="to_delete")
        t._sync_from_legacy()
        mgr = PromptManager(templates=[t])
        mgr.save()
        assert _get_template_file("to_delete").exists()
        mgr.delete("to_delete")
        mgr.save()
        # 目录仍存在但模板已从索引移除
        mgr2 = PromptManager.load()
        assert mgr2.get("to_delete") is None


class TestPromptManagerMigration:
    """验证旧 prompts.json 迁移"""

    def _write_legacy_prompts_json(self, temp_home, data=None):
        if data is None:
            data = {
                "active_id": "default",
                "templates": [
                    {
                        "id": "default",
                        "name": "系统提示词 (默认)",
                        "description": "默认模板",
                        "prompt": "<|im_start|>system\n{system_prompt}\n<|im_end|>",
                        "system_prompt_text": "旧的提示词内容",
                        "parameters": {"temperature": 0.1},
                        "created_at": 0.0,
                        "updated_at": 0.0,
                        "is_active": True,  # 旧字段，迁移时忽略
                    }
                ],
            }
        old_file = temp_home / "etc" / "prompts.json"
        with open(old_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return old_file

    def _write_config_json(self, temp_home, data=None):
        if data is None:
            data = {
                "llm": {
                    "endpoints": [
                        {"name": "deepseek-ai", "api_base": "http://x/v1"},
                        {"name": "local-LLM", "api_base": "http://y/v1"},
                    ],
                    "active": "deepseek-ai",
                }
            }
        cfg_file = temp_home / "etc" / "config.json"
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def test_migration_creates_directory_structure(self, temp_home):
        self._write_legacy_prompts_json(temp_home)
        self._write_config_json(temp_home)
        mgr = PromptManager.load()
        # 验证迁移结果（"default" 模板已重命名为 "fallback"）
        t = mgr.get("fallback")
        assert t is not None
        t._sync_from_legacy()
        assert len(t.versions) == 1
        assert t.id == "fallback"
        # index.json 已创建，但 default 开头的内部模板不持久化到磁盘目录
        assert _get_prompts_index().exists()
        # 旧文件已备份
        bak = temp_home / "etc" / "prompts.json.bak"
        assert bak.exists()
        # 旧文件已移除
        assert not (temp_home / "etc" / "prompts.json").exists()

    def test_migration_idempotent(self, temp_home):
        self._write_legacy_prompts_json(temp_home)
        self._write_config_json(temp_home)
        mgr1 = PromptManager.load()
        # 第二次 load 不会重复迁移
        mgr2 = PromptManager.load()
        assert len(mgr1.templates) == len(mgr2.templates)

    def test_fresh_init_no_migration(self, temp_home):
        """全新环境（无旧文件）正常初始化"""
        mgr = PromptManager.load()
        t = mgr.get("fallback")
        assert t is not None
        assert t.id == "fallback"
        t._sync_from_legacy()
        assert len(t.versions) == 1

    def test_migration_multiple_templates(self, temp_home):
        old_data = {
            "active_id": "tpl_a",
            "templates": [
                {
                    "id": "tpl_a",
                    "name": "A",
                    "system_prompt_text": "提示词A",
                    "is_active": True,
                    "prompt": "",
                    "parameters": {},
                    "created_at": 0,
                    "updated_at": 0,
                    "description": "",
                },
                {
                    "id": "tpl_b",
                    "name": "B",
                    "system_prompt_text": "提示词B",
                    "is_active": False,
                    "prompt": "",
                    "parameters": {},
                    "created_at": 0,
                    "updated_at": 0,
                    "description": "",
                },
            ],
        }
        self._write_legacy_prompts_json(temp_home, old_data)
        self._write_config_json(temp_home)
        mgr = PromptManager.load()
        assert len(mgr.templates) >= 2
        a = mgr.get("tpl_a")
        assert a is not None
        a._sync_from_legacy()
        assert a.versions[0].system_prompt == "提示词A"
        b = mgr.get("tpl_b")
        assert b is not None
        b._sync_from_legacy()
        assert b.versions[0].system_prompt == "提示词B"
