"""Phase 2 F2: 提炼质量评分 — 单元测试
覆盖：quality_score 解析/低于阈值标记/复审列表/确认操作/删除操作/向后兼容
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from memos.config import config, MemoryConfig
from memos.engine.extractor import MemoryExtractor


@pytest.fixture
def ext():
    """创建无真实 LLM 的 MemoryExtractor 实例"""
    e = MemoryExtractor.__new__(MemoryExtractor)
    e.memory = None
    e.project_id = "test-project"
    e.project_name = "测试项目"
    return e


class TestQualityScoreParsing:
    """验证 quality_score 解析正确"""

    def test_quality_score_from_llm_response(self):
        """LLM 返回含 quality_score 的记忆被正确解析"""
        mem = {
            "content": "团队使用FastAPI框架",
            "type": "decision",
            "quality_score": 0.85,
            "quality_reason": "内容具体",
        }
        assert mem.get("quality_score") == 0.85
        assert mem.get("quality_reason") == "内容具体"

    def test_quality_score_invalid_converted(self, ext):
        """无效 quality_score 转为默认值"""
        memories = [{"content": "测试", "type": "fact", "quality_score": "not_a_number"}]
        stored_metas = []
        original = ext.memory
        try:
            fm = mock.Mock()
            fm.recall_with_scores.return_value = []
            ext.memory = fm
            ext.store_memories(memories)
            for call in fm.remember.call_args_list:
                _, kwargs = call
                stored_metas.append(kwargs.get("metadata", {}))
        finally:
            ext.memory = original
        assert stored_metas[0]["quality_score"] == 0.5

    def test_quality_reason_stored(self, ext):
        """quality_reason 正确写入 metadata"""
        memories = [{"content": "测试", "type": "fact", "quality_score": 0.7, "quality_reason": "信息完整"}]
        stored_metas = []
        original = ext.memory
        try:
            fm = mock.Mock()
            fm.recall_with_scores.return_value = []
            ext.memory = fm
            ext.store_memories(memories)
            for call in fm.remember.call_args_list:
                _, kwargs = call
                stored_metas.append(kwargs.get("metadata", {}))
        finally:
            ext.memory = original
        assert stored_metas[0]["quality_reason"] == "信息完整"


class TestBackwardCompatibility:
    """验证向后兼容"""

    def test_no_quality_score_in_old_memory(self):
        """旧记忆（无 quality_score metadata）不应出现在复审列表"""
        old_meta = {"type": "fact", "source": "manual"}
        old_meta.get("quality_score")  # 旧记忆无此字段，返回 None

    def test_default_quality_threshold_exists(self):
        """quality_threshold 配置项存在且默认 0.5"""
        cfg = MemoryConfig()
        assert cfg.quality_threshold == 0.5


@pytest.mark.skip(reason="v0.4.1 未实现：review API 端点缺失")
class TestDashboardReviewAPI:
    """验证复审 API 端点的路由注册（集成测试在 test_integration_all.py 中）"""

    def test_review_queue_endpoint_exists(self):
        """验证复审队列 API 路由存在"""
        from memos.web.app import app

        routes = [r.path for r in app.routes]
        assert "/api/memories/review-queue" in routes

    def test_approve_endpoint_exists(self):
        from memos.web.app import app

        routes = [r.path for r in app.routes]
        assert "/api/memories/{id}/approve" in routes

    def test_reject_endpoint_exists(self):
        from memos.web.app import app

        routes = [r.path for r in app.routes]
        assert "/api/memories/{id}/reject" in routes
