"""管道二系统状态型建议 —— 14+ 测试用例 (v0.4.4 增强版 Phase 2)"""

import os
import time
from pathlib import Path
from unittest import mock

from memos.hooks.prompt import (
    _check_first_time_user,
    _check_unrefined_rounds,
    _check_low_quality_ratio,
    _check_no_daily_review,
    _check_inactive_project,
    _check_expired_memories,
    _check_event_cooldown,
    _check_pipe2_daily_limit,
    _generate_system_suggestions,
)


def _make_mem(count_result=0, get_result=None):
    """创建 mock ContextMemory。"""
    mem = mock.Mock()
    store = mock.Mock()
    store.count.return_value = count_result
    store.get.return_value = get_result or {"ids": [], "documents": [], "metadatas": []}
    mem.store = store
    return mem


PID = "test_pipe2"


# ========== T2.1 first_time_user ==========


class TestCheckFirstTimeUser:
    def test_empty_kb_triggers(self):
        """知识库为空 → 触发。"""
        mem = _make_mem(count_result=0)
        result = _check_first_time_user(mem, PID)
        assert result is not None
        assert result["event_type"] == "first_time_user"
        assert result["priority"] == "high"

    def test_non_empty_kb_skips(self):
        """知识库不为空 → 不触发。"""
        mem = _make_mem(count_result=5)
        assert _check_first_time_user(mem, PID) is None

    def test_never_retriggers_after_seeding(self):
        """升级场景：已有 1 条知识 → 不触发。"""
        mem = _make_mem(count_result=1)
        assert _check_first_time_user(mem, PID) is None


# ========== T2.2 unrefined_rounds ==========


class TestCheckUnrefinedRounds:
    def test_sufficient_rounds_and_low_ratio_triggers(self):
        """user_input > 20 且提炼率 < 30% → 触发。"""
        mem = _make_mem()
        mem.store.count.side_effect = [30, 5]  # user_input=30, knowledge=5
        result = _check_unrefined_rounds(mem, PID)
        assert result is not None
        assert result["event_type"] == "unrefined_rounds"
        assert result["priority"] == "medium"

    def test_insufficient_rounds_skips(self):
        """user_input <= 20 → 不触发。"""
        mem = _make_mem()
        mem.store.count.side_effect = [15]  # 仅 15 轮
        assert _check_unrefined_rounds(mem, PID) is None

    def test_sufficient_ratio_skips(self):
        """提炼率 >= 30% → 不触发。"""
        mem = _make_mem()
        mem.store.count.side_effect = [30, 15]  # 30 rounds, 15 knowledge = 50%
        assert _check_unrefined_rounds(mem, PID) is None

    def test_zero_knowledge_still_triggers(self):
        """user_input=30, knowledge=0 → ratio=0 → 触发。"""
        mem = _make_mem()
        mem.store.count.side_effect = [30, 0]
        assert _check_unrefined_rounds(mem, PID) is not None


# ========== T2.3 low_quality_ratio ==========


class TestCheckLowQualityRatio:
    def test_high_low_quality_ratio_triggers(self):
        """分母 >= 10 且低质量占比 > 30% → 触发。"""
        metadatas = [{"quality_score": 0.3}] * 5 + [{"quality_score": 0.8}] * 5
        mem = _make_mem(
            get_result={"ids": list(range(10)), "metadatas": metadatas}
        )
        result = _check_low_quality_ratio(mem, PID)
        assert result is not None
        assert result["event_type"] == "low_quality_ratio"

    def test_small_sample_skips(self):
        """分母 < 10 → 不触发。"""
        metadatas = [{"quality_score": 0.3}] * 3
        mem = _make_mem(
            get_result={"ids": list(range(3)), "metadatas": metadatas}
        )
        assert _check_low_quality_ratio(mem, PID) is None

    def test_low_ratio_skips(self):
        """低质量占比 <= 30% → 不触发。"""
        metadatas = [{"quality_score": 0.3}] * 2 + [{"quality_score": 0.8}] * 8
        mem = _make_mem(
            get_result={"ids": list(range(10)), "metadatas": metadatas}
        )
        assert _check_low_quality_ratio(mem, PID) is None

    def test_empty_metadatas_handled(self):
        """无 quality_score 时默认按高质量处理。"""
        metadatas = [{"type": "fact"}] * 12
        mem = _make_mem(
            get_result={"ids": list(range(12)), "metadatas": metadatas}
        )
        assert _check_low_quality_ratio(mem, PID) is None


# ========== T2.4 no_daily_review ==========


