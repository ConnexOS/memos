"""测试 F2 - MCP update_memory / delete_memory 工具"""

import os
import tempfile
from pathlib import Path

from memos.engine.memory import ContextMemory

from .conftest import clean_collection

COLLECTION = "test_mcp_crud"


class TestMCPUpdateDelete:
    """MCP update_memory / delete_memory 工具单元测试"""

    @classmethod
    def setup_class(cls):
        os.environ["MEMOS_TEST_COLLECTION"] = COLLECTION
        # 确保先初始化（避免测试中延迟 init）
        from memos.server.mcp import _ensure_initialized, _reset_for_test

        _reset_for_test(COLLECTION)
        _ensure_initialized()

    @classmethod
    def teardown_class(cls):
        os.environ.pop("MEMOS_TEST_COLLECTION", None)
        from memos.server.mcp import _reset_for_test

        _reset_for_test()

    def setup_method(self):
        from memos.server.mcp import _get_memory, _get_project_id

        self.mem = _get_memory()
        self.pid = _get_project_id()
        clean_collection(self.mem)

    def test_update_memory_text(self):
        """更新记忆内容"""
        from memos.server.mcp import update_memory

        mid = self.mem.remember("旧内容", {"type": "fact", "project_id": self.pid})
        result = update_memory(mid, text="新内容")
        assert "已更新" in result
        updated = self.mem.get_memory(mid)
        assert updated["document"] == "新内容"

    def test_update_memory_metadata_merge(self):
        """更新元数据采用合并策略"""
        from memos.server.mcp import update_memory

        mid = self.mem.remember("测试内容", {"type": "fact", "source": "test", "project_id": self.pid})
        result = update_memory(mid, metadata={"type": "decision"})
        assert "已更新" in result
        updated = self.mem.get_memory(mid)
        assert updated["metadata"]["type"] == "decision"
        # source 字段应保留（合并，非替换）
        assert updated["metadata"]["source"] == "test"

    def test_update_memory_not_found(self):
        """更新不存在的 ID"""
        from memos.server.mcp import update_memory

        result = update_memory("nonexistent-id")
        assert "不存在" in result

    def test_update_memory_cross_project_rejected(self):
        """跨项目更新被拒绝"""
        from memos.server.mcp import update_memory, _project_ctx

        mid = self.mem.remember("跨项目测试", {"type": "fact", "project_id": "other-project"})
        saved = getattr(_project_ctx, "project_id", None)
        try:
            _project_ctx.project_id = self.pid
            result = update_memory(mid, text="越权修改")
            assert "跨项目" in result
        finally:
            if saved is not None:
                _project_ctx.project_id = saved

    def test_delete_memory(self):
        """删除记忆"""
        from memos.server.mcp import delete_memory

        mid = self.mem.remember("待删除内容", {"type": "fact", "project_id": self.pid})
        assert self.mem.get_memory(mid) is not None
        result = delete_memory(mid)
        assert "已删除" in result
        assert self.mem.get_memory(mid) is None

    def test_delete_memory_not_found(self):
        """删除不存在的 ID"""
        from memos.server.mcp import delete_memory

        result = delete_memory("nonexistent-id")
        assert "不存在" in result

    def test_delete_memory_cross_project_rejected(self):
        """跨项目删除被拒绝"""
        from memos.server.mcp import delete_memory, _project_ctx

        mid = self.mem.remember("跨项目删除测试", {"type": "fact", "project_id": "other-project"})
        saved = getattr(_project_ctx, "project_id", None)
        try:
            _project_ctx.project_id = self.pid
            result = delete_memory(mid)
            assert "跨项目" in result
        finally:
            if saved is not None:
                _project_ctx.project_id = saved

    def test_update_memory_no_params(self):
        """不提供任何更新参数时报错"""
        from memos.server.mcp import update_memory

        mid = self.mem.remember("测试", {"type": "fact", "project_id": self.pid})
        result = update_memory(mid)
        assert "请至少提供" in result
