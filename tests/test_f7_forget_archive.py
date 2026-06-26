"""F7: 主动遗忘 + 30天自动归档 — 单元测试"""

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
    mem.list_memories.return_value = [
        {
            "id": "id-1",
            "document": "测试记忆1",
            "metadata": {"type": "fact", "project_id": "proj-1", "timestamp": 1000000000, "active": True},
        },
        {
            "id": "id-2",
            "document": "测试记忆2",
            "metadata": {"type": "decision", "project_id": "proj-1", "timestamp": 1000000001, "active": True},
        },
    ]
    mem.count_memories.return_value = 2
    mem.get_memory.return_value = {
        "id": "id-1",
        "document": "测试记忆1",
        "metadata": {"type": "fact", "project_id": "proj-1", "timestamp": 1000000000, "active": True},
    }
    mem.remember.return_value = "new-id-123"
    mem.store.count.return_value = 2
    mem.store.get.return_value = {
        "ids": ["id-1", "id-2"],
        "metadatas": [
            {"type": "fact", "project_id": "proj-1", "timestamp": 1000000000, "active": True},
            {"type": "decision", "project_id": "proj-2", "timestamp": 1000000001, "active": True},
        ],
    }
    mem.recall.return_value = [
        {
            "id": "id-1",
            "document": "测试记忆1",
            "metadata": {"type": "fact", "project_id": "proj-1"},
            "similarity": 0.92,
            "decay_factor": 0.85,
            "final_score": 0.78,
        },
    ]
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
# ContextMemory 层测试：forget / restore / archive
# ============================================================


class TestForgetMemory:
    def test_forget_success(self):
        """forget_memory 设置 status=forgotten + inactive_reason + forgotten_at"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {
            "ids": ["mem-001"],
            "documents": ["测试记忆"],
            "metadatas": [{"type": "fact", "project_id": "proj-1"}],
        }
        mem.store = fake_store

        mem.forget_memory("mem-001", inactive_reason="obsolete")

        fake_store.update.assert_called_once()
        call_kwargs = fake_store.update.call_args[1]
        assert call_kwargs["ids"] == ["mem-001"]
        meta = call_kwargs["metadatas"][0]
        assert meta["status"] == "forgotten"
        assert meta["inactive_reason"] == "obsolete"
        assert "forgotten_at" in meta
        assert isinstance(meta["forgotten_at"], float)

    def test_forget_not_found(self):
        """forget_memory 不存在的记忆抛异常"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mem.store = fake_store

        with pytest.raises(ChromaDBError):
            mem.forget_memory("nonexistent")


class TestRestoreMemory:
    def test_restore_clears_inactive_fields(self):
        """restore_memory 重置 status=active，清除 inactive_reason 和 forgotten_at"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {
            "ids": ["mem-001"],
            "documents": ["测试记忆"],
            "metadatas": [{"status": "forgotten", "inactive_reason": "obsolete", "forgotten_at": 1000000.0}],
        }
        mem.store = fake_store

        mem.restore_memory("mem-001")

        fake_store.update.assert_called_once()
        meta = fake_store.update.call_args[1]["metadatas"][0]
        assert meta["status"] == "active"
        assert meta["inactive_reason"] == ""
        assert meta["forgotten_at"] == 0

    def test_restore_not_found(self):
        """restore_memory 不存在的记忆抛异常"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mem.store = fake_store

        with pytest.raises(ChromaDBError):
            mem.restore_memory("nonexistent")

    def test_restore_from_forgotten_delegates(self):
        """restore_from_forgotten 委托给 restore_memory"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {
            "ids": ["mem-001"],
            "documents": ["测试记忆"],
            "metadatas": [{"status": "forgotten", "inactive_reason": "obsolete"}],
        }
        mem.store = fake_store

        with mock.patch.object(mem, "restore_memory", wraps=mem.restore_memory) as spy:
            mem.restore_from_forgotten("mem-001")
            spy.assert_called_once_with("mem-001")


class TestArchiveMemory:
    def test_archive_sets_status(self):
        """archive_memory 设置 status=archived + inactive_reason=manual_archive"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {
            "ids": ["mem-001"],
            "documents": ["测试记忆"],
            "metadatas": [{"status": "active", "type": "fact"}],
        }
        mem.store = fake_store

        mem.archive_memory("mem-001", inactive_reason="manual_archive")

        fake_store.update.assert_called_once()
        meta = fake_store.update.call_args[1]["metadatas"][0]
        assert meta["status"] == "archived"
        assert meta["inactive_reason"] == "manual_archive"

    def test_archive_not_found(self):
        """archive_memory 不存在的记忆抛异常"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mem.store = fake_store

        with pytest.raises(ChromaDBError):
            mem.archive_memory("nonexistent")

    def test_permanent_archive_delegates(self):
        """permanent_archive 委托给 archive_memory"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {
            "ids": ["mem-001"],
            "documents": ["测试记忆"],
            "metadatas": [{"status": "active", "type": "fact"}],
        }
        mem.store = fake_store

        with mock.patch.object(mem, "archive_memory", wraps=mem.archive_memory) as spy:
            mem.permanent_archive("mem-001")
            spy.assert_called_once_with("mem-001")


# ============================================================
# archive_old_memories 测试：F7 自动归档扫描
# ============================================================