class TestCheckNoDailyReview:
    def test_no_directory_triggers(self, tmp_path):
        """日报目录不存在 → 触发。"""
        with mock.patch("memos.hooks.prompt.PROJECT_DIR", tmp_path):
            result = _check_no_daily_review("dummy")
            assert result is not None
            assert result["event_type"] == "no_daily_review"

    def test_empty_directory_triggers(self, tmp_path):
        """日报目录为空 → 触发。"""
        (tmp_path / "document" / "日报").mkdir(parents=True, exist_ok=True)
        with mock.patch("memos.hooks.prompt.PROJECT_DIR", tmp_path):
            result = _check_no_daily_review("dummy")
            assert result is not None
            assert result["event_type"] == "no_daily_review"

    def test_recent_review_skips(self, tmp_path):
        """最新日报 < 3 天 → 不触发。"""
        review_dir = tmp_path / "document" / "日报"
        review_dir.mkdir(parents=True, exist_ok=True)
        (review_dir / "2026-05-25-开发日报.md").write_text("# test", encoding="utf-8")
        with mock.patch("memos.hooks.prompt.PROJECT_DIR", tmp_path):
            assert _check_no_daily_review("dummy") is None

    def test_stale_review_triggers(self, tmp_path):
        """最新日报 > 3 天 → 触发。"""
        review_dir = tmp_path / "document" / "日报"
        review_dir.mkdir(parents=True, exist_ok=True)
        f = review_dir / "old-review.md"
        f.write_text("# old", encoding="utf-8")
        old_ts = time.time() - 5 * 86400
        os.utime(str(f), (old_ts, old_ts))
        with mock.patch("memos.hooks.prompt.PROJECT_DIR", tmp_path):
            result = _check_no_daily_review("dummy")
            assert result is not None
            assert result["event_type"] == "no_daily_review"


# ========== T2.5 inactive_project ==========


class TestCheckInactiveProject:
    def test_long_inactive_triggers(self):
        """最新活动 > 7 天 → 触发。"""
        old_ts = time.time() - 10 * 86400
        metadatas = [{"timestamp": old_ts, "type": "fact"}]
        mem = _make_mem(get_result={"ids": ["1"], "metadatas": metadatas})
        result = _check_inactive_project(mem, PID)
        assert result is not None
        assert result["event_type"] == "inactive_project"

    def test_recent_activity_skips(self):
        """最近 7 天内有活动 → 不触发。"""
        recent_ts = time.time() - 1 * 86400
        metadatas = [{"timestamp": recent_ts, "type": "decision"}]
        mem = _make_mem(get_result={"ids": ["1"], "metadatas": metadatas})
        assert _check_inactive_project(mem, PID) is None

    def test_empty_knowledge_base_skips(self):
        """知识库为空 → 不触发。"""
        mem = _make_mem(get_result={"ids": [], "metadatas": []})
        assert _check_inactive_project(mem, PID) is None


# ========== T2.6 expired_memories ==========


class TestCheckExpiredMemories:
    def test_many_expired_triggers(self):
        """过期记忆 > 5 条 → 触发。"""
        now = time.time()
        metadatas = (
            [{"expiry_date": now - 100, "type": "fact"}] * 6
            + [{"expiry_date": 0, "type": "decision"}] * 2
        )
        mem = _make_mem(get_result={"ids": list(range(8)), "metadatas": metadatas})
        result = _check_expired_memories(mem, PID)
        assert result is not None
        assert result["event_type"] == "expired_memories"

    def test_few_expired_skips(self):
        """过期记忆 <= 5 → 不触发。"""
        now = time.time()
        metadatas = [{"expiry_date": now - 100, "type": "fact"}] * 3
        mem = _make_mem(get_result={"ids": list(range(3)), "metadatas": metadatas})
        assert _check_expired_memories(mem, PID) is None

    def test_expiry_date_zero_excluded(self):
        """expiry_date=0 不计入过期。"""
        metadatas = [{"expiry_date": 0, "type": "fact"}] * 10
        mem = _make_mem(get_result={"ids": list(range(10)), "metadatas": metadatas})
        assert _check_expired_memories(mem, PID) is None


# ========== T2.7 event_cooldown ==========


class TestCheckEventCooldown:
    def test_same_event_in_cooldown(self):
        """同事件 24h 内 → True（冷却期内）。"""
        mem = _make_mem(get_result={"ids": ["sug1"]})
        assert _check_event_cooldown(mem, PID, "first_time_user") is True

    def test_no_recent_event_passes_cooldown(self):
        """同事件无记录 → False（可触发）。"""
        mem = _make_mem(get_result={"ids": []})
        assert _check_event_cooldown(mem, PID, "first_time_user") is False


# ========== T2.8 pipe2_daily_limit ==========


class TestCheckPipe2DailyLimit:
    def test_under_limit_passes(self):
        """未达上限 → False。"""
        mem = _make_mem(count_result=1)
        assert _check_pipe2_daily_limit(mem, PID) is False

    def test_at_limit_blocks(self):
        """已达上限 → True。"""
        mem = _make_mem(count_result=3)
        assert _check_pipe2_daily_limit(mem, PID) is True

    def test_zero_daily_limit_always_blocks(self):
        """daily_limit=0 → 始终阻断。"""
        mem = _make_mem(count_result=0)
        with mock.patch("memos.hooks.prompt._get_system_suggestion_config") as mock_cfg:
            fake = mock.Mock()
            fake.daily_limit = 0
            fake.cooldown_hours = 24
            mock_cfg.return_value = fake
            assert _check_pipe2_daily_limit(mem, PID) is True


