"""Phase 1-3: 今日回顾 — 长上下文优化单元测试"""

import json
from datetime import datetime
from unittest import mock

import pytest

from memos.engine.extractor import MemoryExtractor
from memos.engine.review import (
    _batch_partition_summarize,
    _batch_summarize,
    _direct_format_conversation,
    _token_aware_chunk,
)

MOCK_PATH = "memos.engine.extractor.requests.post"


# ============================================================
# Task 1: _batch_summarize model 参数
# ============================================================


class TestBatchSummarizeModelParam:
    """验证 _batch_summarize 的 model 参数是否正确传递到请求体"""

    def test_model_param_in_payload(self):
        """传入 model_name 时，_request_with_retry 收到含 model 字段的 payload"""
        rounds = [
            {"type": "user_input", "content": "帮我写一个函数", "timestamp": 100.0},
            {"type": "assistant_output", "content": "好的，这是代码", "timestamp": 101.0},
        ]
        captured = []

        def _capture(payload):
            captured.append(payload)
            mock_resp = mock.Mock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"choices": [{"message": {"content": "summary"}}]}
            return mock_resp

        with mock.patch.object(MemoryExtractor, "_request_with_retry") as mock_method:
            mock_method.side_effect = _capture

            _batch_summarize(
                rounds, "http://test/v1", "test-key", mock.Mock(), "2026-05-27", model_name="deepseek-v4-flash"
            )

            assert len(captured) == 1
            assert captured[0].get("model") == "deepseek-v4-flash"

    def test_model_param_empty(self):
        """model_name 为空时，payload 不含 model 字段"""
        rounds = [
            {"type": "user_input", "content": "帮我写一个函数", "timestamp": 100.0},
            {"type": "assistant_output", "content": "好的，这是代码", "timestamp": 101.0},
        ]
        captured = []

        def _capture(payload):
            captured.append(payload)
            mock_resp = mock.Mock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"choices": [{"message": {"content": "summary"}}]}
            return mock_resp

        with mock.patch.object(MemoryExtractor, "_request_with_retry") as mock_method:
            mock_method.side_effect = _capture

            _batch_summarize(
                rounds, "http://test/v1", "test-key", mock.Mock(), "2026-05-27", model_name=""
            )

            assert len(captured) == 1
            assert "model" not in captured[0]

    def test_batch_partition_passes_model(self):
        """_batch_partition_summarize 正确传递 model_name 到 _batch_summarize 的 payload"""
        rounds = []
        for i in range(25):
            rounds.append({
                "type": "user_input" if i % 2 == 0 else "assistant_output",
                "content": f"content {i}",
                "timestamp": float(100 + i),
            })

        captured = []

        def _capture(payload):
            captured.append(payload)
            mock_resp = mock.Mock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"choices": [{"message": {"content": "summary"}}]}
            return mock_resp

        with mock.patch.object(MemoryExtractor, "_request_with_retry") as mock_method:
            mock_method.side_effect = _capture

            _batch_partition_summarize(
                rounds, "http://test/v1", "test-key", mock.Mock(), "2026-05-27", model_name="test-model"
            )

            assert len(captured) >= 1
            for p in captured:
                assert p.get("model") == "test-model"


# ============================================================
# Task 2: 全批失败降级为 DIRECT
# ============================================================


