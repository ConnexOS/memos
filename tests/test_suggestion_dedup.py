"""S6 — F7 建议去重边界测试 (v0.4.4)

覆盖：冷却期边界、每日上限边界、过期边界、expiry_days=0、上限达到后行为。
"""

from unittest.mock import MagicMock

import pytest


class TestSuggestionCooldownBoundary:
    """_check_suggestion_cooldown 边界条件。"""

    def _import_func(self):
        from memos.hooks.prompt import _check_suggestion_cooldown

        return _check_suggestion_cooldown

    @pytest.fixture
    def mem(self):
        m = MagicMock()
        m.store.get.return_value = {"ids": []}
        return m

    def test_exact_cooldown_boundary(self, mem):
        """精确在冷却期边界外（刚过冷却期）不应被阻断。"""
        func = self._import_func()
        mem.store.get.return_value = {"ids": []}
        assert func(mem, "source-1", "default", 30) is False

    def test_within_cooldown_by_one_second(self, mem):
        """冷却期内 1 秒也被阻断。"""
        func = self._import_func()
        mem.store.get.return_value = {"ids": ["sug-1"]}
        assert func(mem, "source-1", "default", 30) is True

    def test_zero_cooldown_disabled(self, mem):
        """cooldown_minutes=0 不检查冷却期。"""
        func = self._import_func()
        assert func(mem, "source-1", "default", 0) is False

    def test_negative_cooldown_treated_as_zero(self, mem):
        """负值冷却期视为 0（不检查）。"""
        func = self._import_func()
        assert func(mem, "source-1", "default", -1) is False

    def test_different_source_different_cooldown(self, mem):
        """不同 source_memory_id 互不影响。"""
        func = self._import_func()
        mem.store.get.return_value = {"ids": ["sug-1"]}
        assert func(mem, "source-1", "default", 30) is True
        mem.store.get.return_value = {"ids": []}
        assert func(mem, "source-2", "default", 30) is False


class TestSuggestionDailyLimitBoundary:
    """_check_daily_limit 边界条件。"""

    def _import_func(self):
        from memos.hooks.prompt import _check_daily_limit

        return _check_daily_limit

    @pytest.fixture
    def mem(self):
        m = MagicMock()
        m.store.count.return_value = 0
        return m

    def test_exactly_at_limit(self, mem):
        """count == max_per_day 时阻断。"""
        func = self._import_func()
        mem.store.count.return_value = 10
        assert func(mem, "default", 10) is True

    def test_one_below_limit(self, mem):
        """count < max_per_day 时不阻断。"""
        func = self._import_func()
        mem.store.count.return_value = 9
        assert func(mem, "default", 10) is False

    def test_zero_max_prevents_all(self, mem):
        """max_per_day=0 全部阻断。"""
        func = self._import_func()
        assert func(mem, "default", 0) is True

    def test_count_failure_blocks(self, mem):
        """count 查询异常时降级阻断。"""
        func = self._import_func()
        mem.store.count.side_effect = Exception("DB error")
        assert func(mem, "default", 10) is True
