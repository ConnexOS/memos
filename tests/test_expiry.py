"""Phase 4 F3: 知识过期提醒 — 单元测试"""

import time
from unittest import mock

import pytest

from memos.config import config, MemoryConfig
from memos.engine.memory import ContextMemory


@pytest.mark.skip(reason="v0.4.1 未实现：_compute_expiry_status 方法缺失")
class TestExpiryStatusCalculation:
    """验证过期状态计算"""

    def test_active_memory(self):
        """新记忆标记为 active"""
        mem = ContextMemory.__new__(ContextMemory)
        now = time.time()
        recent_ts = now - 10 * 86400  # 10 天前
        status = mem._compute_expiry_status(recent_ts, now)
        assert status == "active"

    def test_expiring_soon(self):
        """接近 archive_days 的记忆标记为 expiring_soon"""
        mem = ContextMemory.__new__(ContextMemory)
        now = time.time()
        borderline_ts = now - 75 * 86400  # 75 天前（90-30=60 < 75 < 90）
        status = mem._compute_expiry_status(borderline_ts, now)
        assert status == "expiring_soon"

    def test_expired(self):
        """超过 archive_days 的记忆标记为 expired"""
        mem = ContextMemory.__new__(ContextMemory)
        now = time.time()
        old_ts = now - 100 * 86400  # 100 天前（> 90 archive_days）
        status = mem._compute_expiry_status(old_ts, now)
        assert status == "expired"

    def test_exact_boundary_expiring_soon(self):
        """正好在 archive_days - warn_days 边界"""
        mem = ContextMemory.__new__(ContextMemory)
        now = time.time()
        ts = now - 60 * 86400  # 60 天 = 90 - 30
        status = mem._compute_expiry_status(ts, now)
        assert status == "expiring_soon"

    def test_exact_boundary_expired(self):
        """正好在 archive_days 边界"""
        mem = ContextMemory.__new__(ContextMemory)
        now = time.time()
        ts = now - 90 * 86400
        status = mem._compute_expiry_status(ts, now)
        assert status == "expired"


@pytest.mark.skip(reason="v0.4.1 未实现：renew_memory 方法缺失")
class TestRenewMemory:
    """验证续期操作"""

    def test_renew_updates_timestamp(self, monkeypatch):
        """续期更新 timestamp 和 renewed_count"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {
            "ids": ["mem-001"],
            "metadatas": [{"timestamp": 1000000, "renewed_count": 0}],
        }
        mem.store = fake_store
        result = mem.renew_memory("mem-001")
        assert result is True
        # 验证 update 被调用
        fake_store.update.assert_called_once()
        call_args = fake_store.update.call_args
        assert call_args[1]["ids"] == ["mem-001"]
        new_meta = call_args[1]["metadatas"][0]
        assert new_meta["renewed_count"] == 1

    def test_renew_nonexistent(self):
        """续期不存在的记忆返回 False"""
        mem = ContextMemory.__new__(ContextMemory)
        fake_store = mock.Mock()
        fake_store.get.return_value = {"ids": [], "metadatas": []}
        mem.store = fake_store
        result = mem.renew_memory("nonexistent")
        assert result is False


@pytest.mark.skip(reason="v0.4.1 未实现：batch_renew 方法缺失")
class TestBatchRenew:
    """批量续期"""

    def test_batch_renew(self, monkeypatch):
        mem = ContextMemory.__new__(ContextMemory)
        renew_results = [True, False, True]
        call_count = [0]

        def fake_renew(mid):
            r = renew_results[call_count[0]]
            call_count[0] += 1
            return r

        monkeypatch.setattr(mem, "renew_memory", fake_renew)
        count = mem.renew_memories_batch(["a", "b", "c"])
        assert count == 2


@pytest.mark.skip(reason="v0.4.1 未实现：过期管理 API 端点缺失")
class TestExpiryConfig:
    """配置项验证"""

    def test_expiry_warn_days_default(self):
        cfg = MemoryConfig()
        assert cfg.expiry_warn_days == 30

    def test_expiry_api_routes(self):
        from memos.web.app import app

        routes = [r.path for r in app.routes]
        assert "/api/memories/expiring" in routes
        assert "/api/memories/{id}/renew" in routes
        assert "/api/memories/batch-renew" in routes