class TestBatchAllFallback:
    """验证全批预摘要失败时降级为 DIRECT 策略"""

    def test_all_fallback_returns_direct_format(self):
        """所有批都降级时，返回文本不含包装指令"""
        rounds = []
        for i in range(35):
            rounds.append({
                "type": "user_input" if i % 2 == 0 else "assistant_output",
                "content": f"question {i}" if i % 2 == 0 else f"answer {i}",
                "timestamp": float(100 + i),
            })

        prompt_tpl = mock.Mock()
        prompt_tpl.build_payload.return_value = {
            "messages": [{"role": "user", "content": "test"}],
        }

        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.side_effect = ConnectionError("simulated network error")

            result = _batch_partition_summarize(
                rounds, "http://test/v1", "test-key", prompt_tpl, "2026-05-27", model_name="test-model"
            )

            assert "以下为对话摘要" not in result
            assert "Today's date: 2026-05-27" in result

    def test_partial_fallback_keeps_wrapper(self):
        """部分批成功时（至少 1 批成功），保留包装指令"""
        rounds = []
        for i in range(35):
            rounds.append({
                "type": "user_input" if i % 2 == 0 else "assistant_output",
                "content": f"question {i}" if i % 2 == 0 else f"answer {i}",
                "timestamp": float(100 + i),
            })

        prompt_tpl = mock.Mock()
        prompt_tpl.build_payload.return_value = {
            "messages": [{"role": "user", "content": "test"}],
        }

        call_count = [0]

        def _mock_request(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                mock_resp = mock.Mock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "choices": [{"message": {"content": "first batch summary"}}]
                }
                return mock_resp
            return None

        with mock.patch(
            "memos.engine.review.MemoryExtractor._request_with_retry",
        ) as mock_retry:
            mock_retry.side_effect = _mock_request

            result = _batch_partition_summarize(
                rounds, "http://test/v1", "test-key", prompt_tpl, "2026-05-27", model_name="test-model"
            )

            assert "以下为对话摘要" in result
            assert "第 1 部分" in result or "第1部分" in result


# ============================================================
# Task 3: 动态 Token 感知分片
# ============================================================


class TestTokenAwareChunk:
    """验证 _token_aware_chunk 的分片逻辑"""

    def test_basic_chunking(self):
        """输入 50 轮等长对话，每片不超过 max_tokens"""
        rounds = []
        for i in range(50):
            rounds.append({
                "user_content": "x" * 500,
                "assistant_content": "y" * 500,
                "timestamp": float(i),
            })

        chunks = _token_aware_chunk(rounds, max_tokens=12000)

        for chunk in chunks:
            total_chars = sum(
                len(r.get("user_content", "") + r.get("assistant_content", ""))
                for r in chunk
            )
            total_tokens = int(total_chars / 0.75)
            assert total_tokens <= 12000, f"batch tokens ({total_tokens}) exceed limit"

        total_rounds = sum(len(c) for c in chunks)
        assert total_rounds == 50

    def test_empty_input(self):
        """空列表返回空列表"""
        assert _token_aware_chunk([], max_tokens=12000) == []

    def test_single_round(self):
        """单轮对话返回 1 批"""
        rounds = [{"user_content": "hi", "assistant_content": "hello", "timestamp": 1.0}]
        chunks = _token_aware_chunk(rounds, max_tokens=12000)
        assert len(chunks) == 1
        assert len(chunks[0]) == 1

    def test_all_rounds_within_limit(self):
        """所有轮次总 token 未超过上限时，不分片"""
        rounds = []
        for i in range(5):
            rounds.append({
                "user_content": "short text",
                "assistant_content": "short reply",
                "timestamp": float(i),
            })

        chunks = _token_aware_chunk(rounds, max_tokens=12000)
        assert len(chunks) == 1
        assert len(chunks[0]) == 5

    def test_different_chunk_sizes(self):
        """不同 max_tokens 产生不同分片数"""
        rounds = []
        for i in range(10):
            rounds.append({
                "user_content": "x" * 1000,
                "assistant_content": "y" * 1000,
                "timestamp": float(i),
            })

        small_chunks = _token_aware_chunk(rounds, max_tokens=5000)
        large_chunks = _token_aware_chunk(rounds, max_tokens=20000)

        assert len(small_chunks) >= len(large_chunks)

    def test_consecutive_calls_deterministic(self):
        """相同输入多次调用产生相同结果"""
        rounds = []
        for i in range(10):
            rounds.append({
                "user_content": f"di {i} wen ti",
                "assistant_content": f"di {i} ge hui da",
                "timestamp": float(i),
            })

        chunks1 = _token_aware_chunk(rounds, max_tokens=5000)
        chunks2 = _token_aware_chunk(rounds, max_tokens=5000)

        assert len(chunks1) == len(chunks2)
        for c1, c2 in zip(chunks1, chunks2):
            assert len(c1) == len(c2)


# ============================================================
# Task 4: 单轮超限保护
# ============================================================


