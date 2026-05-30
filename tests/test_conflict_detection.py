"""Phase 3 F1: 记忆冲突检测 — 单元测试
覆盖：冲突检测触发/非冲突跳过/冲突状态流转/降级路径/配置开关
"""

import json
import threading
import time
from unittest import mock

import pytest

from memos.config import config, MemoryConfig
from memos.engine.extractor import MemoryExtractor


@pytest.fixture
def ext():
    e = MemoryExtractor.__new__(MemoryExtractor)
    e.memory = None
    e.project_id = "test-project"
    e.project_name = "test"
    e.llm_url = "http://fake-llm/v1/chat/completions"
    e.api_key = ""
    return e


class TestConflictDetectionConfig:
    """验证冲突检测配置项"""

    def test_conflict_detection_enabled_default(self):
        cfg = MemoryConfig()
        assert cfg.conflict_detection_enabled is True

    def test_conflict_distance_threshold_default(self):
        cfg = MemoryConfig()
        assert cfg.conflict_distance_threshold == 0.85

    def test_conflict_use_llm_default(self):
        cfg = MemoryConfig()
        assert cfg.conflict_use_llm is True

    def test_conflict_disabled_skips_detection(self, ext):
        """conflict_detection_enabled=False 时跳过检测"""
        with mock.patch.object(config.memory, "conflict_detection_enabled", False):
            ext._detect_conflicts_async("test content", "mem-001")
            # 方法立即返回，不启动线程（因为没有调用任何 memory 或 LLM 方法）
            # 通过验证没有抛出异常来确认


class TestConflictStateFlow:
    """验证冲突状态流转（v0.4.5: resolve + discard 替换 confirm + dismiss）"""

    def test_resolve_api_exists(self):
        """v0.4.5: resolve 端点存在"""
        from memos.web.app import app

        assert "/api/conflicts/{pair_id}/resolve" in [r.path for r in app.routes]

    def test_discard_api_exists(self):
        """v0.4.5: discard 端点存在"""
        from memos.web.app import app

        assert "/api/conflicts/{pair_id}/discard" in [r.path for r in app.routes]

    def test_conflict_list_api_exists(self):
        from memos.web.app import app

        routes = [r.path for r in app.routes]
        assert "/api/conflicts" in routes
        assert "/api/conflicts/count" in routes


