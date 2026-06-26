"""F10: 反馈反哺 — 单元测试"""

import time
from unittest import mock

import pytest
from fastapi.testclient import TestClient


# ============================================================
# Dashboard API 测试专用 Fixture
# ============================================================


@pytest.fixture
def mock_mem():
    mem = mock.MagicMock()
    mem.get_memory.return_value = {
        "id": "mem-001",
        "document": "测试记忆",
        "metadata": {"type": "fact", "project_id": "proj-1", "reuse_count": 5, "useful_feedback_count": 2},
    }
    mem.list_memories.return_value = []
    mem.count_memories.return_value = 0
    mem.store.get.return_value = {
        "ids": ["mem-001"],
        "metadatas": [{"type": "fact", "project_id": "proj-1", "reuse_count": 5, "useful_feedback_count": 2}],
    }
    return mem


@pytest.fixture
def client(mock_mem):
    with (
        mock.patch("memos.server.app.ContextMemory", return_value=mock_mem),
        mock.patch("memos.web.auth.verify_session_token", return_value={"token_hash": "test", "exp": 9999999999}),
    ):
        from memos.server.app import create_unified_app

        app = create_unified_app()
        with TestClient(app, cookies={"memos_session": "fake-session-token"}) as c:
            yield c


from memos.engine.memory import ContextMemory
from memos.errors import ChromaDBError


# ============================================================
# _compute_reuse_boost 测试
# ============================================================


class TestComputeReuseBoost:

    def _make_meta(self, reuse_count=0, useful_feedback_count=0):
        return {"reuse_count": reuse_count, "useful_feedback_count": useful_feedback_count}

    def test_zero_views_zero_feedback(self):
        """无复用无反馈时 boost 为 0"""
        cm = ContextMemory.__new__(ContextMemory)
        meta = self._make_meta(0, 0)
        boost = cm._compute_reuse_boost(meta)
        assert boost == pytest.approx(0.0, abs=1e-6)

    def test_positive_views_no_feedback(self):
        """有复用次数时 boost 来自 log2"""
        cm = ContextMemory.__new__(ContextMemory)
        meta = self._make_meta(10, 0)
        # log2(10+1) * 0.15 ≈ 0.5188
        boost = cm._compute_reuse_boost(meta)
        expected = (11 ** 0.5) * 0  # placeholder
        import math

        expected = math.log2(11) * 0.15
        assert boost == pytest.approx(expected, abs=1e-4)

    def test_positive_feedback_boosts(self):
        """useful_feedback_count 正向贡献"""
        cm = ContextMemory.__new__(ContextMemory)
        meta = self._make_meta(0, 3)
        # 0 + 3 * 0.30 = 0.9
        boost = cm._compute_reuse_boost(meta)
        assert boost == pytest.approx(0.9, abs=1e-6)

    def test_negative_feedback_penalizes(self):
        """useful_feedback_count 负向惩罚"""
        cm = ContextMemory.__new__(ContextMemory)
        meta = self._make_meta(0, -3)
        # 0 + (-3) * 0.30 = -0.9
        boost = cm._compute_reuse_boost(meta)
        assert boost == pytest.approx(-0.9, abs=1e-6)

    def test_negative_feedback_min_clamp(self):
        """useful_feedback_count 最低 -10"""
        cm = ContextMemory.__new__(ContextMemory)
        meta = self._make_meta(0, -20)
        # clamp(-20, -10) = -10, boost = 0 + (-10) * 0.30 = -3.0
        boost = cm._compute_reuse_boost(meta)
        assert boost == pytest.approx(-3.0, abs=1e-6)

    def test_combined_formula(self):
        """reuse_count + useful_feedback_count 组合"""
        cm = ContextMemory.__new__(ContextMemory)
        meta = self._make_meta(7, 2)
        import math

        # log2(7+1) * 0.15 + 2 * 0.30 = log2(8)*0.15 + 0.6 = 3*0.15 + 0.6 = 0.45 + 0.6 = 1.05
        expected = math.log2(8) * 0.15 + 2 * 0.30
        boost = cm._compute_reuse_boost(meta)
        assert boost == pytest.approx(expected, abs=1e-6)

    def test_missing_fields(self):
        """缺失字段时视为 0"""
        cm = ContextMemory.__new__(ContextMemory)
        boost = cm._compute_reuse_boost({})
        assert boost == pytest.approx(0.0, abs=1e-6)

    def test_none_fields(self):
        """None 字段时视为 0"""
        cm = ContextMemory.__new__(ContextMemory)
        meta = {"reuse_count": None, "useful_feedback_count": None}
        boost = cm._compute_reuse_boost(meta)
        assert boost == pytest.approx(0.0, abs=1e-6)


# ============================================================
# _apply_feedback_to_source 测试
# ============================================================