class TestArchiveOldMemories:
    def test_archives_old_forgotten(self):
        """超过 archive_days 的 forgotten 记忆自动归档"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        now = time.time()
        old_ts = now - 40 * 86400  # 40 天前
        fake_store.get.return_value = {
            "ids": ["mem-001", "mem-002"],
            "metadatas": [
                {"status": "forgotten", "forgotten_at": old_ts, "inactive_reason": "manual_forget"},
                {"status": "forgotten", "forgotten_at": old_ts, "inactive_reason": "obsolete"},
            ],
        }
        mem.store = fake_store

        count = mem.archive_old_memories(days=30)

        assert count == 2
        fake_store.update.assert_called_once()
        call_kwargs = fake_store.update.call_args[1]
        assert len(call_kwargs["ids"]) == 2
        for meta in call_kwargs["metadatas"]:
            assert meta["status"] == "archived"
            assert meta["inactive_reason"] == "auto_archive"

    def test_skips_recent_forgotten(self):
        """30 天内的 forgotten 记忆不归档"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        now = time.time()
        recent_ts = now - 10 * 86400  # 10 天前（< 30 天）
        fake_store.get.return_value = {
            "ids": ["mem-001"],
            "metadatas": [
                {"status": "forgotten", "forgotten_at": recent_ts, "inactive_reason": "manual_forget"},
            ],
        }
        mem.store = fake_store

        # cutoff = now - 30*86400，recent_ts=now-10*86400 > cutoff，所以不会返回
        # 实际上返回的 ids 已经由 ChromaDB where 过滤，所以我们模拟空结果
        fake_store.get.return_value = {"ids": [], "metadatas": []}

        count = mem.archive_old_memories(days=30)

        assert count == 0
        fake_store.update.assert_not_called()

    def test_skips_forgotten_at_zero(self):
        """forgotten_at=0 的未迁移旧数据跳过归档"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        now = time.time()
        fake_store.get.return_value = {
            "ids": ["mem-001"],
            "metadatas": [
                {"status": "forgotten", "forgotten_at": 0, "inactive_reason": "manual_forget"},
            ],
        }
        mem.store = fake_store

        count = mem.archive_old_memories(days=30)

        assert count == 0
        fake_store.update.assert_not_called()

    def test_no_forgotten_memories(self):
        """没有 forgotten 记忆时返回 0"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {"ids": [], "metadatas": []}
        mem.store = fake_store

        count = mem.archive_old_memories(days=30)

        assert count == 0
        fake_store.update.assert_not_called()


# ============================================================
# Scheduler 层测试：_auto_archive_forgotten
# ============================================================


class TestSchedulerAutoArchive:
    def test_auto_archive_called_once_per_day(self):
        """调度器每日只执行一次 forgotten 归档扫描"""
        from memos.features.scheduler import SchedulerThread

        mock_memory = mock.Mock()
        mock_memory.archive_old_memories.return_value = 0

        scheduler = SchedulerThread(memory_instance=mock_memory)

        # 第一次调用触发扫描
        scheduler._auto_archive_forgotten()
        assert mock_memory.archive_old_memories.call_count == 1

        # 同一天的第二次调用不触发
        scheduler._auto_archive_forgotten()
        assert mock_memory.archive_old_memories.call_count == 1

    def test_auto_archive_with_results(self):
        """归档到记忆时记录日志"""
        from memos.features.scheduler import SchedulerThread

        mock_memory = mock.Mock()
        mock_memory.archive_old_memories.return_value = 3

        scheduler = SchedulerThread(memory_instance=mock_memory)
        scheduler._auto_archive_forgotten()

        mock_memory.archive_old_memories.assert_called_once()

    def test_auto_archive_no_memory(self):
        """没有 memory 实例时跳过"""
        from memos.features.scheduler import SchedulerThread

        scheduler = SchedulerThread(memory_instance=None)
        scheduler._auto_archive_forgotten()  # 不抛异常即通过


# ============================================================
# Dashboard API 端点测试：forget / restore / archive
# ============================================================


class TestForgetEndpoint:
    def test_forget_success(self, client, mock_mem):
        """POST /api/memories/{id}/forget 返回 200"""
        resp = client.post("/api/memories/id-1/forget")
        assert resp.status_code == 200
        mock_mem.forget_memory.assert_called_once_with("id-1")

    def test_forget_not_found(self, client, mock_mem):
        """forget 不存在的记忆返回 404"""
        mock_mem.forget_memory.side_effect = ValueError("未找到")
        resp = client.post("/api/memories/nonexistent/forget")
        assert resp.status_code == 404

    def test_forget_chromadb_error(self, client, mock_mem):
        """forget 数据库异常返回 404"""
        from memos.errors import ChromaDBError
        mock_mem.forget_memory.side_effect = ChromaDBError("数据库错误")
        resp = client.post("/api/memories/id-1/forget")
        assert resp.status_code == 404


class TestArchiveEndpoint:
    def test_archive_success(self, client, mock_mem):
        """POST /api/memories/{id}/archive 返回 200"""
        resp = client.post("/api/memories/id-1/archive")
        assert resp.status_code == 200
        assert resp.json()["message"] == "记忆已归档"
        mock_mem.archive_memory.assert_called_once_with("id-1")

    def test_archive_not_found(self, client, mock_mem):
        """archive 不存在的记忆返回 404"""
        mock_mem.archive_memory.side_effect = ValueError("未找到")
        resp = client.post("/api/memories/nonexistent/archive")
        assert resp.status_code == 404


class TestRestoreEndpoint:
    def test_restore_success(self, client, mock_mem):
        """POST /api/memories/{id}/restore 返回 200"""
        resp = client.post("/api/memories/id-1/restore")
        assert resp.status_code == 200
        assert resp.json()["message"] == "记忆已恢复"
        mock_mem.restore_memory.assert_called_once_with("id-1")

    def test_restore_not_found(self, client, mock_mem):
        """restore 不存在的记忆返回 404"""
        mock_mem.restore_memory.side_effect = ValueError("未找到")
        resp = client.post("/api/memories/nonexistent/restore")
        assert resp.status_code == 404