# ========== T2.9 main orchestrator ==========


class TestGenerateSystemSuggestions:
    def test_disabled_returns_empty(self):
        """system_suggestion.enabled=False → 空列表。"""
        with (
            mock.patch("memos.hooks.prompt._get_system_suggestion_config") as mock_cfg,
            mock.patch("memos.hooks.prompt._get_memory_config"),
        ):
            fake = mock.Mock()
            fake.enabled = False
            mock_cfg.return_value = fake
            result = _generate_system_suggestions(_make_mem(), PID)
            assert result == []

    def test_no_suggestions_file_blocks(self, tmp_path):
        """免打扰文件存在 → 空列表。"""
        with (
            mock.patch("memos.hooks.prompt.NO_SUGGESTIONS_FILE",
                       tmp_path / ".claude" / "no_suggestions"),
            mock.patch("memos.hooks.prompt._get_system_suggestion_config") as mock_cfg,
            mock.patch("memos.hooks.prompt._get_memory_config"),
        ):
            fake = mock.Mock()
            fake.enabled = True
            fake.daily_limit = 3
            fake.cooldown_hours = 24
            mock_cfg.return_value = fake

            no_sug = tmp_path / ".claude" / "no_suggestions"
            no_sug.parent.mkdir(parents=True, exist_ok=True)
            no_sug.write_text('{"created_at": 1}')

            result = _generate_system_suggestions(_make_mem(), PID)
            assert result == []

    def test_first_time_user_high_priority(self):
        """同时触发多个事件，高优先级排在前面。"""
        count_side_effects = {"count": [0]}  # first_time_user: count=0
        mem = _make_mem()
        mem.store.count.side_effect = [0]  # knowledge_types count = 0

        with (
            mock.patch("memos.hooks.prompt._check_event_cooldown", return_value=False),
            mock.patch("memos.hooks.prompt._no_suggestions_file_exists", return_value=False),
            mock.patch("memos.hooks.prompt._get_system_suggestion_config") as mock_cfg,
        ):
            fake = mock.Mock()
            fake.enabled = True
            fake.daily_limit = 3
            fake.cooldown_hours = 24
            fake.triggers = mock.Mock(
                first_time_user=True, unrefined_rounds=True,
                low_quality_ratio=True, no_daily_review=True,
                inactive_project=True, expired_memories=True,
            )
            mock_cfg.return_value = fake
            result = _generate_system_suggestions(mem, PID)
            assert len(result) > 0
            assert result[0]["priority"] == "high"

    def test_single_check_failure_does_not_block_others(self):
        """单条检查失败不阻断其他事件。"""
        mem = _make_mem()
        mem.store.count.return_value = 0  # first_time_user 触发条件

        def failing_check(mem, pid):
            raise RuntimeError("网络异常")

        with (
            mock.patch("memos.hooks.prompt._check_first_time_user", failing_check),
            mock.patch("memos.hooks.prompt._check_unrefined_rounds", return_value=None),
            mock.patch("memos.hooks.prompt._check_event_cooldown", return_value=False),
            mock.patch("memos.hooks.prompt._no_suggestions_file_exists", return_value=False),
            mock.patch("memos.hooks.prompt._get_system_suggestion_config") as mock_cfg,
        ):
            fake = mock.Mock()
            fake.enabled = True
            fake.daily_limit = 3
            fake.cooldown_hours = 24
            fake.triggers = mock.Mock(
                first_time_user=True, unrefined_rounds=True,
                low_quality_ratio=True, no_daily_review=True,
                inactive_project=True, expired_memories=True,
            )
            mock_cfg.return_value = fake
            result = _generate_system_suggestions(mem, PID)
            assert isinstance(result, list)

    def test_disabled_trigger_skipped(self):
        """disabled trigger 不执行检查。"""
        mem = _make_mem()
        mem.store.count.return_value = 0  # 会触发，但 first_time_user 被禁用

        with (
            mock.patch("memos.hooks.prompt._get_system_suggestion_config") as mock_cfg,
            mock.patch("memos.hooks.prompt._no_suggestions_file_exists", return_value=False),
        ):
            fake = mock.Mock()
            fake.enabled = True
            fake.daily_limit = 3
            fake.cooldown_hours = 24
            fake.triggers = mock.Mock(
                first_time_user=False,  # 禁用
                unrefined_rounds=True,
                low_quality_ratio=True, no_daily_review=True,
                inactive_project=True, expired_memories=True,
            )
            mock_cfg.return_value = fake
            # first_time_user 被禁用，不会有 high priority
            result = _generate_system_suggestions(mem, PID)
            if result:
                assert all(r["priority"] != "high" for r in result)