class TestApplyFeedbackToSource:

    @staticmethod
    def _store_result(reuse=0, useful=0):
        """返回 ChromaDB store.get() 格式"""
        return {
            "ids": ["mem-001"],
            "documents": ["测试记忆"],
            "metadatas": [{"reuse_count": reuse, "useful_feedback_count": useful, "type": "fact", "project_id": "p1"}],
        }

    def test_useful_increments_both(self):
        """useful 反馈 +1 reuse_count 和 useful_feedback_count"""
        cm = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = self._store_result(5, 2)
        cm.store = fake_store

        cm._apply_feedback_to_source("mem-001", "useful")

        fake_store.update.assert_called_once()
        meta = fake_store.update.call_args[1]["metadatas"][0]
        assert meta["reuse_count"] == 6
        assert meta["useful_feedback_count"] == 3
        assert "last_feedback_at" in meta

    def test_not_useful_decrements_both(self):
        """not_useful 反馈 -1 reuse_count(最低0) 和 useful_feedback_count(最低-10)"""
        cm = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = self._store_result(3, 1)
        cm.store = fake_store

        cm._apply_feedback_to_source("mem-001", "not_useful")

        fake_store.update.assert_called_once()
        meta = fake_store.update.call_args[1]["metadatas"][0]
        assert meta["reuse_count"] == 2
        assert meta["useful_feedback_count"] == 0
        assert "last_feedback_at" in meta

    def test_not_useful_floor_at_minus_10(self):
        """not_useful useful_feedback_count 最低 -10"""
        cm = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = self._store_result(0, -9)
        cm.store = fake_store

        cm._apply_feedback_to_source("mem-001", "not_useful")

        meta = fake_store.update.call_args[1]["metadatas"][0]
        assert meta["useful_feedback_count"] == -10

    def test_not_useful_reuse_floor_at_0(self):
        """not_useful reuse_count 最低 0"""
        cm = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = self._store_result(0, 0)
        cm.store = fake_store

        cm._apply_feedback_to_source("mem-001", "not_useful")

        meta = fake_store.update.call_args[1]["metadatas"][0]
        assert meta["reuse_count"] == 0

    def test_source_not_found(self):
        """源记忆不存在时静默返回"""
        cm = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        cm.store = fake_store

        cm._apply_feedback_to_source("nonexistent", "useful")
        fake_store.update.assert_not_called()

    def test_unknown_feedback_type(self):
        """未知反馈类型静默返回"""
        cm = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = self._store_result(5, 2)
        cm.store = fake_store

        cm._apply_feedback_to_source("mem-001", "unknown_type")
        fake_store.update.assert_not_called()

    def test_useful_triggers_sse(self):
        """useful 反馈触发 touch_event(feedback)"""
        cm = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = self._store_result(5, 2)
        cm.store = fake_store

        with mock.patch("memos.features.event_bus.touch_event") as mock_touch:
            cm._apply_feedback_to_source("mem-001", "useful")
            mock_touch.assert_called_once_with("feedback")

    def test_not_useful_triggers_sse(self):
        """not_useful 反馈触发 touch_event(feedback)"""
        cm = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = self._store_result(5, 2)
        cm.store = fake_store

        with mock.patch("memos.features.event_bus.touch_event") as mock_touch:
            cm._apply_feedback_to_source("mem-001", "not_useful")
            mock_touch.assert_called_once_with("feedback")


# ============================================================
# Dashboard API 端点测试：feedback useful / not-useful
# ============================================================


class TestFeedbackUsefulEndpoint:

    def test_feedback_useful_success(self, client, mock_mem):
        """POST /api/memories/{id}/feedback/useful 返回 200 并 +1"""
        resp = client.post("/api/memories/mem-001/feedback/useful")
        assert resp.status_code == 200
        data = resp.json()
        assert data["useful_feedback_count"] == 3  # 2 + 1
        assert data["message"] == "已标记为有用"

    def test_feedback_useful_not_found(self, client, mock_mem):
        """不存在的记忆返回 404"""
        mock_mem.get_memory.return_value = None
        resp = client.post("/api/memories/nonexistent/feedback/useful")
        assert resp.status_code == 404

    def test_feedback_useful_zero_start(self, client, mock_mem):
        """从 0 开始 useful_feedback_count"""
        mock_mem.get_memory.return_value = {
            "id": "mem-002",
            "document": "测试",
            "metadata": {"type": "fact", "project_id": "p1"},
        }
        resp = client.post("/api/memories/mem-002/feedback/useful")
        assert resp.status_code == 200
        assert resp.json()["useful_feedback_count"] == 1

    def test_feedback_useful_sse(self, client, mock_mem):
        """触发 SSE touch_event(feedback)"""
        with mock.patch("memos.features.event_bus.touch_event") as mock_touch:
            resp = client.post("/api/memories/mem-001/feedback/useful")
            assert resp.status_code == 200
            mock_touch.assert_called_once_with("feedback")


class TestFeedbackNotUsefulEndpoint:

    def test_feedback_not_useful_success(self, client, mock_mem):
        """POST /api/memories/{id}/feedback/not-useful 返回 200 并 -1"""
        resp = client.post("/api/memories/mem-001/feedback/not-useful")
        assert resp.status_code == 200
        data = resp.json()
        assert data["useful_feedback_count"] == 1  # 2 - 1
        assert data["message"] == "已标记为无用"

    def test_feedback_not_useful_not_found(self, client, mock_mem):
        """不存在的记忆返回 404"""
        mock_mem.get_memory.return_value = None
        resp = client.post("/api/memories/nonexistent/feedback/not-useful")
        assert resp.status_code == 404

    def test_feedback_not_useful_floor(self, client, mock_mem):
        """useful_feedback_count 最低 -10"""
        mock_mem.get_memory.return_value = {
            "id": "mem-003",
            "document": "测试",
            "metadata": {"type": "fact", "project_id": "p1", "useful_feedback_count": -10},
        }
        resp = client.post("/api/memories/mem-003/feedback/not-useful")
        assert resp.status_code == 200
        assert resp.json()["useful_feedback_count"] == -10

    def test_feedback_not_useful_sse(self, client, mock_mem):
        """触发 SSE touch_event(feedback)"""
        with mock.patch("memos.features.event_bus.touch_event") as mock_touch:
            resp = client.post("/api/memories/mem-001/feedback/not-useful")
            assert resp.status_code == 200
            mock_touch.assert_called_once_with("feedback")