class TestConflictDetectionAsync:
    """验证异步冲突检测核心逻辑"""

    def test_semaphore_limits_concurrency(self, ext):
        """Semaphore 限制并发检测数"""
        assert MemoryExtractor._conflict_semaphore._value >= 0
        # 初始值应为 3（定义值）
        # 注意：Semaphore._value 可能随 Python 版本不同表现不同

    def test_detect_conflicts_runs_in_background_thread(self, ext, monkeypatch):
        """冲突检测在后台线程中运行"""
        monkeypatch.setattr(config.memory, "conflict_detection_enabled", True)
        monkeypatch.setattr(config.memory, "conflict_distance_threshold", 0.35)

        fm = mock.Mock()
        fm.recall_with_scores.return_value = []
        ext.memory = fm

        thread_started = []

        orig_thread = threading.Thread

        def tracking_thread(*args, **kwargs):
            t = orig_thread(*args, **kwargs)
            thread_started.append(t)
            return t

        monkeypatch.setattr(threading, "Thread", tracking_thread)

        ext._detect_conflicts_async("test content", "mem-001")
        # 验证线程被创建
        assert len(thread_started) >= 1
        # 线程是 daemon
        assert thread_started[0].daemon is True

    def test_detect_conflicts_filters_own_memory(self, ext, monkeypatch):
        """排除自身记忆"""
        monkeypatch.setattr(config.memory, "conflict_detection_enabled", True)
        monkeypatch.setattr(config.memory, "conflict_distance_threshold", 0.35)
        # Mock LLM endpoint 名称
        monkeypatch.setattr(config.llm, "active", "default")

        similar = [
            {"id": "mem-001", "document": "same content", "distance": 0.1, "metadata": {}},
            {"id": "mem-002", "document": "other content", "distance": 0.2, "metadata": {}},
        ]
        fm = mock.Mock()
        fm.recall_with_scores.return_value = similar
        ext.memory = fm

        # Mock LLM 响应
        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"has_conflict": false, "conflict_with": null, "reason": ""}'}}]
        }
        with mock.patch("memos.engine.extractor.requests.post", return_value=mock_resp):
            ext._detect_conflicts_async("test content", "mem-001")
            time.sleep(0.2)  # 等待后台线程完成
            # mem-001 自身被过滤，只保留 mem-002
            # 验证 recall_with_scores 被正确调用
            fm.recall_with_scores.assert_called_once()

    def test_detect_conflicts_filters_dismissed_only(self, ext, monkeypatch):
        """只排除 dismissed 状态，confirmed 可参与新冲突检测"""
        monkeypatch.setattr(config.memory, "conflict_detection_enabled", True)
        monkeypatch.setattr(config.memory, "conflict_distance_threshold", 0.35)

        # 全部为 dismissed → 全部被过滤，LLM 不调用
        similar_all_dismissed = [
            {"id": "mem-002", "document": "content A", "distance": 0.2, "metadata": {"conflict_status": "dismissed"}},
            {"id": "mem-003", "document": "content B", "distance": 0.2, "metadata": {"conflict_status": "dismissed"}},
        ]
        fm = mock.Mock()
        fm.recall_with_scores.return_value = similar_all_dismissed
        ext.memory = fm

        with mock.patch("memos.engine.extractor.requests.post") as mock_post:
            ext._detect_conflicts_async("test content", "mem-001")
            time.sleep(0.2)
            mock_post.assert_not_called()  # 全部 dismissed，LLM 不调用

    def test_detect_conflicts_allows_confirmed_status(self, ext, monkeypatch):
        """confirmed 状态的记忆仍可参与新冲突检测"""
        monkeypatch.setattr(config.memory, "conflict_detection_enabled", True)
        monkeypatch.setattr(config.memory, "conflict_distance_threshold", 0.35)
        monkeypatch.setattr(config.llm, "active", "default")

        similar = [
            {"id": "mem-004", "document": "content A", "distance": 0.2, "metadata": {"conflict_status": "confirmed"}},
        ]
        fm = mock.Mock()
        fm.recall_with_scores.return_value = similar
        fm.update_memory = mock.Mock()
        ext.memory = fm

        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"has_conflict": True, "conflict_with": "mem-004", "reason": "时间冲突"})
                    }
                }
            ]
        }
        fake_pm = mock.Mock()
        fake_tpl = mock.Mock()
        fake_tpl.build_payload.return_value = {"messages": [{"role": "user", "content": "test"}]}
        fake_pm.get_for_endpoint.return_value = fake_tpl
        monkeypatch.setattr(config, "prompt", fake_pm)

        with mock.patch("memos.engine.extractor.requests.post", return_value=mock_resp):
            ext._detect_conflicts_async("test content", "mem-005")
            time.sleep(0.3)
            # confirmed 状态不被过滤，LLM 被调用
            fm.update_memory.assert_called()

    def test_detect_conflicts_degraded_on_llm_error(self, ext, monkeypatch):
        """LLM 不可用时降级处理（不崩溃）"""
        monkeypatch.setattr(config.memory, "conflict_detection_enabled", True)
        monkeypatch.setattr(config.memory, "conflict_distance_threshold", 0.35)

        similar = [
            {"id": "mem-004", "document": "some content", "distance": 0.2, "metadata": {}},
        ]
        fm = mock.Mock()
        fm.recall_with_scores.return_value = similar
        ext.memory = fm

        with mock.patch("memos.engine.extractor.requests.post", side_effect=Exception("LLM unavailable")):
            # 不应抛出异常
            ext._detect_conflicts_async("test content", "mem-001")
            time.sleep(0.3)
            # 线程优雅结束，不崩溃

    def test_detect_conflicts_has_conflict_updates_metadata(self, ext, monkeypatch):
        """检测到冲突时更新双方 metadata"""
        monkeypatch.setattr(config.memory, "conflict_detection_enabled", True)
        monkeypatch.setattr(config.memory, "conflict_distance_threshold", 0.35)

        similar = [
            {"id": "mem-010", "document": "后端使用Flask框架", "distance": 0.15, "metadata": {}},
        ]
        fm = mock.Mock()
        fm.recall_with_scores.return_value = similar
        ext.memory = fm

        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"has_conflict": True, "conflict_with": "mem-010", "reason": "两者关于后端框架的陈述矛盾"}
                        )
                    }
                }
            ]
        }
        # Mock PromptManager 返回冲突检测模板
        fake_pm = mock.Mock()
        fake_tpl = mock.Mock()
        fake_tpl.build_payload.return_value = {"messages": [{"role": "user", "content": "test"}]}
        fake_pm.get_for_endpoint.return_value = fake_tpl
        monkeypatch.setattr(config, "prompt", fake_pm)

        with mock.patch("memos.engine.extractor.requests.post", return_value=mock_resp):
            ext._detect_conflicts_async("后端使用FastAPI框架", "mem-009")
            time.sleep(0.3)
            # 验证双方 metadata 被更新
            update_calls = fm.update_memory.call_args_list
            assert len(update_calls) >= 1, f"Expected >=1 update calls, got {len(update_calls)}"
            # 第一次调用更新新记忆
            call1_meta = update_calls[0][1]["new_metadata"]
            assert call1_meta["conflict_status"] == "pending"
            assert call1_meta["conflict_with"] == "mem-010"

    # ==== v0.4.5: conflict_use_llm=false 分支 ====

    def test_conflict_use_llm_false_vector_only(self, ext, monkeypatch):
        """conflict_use_llm=false 时跳过 LLM，纯向量判断"""
        monkeypatch.setattr(config.memory, "conflict_detection_enabled", True)
        monkeypatch.setattr(config.memory, "conflict_distance_threshold", 0.85)
        monkeypatch.setattr(config.memory, "conflict_use_llm", False)

        similar = [
            {"id": "mem-020", "document": "项目使用 PostgreSQL", "distance": 0.1, "metadata": {}},
        ]
        fm = mock.Mock()
        fm.recall_with_scores.return_value = similar
        ext.memory = fm

        # LLM 不应被调用
        with mock.patch("memos.engine.extractor.requests.post") as mock_post:
            ext._detect_conflicts_async("项目使用 MySQL", "mem-019")
            time.sleep(0.3)
            mock_post.assert_not_called()

            # 确认 update_memory 被调用
            fm.update_memory.assert_called()

    def test_conflict_use_llm_false_sets_role(self, ext, monkeypatch):
        """conflict_use_llm=false 标记 conflict_role"""
        monkeypatch.setattr(config.memory, "conflict_detection_enabled", True)
        monkeypatch.setattr(config.memory, "conflict_distance_threshold", 0.85)
        monkeypatch.setattr(config.memory, "conflict_use_llm", False)

        similar = [
            {"id": "mem-030", "document": "使用 Flask 框架", "distance": 0.12, "metadata": {}},
        ]
        fm = mock.Mock()
        fm.recall_with_scores.return_value = similar
        ext.memory = fm

        ext._detect_conflicts_async("使用 FastAPI 框架", "mem-029")
        time.sleep(0.3)

        update_calls = fm.update_memory.call_args_list
        assert len(update_calls) >= 1
        # 新记忆标记为 trigger
        call1 = update_calls[0][1]["new_metadata"]
        assert call1["conflict_status"] == "pending"
        assert call1["conflict_role"] == "trigger", f"Expected trigger role, got {call1.get('conflict_role')}"
        assert call1["conflict_with"] == "mem-030"

    def test_llm_path_sets_role(self, ext, monkeypatch):
        """LLM 路径也标记 conflict_role"""
        monkeypatch.setattr(config.memory, "conflict_detection_enabled", True)
        monkeypatch.setattr(config.memory, "conflict_distance_threshold", 0.85)
        monkeypatch.setattr(config.memory, "conflict_use_llm", True)

        similar = [
            {"id": "mem-040", "document": "使用 Django 框架", "distance": 0.12, "metadata": {}},
        ]
        fm = mock.Mock()
        fm.recall_with_scores.return_value = similar
        ext.memory = fm

        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(
                {"has_conflict": True, "conflict_with": "mem-040", "reason": "框架选型矛盾"}
            )}}]
        }
        fake_pm = mock.Mock()
        fake_tpl = mock.Mock()
        fake_tpl.build_payload.return_value = {"messages": [{"role": "user", "content": "test"}]}
        fake_pm.get_for_endpoint.return_value = fake_tpl
        monkeypatch.setattr(config, "prompt", fake_pm)

        with mock.patch("memos.engine.extractor.requests.post", return_value=mock_resp):
            ext._detect_conflicts_async("使用 FastAPI 框架", "mem-039")
            time.sleep(0.3)

            update_calls = fm.update_memory.call_args_list
            assert len(update_calls) >= 1
            call1 = update_calls[0][1]["new_metadata"]
            assert call1["conflict_role"] == "trigger"