class TestSingleRoundOverflow:
    """验证单轮超出 max_tokens 时的保护"""

    def test_single_round_exceeds_limit(self):
        """单轮超过 max_tokens 时单独成批"""
        rounds = [{
            "user_content": "x" * 10000,
            "assistant_content": "",
            "timestamp": 0.0,
        }]
        for i in range(1, 10):
            rounds.append({
                "user_content": f"short q {i}",
                "assistant_content": f"short a {i}",
                "timestamp": float(i),
            })

        chunks = _token_aware_chunk(rounds, max_tokens=5000)

        found_overflow = False
        for chunk in chunks:
            for r in chunk:
                if len(r.get("user_content", "")) > 5000:
                    found_overflow = True
                    assert len(chunk) == 1
        assert found_overflow

    def test_all_rounds_exceed_limit(self):
        """所有轮次都超过 max_tokens 时，每轮单独成批"""
        rounds = []
        for i in range(5):
            rounds.append({
                "user_content": "x" * 10000,
                "assistant_content": "",
                "timestamp": float(i),
            })

        chunks = _token_aware_chunk(rounds, max_tokens=5000)
        assert len(chunks) == 5
        for chunk in chunks:
            assert len(chunk) == 1


# ============================================================
# _direct_format_conversation 辅助函数
# ============================================================


class TestDirectFormat:
    """验证 _direct_format_conversation 的输出格式"""

    def test_basic_format(self):
        records = [
            {"type": "user_input", "content": "wen ti 1", "timestamp": 100.0},
            {"type": "assistant_output", "content": "hui da 1", "timestamp": 101.0},
        ]
        result = _direct_format_conversation(records, "2026-05-27")
        assert "Today's date: 2026-05-27" in result
        assert "User: wen ti 1" in result
        assert "Assistant: hui da 1" in result

    def test_no_date_str(self):
        records = [
            {"type": "user_input", "content": "test content", "timestamp": 100.0},
        ]
        today = datetime.now().strftime("%Y-%m-%d")
        result = _direct_format_conversation(records, None)
        assert today in result


# ============================================================
# Phase 3: Task 7 — 分层摘要架构
# ============================================================


class TestHierarchicalSummarize:
    """验证 HIERARCHICAL 策略和 _hierarchical_summarize"""

    def test_strategy_selection_hierarchical(self):
        from memos.engine.review import _select_strategy, DailyReviewStrategy

        strategy = _select_strategy(501, True)
        assert strategy == DailyReviewStrategy.HIERARCHICAL

    def test_strategy_selection_batch_at_500(self):
        from memos.engine.review import _select_strategy, DailyReviewStrategy

        strategy = _select_strategy(500, True)
        assert strategy == DailyReviewStrategy.BATCH

    def test_strategy_selection_direct_at_300(self):
        from memos.engine.review import _select_strategy, DailyReviewStrategy

        strategy = _select_strategy(300, True)
        assert strategy == DailyReviewStrategy.DIRECT

    def test_hierarchical_l1_layers_count(self):
        from memos.engine.review import _hierarchical_summarize

        rounds = []
        for i in range(210):
            rounds.append({
                "user_content": f"question {i}",
                "assistant_content": f"answer {i}",
                "timestamp": float(i),
            })

        prompt_tpl = mock.Mock()
        prompt_tpl.build_payload.return_value = {
            "messages": [{"role": "user", "content": "test"}],
        }

        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "L1 summary"}}]}

        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.return_value = mock_resp
            result = _hierarchical_summarize(
                rounds, "http://test/v1", "test-key", prompt_tpl, "2026-05-27", "test-model",
                layer_size=50, summary_per_group=3,
            )

        assert "以下为分层摘要结果" in result

    def test_hierarchical_l2_when_many_groups(self):
        from memos.engine.review import _hierarchical_summarize

        rounds = []
        for i in range(500):
            rounds.append({
                "user_content": f"question {i}",
                "assistant_content": f"answer {i}",
                "timestamp": float(i),
            })

        prompt_tpl = mock.Mock()
        prompt_tpl.build_payload.return_value = {
            "messages": [{"role": "user", "content": "test"}],
        }

        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "summary content"}}]}

        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.return_value = mock_resp
            result = _hierarchical_summarize(
                rounds, "http://test/v1", "test-key", prompt_tpl, "2026-05-27", "test-model",
                layer_size=20, summary_per_group=3,
            )

        assert "以下为分层摘要结果" in result
        assert mock_post.call_count >= 25

    def test_hierarchical_empty_rounds(self):
        from memos.engine.review import _hierarchical_summarize

        result = _hierarchical_summarize([], "http://test/v1", "test-key", mock.Mock(), "2026-05-27")
        assert result == ""

    def test_hierarchical_in_generate_report(self):
        from memos.engine.review import generate_daily_report

        raw_records = []
        for i in range(2000):
            rid = str(i // 2)
            is_user = i % 2 == 0
            content = (
                f"this is user message for round {rid} with enough text to pass preclean filter. "
                f"we need at least 100 chars total per round. round id: {rid}. "
                if is_user
                else f"this is assistant reply for round {rid} with enough text to pass preclean filter. "
                f"contains technical discussion. round id: {rid}. "
            )
            raw_records.append({
                "id": f"r{i}",
                "content": content,
                "type": "user_input" if is_user else "assistant_output",
                "timestamp": float(i),
                "project_id": "default",
                "round_id": rid,
            })

        with mock.patch("memos.engine.review._query_conversations_by_date_range") as mock_query:
            mock_query.return_value = raw_records

            with mock.patch(MOCK_PATH) as mock_post:
                mock_resp = mock.Mock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"choices": [{"message": {"content": "# daily report\n\ncontent"}}]}
                mock_post.return_value = mock_resp

                result = generate_daily_report(
                    mock.Mock(), target_date="2026-05-27",
                )

        assert result["strategy"] == "hierarchical", f"expected hierarchical, got {result['strategy']}"


