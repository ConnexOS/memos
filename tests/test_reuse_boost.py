"""Phase 5 F4: 检索结果排序增强 — 单元测试"""

import math
import time
from unittest import mock

import pytest

from memos.config import config, MemoryConfig
from memos.engine.memory import ContextMemory


class TestReuseBoostCalculation:
    """验证 reuse_boost 计算"""

    def test_no_reuse_count_returns_zero(self):
        mem = ContextMemory.__new__(ContextMemory)
        boost = mem._compute_reuse_boost({})
        assert boost == 0.0

    def test_zero_reuse_count_returns_zero(self):
        mem = ContextMemory.__new__(ContextMemory)
        boost = mem._compute_reuse_boost({"reuse_count": 0})
        assert boost == 0.0

    def test_fresh_reuse_gives_boost(self):
        mem = ContextMemory.__new__(ContextMemory)
        now = time.time()
        boost = mem._compute_reuse_boost(
            {
                "reuse_count": 3,
                "last_reused_at": now,
            },
            now,
        )
        # reuse_boost = 3 * exp(-0.01 * 0) * 0.1 = 0.3
        assert boost > 0.2
        assert boost <= 0.5  # within cap

    def test_old_reuse_decays(self):
        mem = ContextMemory.__new__(ContextMemory)
        now = time.time()
        boost = mem._compute_reuse_boost(
            {
                "reuse_count": 3,
                "last_reused_at": now - 30 * 86400,  # 30 days ago
            },
            now,
        )
        # reuse_boost = 3 * exp(-0.01 * 30) * 0.1 = 3 * 0.7408 * 0.1 = 0.222
        assert boost < 0.3  # decayed from 0.3
        assert boost > 0

    def test_boost_capped(self):
        mem = ContextMemory.__new__(ContextMemory)
        now = time.time()
        boost = mem._compute_reuse_boost(
            {
                "reuse_count": 100,
                "last_reused_at": now,
            },
            now,
        )
        # 100 * exp(0) * 0.1 = 10, capped at 0.5
        assert boost == 0.5

    def test_reuse_weight_zero_disables(self, monkeypatch):
        monkeypatch.setattr(config.memory, "reuse_weight", 0.0)
        mem = ContextMemory.__new__(ContextMemory)
        now = time.time()
        boost = mem._compute_reuse_boost(
            {
                "reuse_count": 5,
                "last_reused_at": now,
            },
            now,
        )
        assert boost == 0.0


class TestReuseConfig:
    def test_default_values(self):
        cfg = MemoryConfig()
        assert cfg.reuse_weight == 0.1
        assert cfg.reuse_decay == 0.01
        assert cfg.reuse_boost_cap == 0.5

    def test_view_api_route(self):
        from memos.web.app import app

        routes = [r.path for r in app.routes]
        assert "/api/memories/{id}/view" in routes
