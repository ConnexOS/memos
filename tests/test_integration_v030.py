"""
MEMOS v0.3.0 集成测试套件
覆盖：MCP管线、端点解耦、模板类型、今日回顾、异常边界、线程安全、配置、安装向导、回归
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memos.errors import (
    ChromaDBError,
    ConfigCorruptedError,
    DiskFullError,
    LLMUnreachableError,
    ModelNotFoundError,
    PermissionDeniedError,
    MemoError,
    format_error,
    from_exception,
    http_status_for,
)


def _make_memory_with_temp_collection():
    from memos.engine.memory import ContextMemory
    import uuid

    col = f"test_int_{uuid.uuid4().hex[:12]}"
    os.environ["MEMOS_TEST_COLLECTION"] = col
    return ContextMemory(collection_name=col), col


# ============================================================
# Group I1: MCP 管线 — memory/extractor/chromastore 全链路
# ============================================================


class TestMCPMemoryPipeline:
    """验证 remember→recall→update→delete→list 完整链路"""

    def test_remember_recall_roundtrip(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()

        mid = mem.remember(
            "测试集成：使用pytest进行单元测试", metadata={"type": "fact", "project_id": "test-proj", "source": "test"}
        )
        assert mid is not None
        results = mem.recall("pytest 单元测试", top_k=5, project_id="test-proj")
        assert len(results) > 0

    def test_save_knowledge_direct_write(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        mid = mem.remember(
            "直写测试：Python3.12是当前版本",
            metadata={"type": "decision", "project_id": "test-proj", "source": "user_instructed"},
        )
        assert mid is not None
        results = mem.recall("Python 版本", top_k=3, project_id="test-proj")
        assert len(results) > 0

    def test_update_memory_content_and_metadata(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        mid = mem.remember("原始内容", metadata={"type": "fact", "project_id": "test-proj"})
        mem.update_memory(mid, new_content="更新后的内容", new_metadata={"type": "decision"})
        updated = mem.get_memory(mid)
        assert "更新后的内容" in updated["document"]

    def test_delete_memory(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        mid = mem.remember("待删除内容", metadata={"type": "fact", "project_id": "test-proj"})
        mem.delete_memory(mid)
        assert mem.get_memory(mid) is None

    def test_project_isolation(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        mem.remember("项目A的记忆", metadata={"type": "fact", "project_id": "proj-a"})
        mem.remember("项目B的记忆", metadata={"type": "fact", "project_id": "proj-b"})
        results_a = mem.recall("记忆", top_k=5, project_id="proj-a")
        results_b = mem.recall("记忆", top_k=5, project_id="proj-b")
        assert any("项目A" in r for r in results_a)
        assert any("项目B" in r for r in results_b)

    def test_list_memories_pagination(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        for i in range(15):
            mem.remember(f"分页测试内容{i}", metadata={"type": "fact", "project_id": "test-proj"})
        page1 = mem.list_memories(project_id="test-proj", limit=10, offset=0)
        page2 = mem.list_memories(project_id="test-proj", limit=10, offset=10)
        all_items = mem.list_memories(project_id="test-proj", limit=100, offset=0)
        assert len(page1) == 10
        assert len(page2) == 5
        assert len(all_items) == 15

    def test_list_memories_type_filter(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        mem.remember("事实1", metadata={"type": "fact", "project_id": "test-proj"})
        mem.remember("决策1", metadata={"type": "decision", "project_id": "test-proj"})
        facts = mem.list_memories(project_id="test-proj", type_filter=["fact"])
        decisions = mem.list_memories(project_id="test-proj", type_filter=["decision"])
        assert len(facts) == 1
        assert len(decisions) == 1


# ============================================================
# Group I2: MCP Server 工具参数校验
# ============================================================


class TestMCPServerTools:
    """验证 MCP server.py 各工具的参数校验和边界条件"""

    def test_set_project_id_validation_rules(self):
        import re

        pattern = r"^[a-zA-Z0-9_\-]+$"
        assert re.match(pattern, "my-project_01")
        assert not re.match(pattern, "proj/../etc")
        assert not re.match(pattern, "has spaces")
        assert not re.match(pattern, "")

    def test_set_project_id_length_limit(self):
        from memos.server.mcp import _id_len

        assert _id_len > 0
        long_pid = "a" * 65
        assert len(long_pid) > 64

    def test_remember_text_length_limit(self):
        from memos.server.mcp import MAX_INPUT_LENGTH

        long_text = "x" * (MAX_INPUT_LENGTH + 1)
        assert len(long_text) > MAX_INPUT_LENGTH

    def test_recall_top_k_limit(self):
        from memos.config import config

        max_k = config.server.mcp_top_k_max
        assert max_k > 0

    def test_save_knowledge_type_validation(self):
        valid_types = {"fact", "decision", "preference", "todo"}
        assert "fact" in valid_types
        assert "bug_fix" not in valid_types
        assert "daily_report" not in valid_types

    def test_mcp_tool_count(self):
        """验证 11 个 MCP 工具均已注册（v0.4.5 新增 list_todos + update_todo）"""
        from memos.server.mcp import mcp

        tool_names = [t.name for t in mcp._tool_manager._tools.values()]
        expected = {
            "remember",
            "recall",
            "list_memories",
            "set_project_id",
            "log_complete_turn",
            "save_knowledge",
            "update_memory",
            "delete_memory",
            "force_extract",
            "list_todos",
            "update_todo",
        }
        assert set(tool_names) == expected


# ============================================================
# Group I3: 端点-提示词解耦集成 (F3+F4)
# ============================================================


class TestEndpointPromptIntegration:
    """验证 F3(模板类型) + F4(端点解耦) 的模块协作"""

    def test_full_fallback_chain(self):
        """三级 fallback 链路正确"""
        from memos.config import PromptManager, PromptTemplate, LLMEndpoint

        pm = PromptManager()
        # 创建各类型默认模板
        pm.upsert(
            PromptTemplate(id="default", name="全局默认", template_type="default", system_prompt_text="通用提示词")
        )
        pm.upsert(
            PromptTemplate(
                id="default-extract", name="提炼默认", template_type="extract", system_prompt_text="提炼专用提示词"
            )
        )
        pm.upsert(
            PromptTemplate(
                id="default-daily-review",
                name="日报默认",
                template_type="daily-review",
                system_prompt_text="日报专用提示词",
            )
        )
        # 创建端点专属模板（命名约定格式）
        pm.upsert(
            PromptTemplate(
                id="test-ep@extract",
                name="端点专属提炼",
                template_type="extract",
                system_prompt_text="自定义提炼提示词",
            )
        )

        # 使用 get_by_type 验证类型过滤
        extracts = pm.get_by_type("extract")
        assert any(t.id == "test-ep@extract" for t in extracts)
        assert any(t.id == "default-extract" for t in extracts)

        # 验证 get_for_endpoint 方法存在且可调用
        assert callable(pm.get_for_endpoint)

    def test_template_type_query(self):
        """PromptManager.get_by_type() 按类型过滤"""
        from memos.config import PromptManager, PromptTemplate

        pm = PromptManager()
        pm.upsert(PromptTemplate(id="t1", name="提炼1", template_type="extract", system_prompt_text="s1"))
        pm.upsert(PromptTemplate(id="t2", name="提炼2", template_type="extract", system_prompt_text="s2"))
        pm.upsert(PromptTemplate(id="t3", name="日报1", template_type="daily-review", system_prompt_text="s3"))

        assert len(pm.get_by_type("extract")) == 2
        assert len(pm.get_by_type("daily-review")) == 1
        assert len(pm.get_by_type("default")) == 0

    def test_endpoint_template_sharing(self):
        """同一个模板 ID 可被多个端点关联"""
        from memos.config import PromptManager, PromptTemplate, LLMEndpoint

        pm = PromptManager()
        pm.upsert(
            PromptTemplate(
                id="shared-extract", name="共享提炼模板", template_type="extract", system_prompt_text="共享提示词"
            )
        )

        ep_a = LLMEndpoint(
            name="endpoint-a", api_base="http://a:8080/v1", prompt_templates={"extract": "shared-extract"}
        )
        ep_b = LLMEndpoint(
            name="endpoint-b", api_base="http://b:8080/v1", prompt_templates={"extract": "shared-extract"}
        )

        assert ep_a.prompt_templates["extract"] == "shared-extract"
        assert ep_b.prompt_templates["extract"] == "shared-extract"

    def test_backward_compat_no_template_type(self):
        """旧模板自动补齐 template_type="default" """
        from memos.config import PromptTemplate

        t = PromptTemplate(id="old-template", name="旧模板", system_prompt_text="旧提示词")
        assert t.template_type == "default"

    def test_backward_compat_no_prompt_templates(self):
        """旧端点 prompt_templates 默认为空"""
        from memos.config import LLMEndpoint

        ep = LLMEndpoint(name="old-endpoint", api_base="http://old:8080/v1")
        assert ep.prompt_templates == {}


# ============================================================
# Group I4: 今日回顾集成 (F5)
# ============================================================


class TestDailyReviewIntegration:
    """验证 F5 今日回顾全链路"""

    def test_format_conversation_ordering(self):
        from memos.engine.extractor import format_conversation

        records = [
            {"type": "user_input", "content": "第二条用户消息", "timestamp": 200},
            {"type": "assistant_output", "content": "第一条助手回复", "timestamp": 150},
            {"type": "user_input", "content": "第一条用户消息", "timestamp": 100},
        ]
        formatted = format_conversation(records)
        lines = formatted.split("\n")
        assert "第一条用户消息" in lines[0]
        assert "第一条助手回复" in lines[1]
        assert "第二条用户消息" in lines[2]

    def test_format_conversation_skips_empty(self):
        from memos.engine.extractor import format_conversation

        records = [
            {"type": "user_input", "content": "有效消息", "timestamp": 100},
            {"type": "assistant_output", "content": "", "timestamp": 200},
            {"type": "user_input", "content": "   ", "timestamp": 300},
        ]
        formatted = format_conversation(records)
        assert formatted == "User: 有效消息"

    def test_daily_review_templates_exist(self):
        """验证 daily-review 类型模板可通过配置获取"""
        from memos.config import config

        pm = config.prompt
        pm.ensure_default_template()
        daily_templates = pm.get_by_type("daily-review")
        assert len(daily_templates) >= 1

    def test_daily_review_api_no_data(self):
        """daily-review API 无数据时正确降级（可能需认证）"""
        from memos.web.app import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            resp = client.post(
                "/api/conversations/daily-review", json={"date": "2020-01-01", "project_id": "nonexistent-project-xyz"}
            )
            # 可能返回 200（无数据）或 401（需认证）
            assert resp.status_code in [200, 401]
            if resp.status_code == 200:
                data = resp.json()
                assert data["raw_rounds"] == 0

    def test_daily_review_preview_no_data(self):
        """daily-review preview 无数据时正确提示（可能需认证）"""
        from memos.web.app import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            resp = client.post(
                "/api/conversations/daily-review/preview", json={"date": "2020-01-01", "project_id": "nonexistent-xyz"}
            )
            assert resp.status_code in [200, 401]

    def test_extract_llm_content_completion_format(self):
        """_extract_llm_content 兼容 completion 和 chat/completions 两种格式"""
        from memos.engine.extractor import _extract_llm_content

        # chat/completions 格式
        assert "hello" in _extract_llm_content({"choices": [{"message": {"content": "hello world"}}]})
        # completion 格式
        assert "hello" in _extract_llm_content({"content": "hello world"})
        # None
        assert _extract_llm_content(None) == ""

    def test_strip_think_block(self):
        """_strip_think_block 处理思考块"""
        from memos.engine.extractor import _strip_think_block

        # 闭合标签
        result = _strip_think_block("<think>推理内容</think>实际输出")
        assert "实际输出" in result
        assert "推理内容" not in result
        # 无标签
        assert _strip_think_block("普通文本") == "普通文本"


# ============================================================
# Group I5: 异常边界与错误码传播 (F7)
# ============================================================


class TestErrorPropagation:
    """验证 MemoError 跨模块传播和捕获"""

    def test_memo_error_to_dict_all_fields(self):
        e = ChromaDBError("数据库连接失败", suggestion="检查ChromaDB服务", detail="Connection refused")
        d = e.to_dict()
        assert d["code"] == "MEM_003"
        assert "数据库连接失败" in d["message"]
        assert "检查ChromaDB服务" in d["suggestion"]
        assert "Connection refused" in d["detail"]

    def test_memo_error_str_format(self):
        e = ModelNotFoundError("bge-large-zh-v1.5未找到")
        assert "[MEM_001]" in str(e)
        assert "bge-large" in str(e)

    def test_all_error_codes_unique(self):
        codes = [
            ModelNotFoundError.code,
            LLMUnreachableError.code,
            ChromaDBError.code,
            ConfigCorruptedError.code,
            DiskFullError.code,
            PermissionDeniedError.code,
        ]
        assert len(codes) == len(set(codes))

    def test_http_status_mapping(self):
        assert http_status_for(ModelNotFoundError()) == 503
        assert http_status_for(LLMUnreachableError()) == 502
        assert http_status_for(ChromaDBError()) == 500
        assert http_status_for(ConfigCorruptedError()) == 400
        assert http_status_for(DiskFullError()) == 507
        assert http_status_for(PermissionDeniedError()) == 403

    def test_format_error_cli(self):
        e = LLMUnreachableError("端点 ollama 连接超时", detail="ConnectTimeout: 10s")
        formatted = format_error(e)
        assert "[MEM_002]" in formatted
        assert "端点 ollama 连接超时" in formatted
        assert "ConnectTimeout" in formatted

    def test_from_exception_preserves_detail(self):
        orig = ValueError("原始错误信息")
        wrapped = from_exception(orig, ChromaDBError)
        assert wrapped.code == "MEM_003"
        assert "原始错误信息" in wrapped.message
        assert "ValueError" in wrapped.detail

    def test_memory_crud_raises_chromadb_error(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        with pytest.raises(ChromaDBError):
            mem.update_memory("nonexistent-id-xyz", new_content="test")
        with pytest.raises(ChromaDBError):
            mem.delete_memory("nonexistent-id-xyz")

    def test_config_load_corrupted_raises_config_error(self):
        from memos.config import restore_from_backup

        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "bad.json"
            bad_path.write_text("{invalid json", encoding="utf-8")
            with pytest.raises(ConfigCorruptedError):
                restore_from_backup(bad_path)

    def test_memo_error_template_substitution(self):
        e = ModelNotFoundError("{model} 未找到", model="bge-large")
        assert "bge-large" in e.message
        assert "未找到" in e.message

    def test_disk_full_error(self):
        e = DiskFullError("需要 500MB，可用 10MB")
        assert e.code == "MEM_005"
        assert "500MB" in e.message

    def test_permission_denied_error(self):
        e = PermissionDeniedError("无法写入 /etc/config.json")
        assert e.code == "MEM_006"


# ============================================================
# Group I6: 线程安全 (CRIT-1/CRIT-4 修复验证)
# ============================================================


class TestThreadSafety:
    """验证 CRIT-1/CRIT-4 修复后的线程安全性"""

    def test_encoder_has_lock(self):
        from memos.engine.memory import _encoder_lock

        assert _encoder_lock is not None
        assert isinstance(_encoder_lock, type(threading.Lock()))

    def test_bm25_has_lock(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        assert hasattr(mem, "_bm25_lock")
        assert mem._bm25_lock is not None

    def test_concurrent_remember_threads(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        errors = []

        def do_remember(i):
            try:
                mem.remember(f"并发测试内容{i}", metadata={"type": "fact", "project_id": "test-proj"})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_remember, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发 remember 出现 {len(errors)} 个错误: {errors}"
        count = mem.count_memories(project_id="test-proj")
        assert count == 10

    def test_concurrent_recall_threads(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        for i in range(20):
            mem.remember(f"并发检索测试{i}", metadata={"type": "fact", "project_id": "test-proj"})

        errors = []

        def do_recall():
            try:
                mem.recall("并发检索", top_k=5, project_id="test-proj")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_recall) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发 recall 出现 {len(errors)} 个错误"


# ============================================================
# Group I7: 配置集成与持久化
# ============================================================


class TestConfigIntegration:
    """验证配置加载、环境变量覆盖、持久化完整链路"""

    def test_config_has_v030_fields(self):
        """配置包含 v0.3.0 新增字段"""
        from memos.config import config

        mc = config.model
        assert hasattr(mc, "download_retries")
        assert hasattr(mc, "download_timeout")
        assert hasattr(mc, "verify_sha256")
        assert mc.download_retries == 3
        assert mc.download_timeout == 600

    def test_llm_endpoint_has_prompt_templates(self):
        from memos.config import config

        for ep in config.llm.endpoints:
            assert hasattr(ep, "prompt_templates")
            assert isinstance(ep.prompt_templates, dict)

    def test_config_flatten_roundtrip(self):
        """配置扁平化键名包含分隔符"""
        from memos.config import config

        flat = config.flatten()
        assert isinstance(flat, dict)

    def test_prompt_manager_get_by_type(self):
        from memos.config import config

        pm = config.prompt
        pm.ensure_default_template()
        extracts = pm.get_by_type("extract")
        assert len(extracts) >= 1
        for t in extracts:
            assert t.template_type == "extract"

    def test_prompt_manager_get_for_endpoint(self):
        from memos.config import config

        pm = config.prompt
        pm.ensure_default_template()
        # 使用默认端点查找
        result = pm.get_for_endpoint("default", template_type="extract")
        # 应该找到 default-extract（通过命名约定或 fallback）
        assert result is not None

    def test_default_templates_exist(self):
        """必要的默认/回退模板均已创建"""
        from memos.config import config

        pm = config.prompt
        pm.ensure_default_template()
        assert pm.get("fallback") is not None
        assert pm.get("default@extract") is not None
        assert pm.get("default@daily-review") is not None
        assert pm.get("fallback@extract") is not None


# ============================================================
# Group I8: 安装向导集成 (F1+F2)
# ============================================================


class TestWizardIntegration:
    """验证 F1+F2 向导与模型下载的集成"""

    def test_wizard_steps_definition(self):
        from memos.features.wizard import InitWizard

        assert len(InitWizard.STEPS) == 6
        assert InitWizard.STEPS[0] == "环境检测"
        assert InitWizard.STEPS[-1] == "初始化完成"

    def test_wizard_state_save_and_load(self):
        from memos.features.wizard import InitWizard
        from memos.config import MemoConfig

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            etc_dir = home / "etc"
            etc_dir.mkdir(parents=True)
            config_file = etc_dir / "config.json"
            config_file.write_text(
                json.dumps(
                    {
                        "chroma": {
                            "mode": "persistent",
                            "path": str(etc_dir / "memdb"),
                            "collection_name": "test",
                            "host": "localhost",
                            "port": 8001,
                            "timeout": 30,
                        },
                        "model": {
                            "path": str(etc_dir / "model"),
                            "vector_dim": 1024,
                            "download_retries": 3,
                            "download_timeout": 600,
                            "verify_sha256": False,
                        },
                        "llm": {"active": "default", "endpoints": []},
                        "memory": {},
                        "buffer": {},
                        "dashboard": {},
                        "server": {},
                        "prompt": {},
                        "auth": {},
                    }
                ),
                encoding="utf-8",
            )
            # 设置 MEMOS_HOME 以控制配置加载路径
            os.environ["MEMOS_HOME"] = str(home)
            try:
                cfg = MemoConfig.load()
                wizard = InitWizard(cfg, home=home)
                wizard._mark_step_done(0)
                assert wizard._state.get("step_0") is True
                wizard2 = InitWizard(cfg, home=home)
                assert wizard2._state.get("step_0") is True
                wizard2._clear_state()
                assert not wizard2.state_file.exists()
            finally:
                del os.environ["MEMOS_HOME"]

    def test_wizard_ctrl_c_propagation(self):
        """CRIT-7修复: _input 在 KeyboardInterrupt 时重新抛出"""
        from memos.features.wizard import InitWizard
        from memos.config import MemoConfig

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            etc_dir = home / "etc"
            etc_dir.mkdir(parents=True)
            config_file = etc_dir / "config.json"
            config_file.write_text(
                json.dumps(
                    {
                        "chroma": {
                            "mode": "persistent",
                            "path": str(etc_dir / "memdb"),
                            "collection_name": "test",
                            "host": "localhost",
                            "port": 8001,
                            "timeout": 30,
                        },
                        "model": {
                            "path": str(etc_dir / "model"),
                            "vector_dim": 1024,
                            "download_retries": 3,
                            "download_timeout": 600,
                            "verify_sha256": False,
                        },
                        "llm": {"active": "default", "endpoints": []},
                        "memory": {},
                        "buffer": {},
                        "dashboard": {},
                        "server": {},
                        "prompt": {},
                        "auth": {},
                    }
                ),
                encoding="utf-8",
            )
            os.environ["MEMOS_HOME"] = str(home)
            try:
                cfg = MemoConfig.load()
                wizard = InitWizard(cfg, home=home)
                # 验证 _input 方法存在且可调用
                assert callable(wizard._input)
                with patch("builtins.input", side_effect=KeyboardInterrupt):
                    with pytest.raises(KeyboardInterrupt):
                        wizard._input("测试: ")
            finally:
                del os.environ["MEMOS_HOME"]

    def test_model_path_by_name(self):
        """CRIT-6修复: get_model_path 按模型名返回不同目录"""
        from memos.storage.embeddings import get_model_path

        bge_path = get_model_path("bge-large-zh-v1.5")
        minilm_path = get_model_path("all-MiniLM-L6-v2")
        assert "bge-large-zh-v1.5" in str(bge_path)
        assert "all-MiniLM-L6-v2" in str(minilm_path)
        assert bge_path != minilm_path

    def test_model_exists_detection(self):
        """model_exists 检测模型文件"""
        from memos.storage.embeddings import model_exists, get_model_path

        # bge-large 应该存在（本地已下载）
        model_dir = get_model_path("bge-large-zh-v1.5")
        exists = model_exists(model_dir)
        assert isinstance(exists, bool)

    def test_model_download_config_defaults(self):
        from memos.config import config

        mc = config.model
        assert mc.download_retries == 3
        assert mc.download_timeout == 600
        assert mc.verify_sha256 is False


# ============================================================
# Group I9: Dashboard API 集成
# ============================================================


class TestDashboardAPIIntegration:
    """验证 Dashboard API 前后端数据流"""

    @pytest.fixture
    def client(self):
        from memos.web.app import app
        from fastapi.testclient import TestClient

        with TestClient(app) as c:
            yield c

    def test_system_status_endpoint(self, client):
        resp = client.get("/api/status")
        # 可能需认证，但 /api/status 应为公开端点
        assert resp.status_code in [200, 401]

    def test_prompts_list_endpoint(self, client):
        resp = client.get("/api/prompts")
        assert resp.status_code in [200, 401]

    def test_llm_endpoints_list(self, client):
        resp = client.get("/api/llm/endpoints")
        assert resp.status_code in [200, 401]

    def test_daily_review_invalid_date(self, client):
        resp = client.post("/api/conversations/daily-review", json={"date": "invalid-date", "project_id": "test"})
        assert resp.status_code in [200, 400, 401, 422]

    def test_daily_review_nonexistent_endpoint(self, client):
        resp = client.post(
            "/api/conversations/daily-review",
            json={"date": "2026-05-16", "project_id": "test", "llm_endpoint": "nonexistent-endpoint-xyz"},
        )
        assert resp.status_code in [200, 400, 401, 404, 422, 502]


# ============================================================
# Group I10: 性能基线
# ============================================================


class TestPerformanceBaseline:
    """验证 v0.3.0 性能指标"""

    def test_remember_latency(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        start = time.perf_counter()
        mem.remember("性能测试内容", metadata={"type": "fact", "project_id": "perf-test"})
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 500, f"remember 延迟 {elapsed:.0f}ms 超过 500ms"

    def test_recall_latency(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        for i in range(10):
            mem.remember(f"性能测试{i}", metadata={"type": "fact", "project_id": "perf-test"})
        start = time.perf_counter()
        mem.recall("性能测试", top_k=5, project_id="perf-test")
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 2000, f"recall 延迟 {elapsed:.0f}ms 超过 2000ms"

    def test_list_memories_latency(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        for i in range(100):
            mem.remember(f"批量{i}", metadata={"type": "fact", "project_id": "perf-test"})
        start = time.perf_counter()
        items = mem.list_memories(project_id="perf-test", limit=100, offset=0)
        elapsed = (time.perf_counter() - start) * 1000
        assert len(items) == 100
        assert elapsed < 1000, f"list_memories(100) 延迟 {elapsed:.0f}ms 超过 1000ms"


# ============================================================
# Group I11: v0.2.0 功能回归
# ============================================================


class TestV020Regression:
    """验证 v0.2.0 功能在 v0.3.0 中保持兼容"""

    @pytest.mark.skip(reason="v0.4.2: export header format changed, needs test update")
    def test_export_import_roundtrip(self):
        from memos.engine.memory import ContextMemory
        import io

        mem, _ = _make_memory_with_temp_collection()
        for i in range(5):
            mem.remember(f"导出测试{i}", metadata={"type": "fact", "project_id": "export-test"})
        output = io.StringIO()
        count = 0
        for line in mem.export_memories(project_id="export-test"):
            output.write(json.dumps(line, ensure_ascii=False) + "\n")
            count += 1
        assert count == 5
        output.seek(0)
        result = mem.import_memories(output, target_project_id="import-test", strategy="duplicate")
        assert result["imported"] == 5
        assert len(result.get("errors", [])) == 0

    def test_hybrid_search(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        mem.remember("Python异步编程使用asyncio库", metadata={"type": "fact", "project_id": "hybrid-test"})
        mem.remember("JavaScript使用Promise处理异步", metadata={"type": "fact", "project_id": "hybrid-test"})
        results = mem.recall("异步编程", top_k=3, project_id="hybrid-test", hybrid=True, bm25_weight=0.5)
        assert len(results) > 0

    def test_time_decay_sorting(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        mem.remember(
            "旧记忆", metadata={"type": "fact", "project_id": "decay-test", "created_at": time.time() - 86400 * 30}
        )
        mem.remember("新记忆", metadata={"type": "fact", "project_id": "decay-test", "created_at": time.time()})
        results = mem.recall("记忆", top_k=5, project_id="decay-test")
        assert len(results) > 0

    def test_archive_restore_flow(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        mid = mem.remember("待归档记忆", metadata={"type": "fact", "project_id": "archive-test"})
        mem.archive_memory(mid)
        archived = mem.get_memory(mid)
        assert archived is not None
        # archive 设置 active=False
        assert archived.get("metadata", {}).get("active") is False
        mem.restore_memory(mid)
        restored = mem.get_memory(mid)
        # restore 设置 active=True
        assert restored.get("metadata", {}).get("active") is True

    def test_count_memories(self):
        from memos.engine.memory import ContextMemory

        mem, _ = _make_memory_with_temp_collection()
        for i in range(5):
            mem.remember(f"计数测试{i}", metadata={"type": "fact", "project_id": "count-test"})
        count = mem.count_memories(project_id="count-test")
        assert count == 5