# ============================================================
# Phase 3: Task 8 — 配置化分片参数
# ============================================================


class TestConfigChunkParams:
    """验证配置化分片参数的读取和使用"""

    def test_batch_partition_reads_chunk_tokens_from_config(self):
        from memos.config import config as _cfg

        original = _cfg.memory.daily_review_chunk_tokens
        try:
            _cfg.memory.daily_review_chunk_tokens = 100

            rounds = []
            for i in range(200):
                rounds.append({
                    "type": "user_input" if i % 2 == 0 else "assistant_output",
                    "content": "a",
                    "timestamp": float(i),
                })

            prompt_tpl = mock.Mock()
            prompt_tpl.build_payload.return_value = {
                "messages": [{"role": "user", "content": "test"}],
            }

            with mock.patch(MOCK_PATH) as mock_post:
                mock_post.side_effect = ConnectionError("simulated error")
                result = _batch_partition_summarize(
                    rounds, "http://test/v1", "test-key", prompt_tpl, "2026-05-27", "test-model"
                )

            assert "Today's date: 2026-05-27" in result
        finally:
            _cfg.memory.daily_review_chunk_tokens = original

    def test_config_defaults_exist(self):
        from memos.config.models import MemoryConfig

        cfg = MemoryConfig()
        assert hasattr(cfg, "daily_review_chunk_tokens")
        assert cfg.daily_review_chunk_tokens == 12000


# ============================================================
# Phase 3: Task 9 — build_payload 增加 model_name
# ============================================================


class TestBuildPayloadModelName:
    """验证 PromptTemplate.build_payload 的 model_name 参数"""

    def test_build_payload_with_model_name(self):
        from memos.config.prompts import PromptTemplate

        tpl = PromptTemplate(id="test")
        payload = tpl.build_payload("conversation content", model_name="deepseek-v4-flash")

        assert payload.get("model") == "deepseek-v4-flash"
        assert "messages" in payload

    def test_build_payload_without_model_name(self):
        from memos.config.prompts import PromptTemplate

        tpl = PromptTemplate(id="test")
        payload = tpl.build_payload("conversation content")

        assert "model" not in payload

    def test_build_payload_model_name_none(self):
        from memos.config.prompts import PromptTemplate

        tpl = PromptTemplate(id="test")
        payload = tpl.build_payload("conversation content", model_name=None)

        assert "model" not in payload

    def test_build_payload_with_both_version_and_model(self):
        from memos.config.prompts import PromptTemplate

        tpl = PromptTemplate(id="test")
        tpl.upgrade("2.0.0", "new version")

        payload = tpl.build_payload("content", version_override="2.0.0", model_name="test-model")

        assert payload.get("model") == "test-model"
        ver = tpl.get_version("2.0.0")
        assert ver is not None
