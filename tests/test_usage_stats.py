"""Phase 6 F6: LLM 用量统计 — 单元测试"""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from memos.features.usage import UsageLogger


@pytest.fixture
def logger():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
        path = f.name
    lg = UsageLogger(log_path=path)
    yield lg
    try:
        os.unlink(path)
    except OSError:
        pass


class TestUsageLogger:
    def test_log_and_query(self, logger):
        logger.log(
            {
                "event": "extract_success",
                "endpoint": "test",
                "memories_extracted": 3,
                "input_tokens": 100,
                "output_tokens": 200,
            }
        )
        logger.log({"event": "extract_failed", "endpoint": "test", "error": "timeout"})
        events = logger.query()
        assert len(events) == 2
        assert events[0]["event"] == "extract_success"

    def test_query_since_filter(self, logger):
        now = time.time()
        logger.log({"timestamp": now - 10000, "event": "old", "endpoint": "x"})
        logger.log({"timestamp": now, "event": "new", "endpoint": "x"})
        recent = logger.query(since=now - 10)
        assert len(recent) == 1
        assert recent[0]["event"] == "new"

    def test_query_endpoint_filter(self, logger):
        logger.log({"event": "e1", "endpoint": "ep-a"})
        logger.log({"event": "e2", "endpoint": "ep-b"})
        filtered = logger.query(endpoint="ep-a")
        assert len(filtered) == 1
        assert filtered[0]["endpoint"] == "ep-a"

    def test_get_stats(self, logger):
        now = time.time()
        for i in range(3):
            logger.log(
                {
                    "timestamp": now - 100,
                    "event": "extract_success",
                    "endpoint": "test",
                    "memories_extracted": 2,
                    "input_tokens": 500,
                    "output_tokens": 300,
                }
            )
        logger.log({"timestamp": now - 50, "event": "extract_failed", "endpoint": "test"})
        stats = logger.get_stats(period="week")
        assert stats["total_calls"] == 4
        assert stats["success_count"] == 3
        assert stats["failed_count"] == 1
        assert stats["success_rate"] == 75.0
        assert stats["total_cards"] == 6
        assert stats["total_tokens"] == (500 + 300) * 3  # 2400

    def test_get_trend(self, logger):
        now = time.time()
        logger.log({"timestamp": now - 86400, "event": "extract_success", "endpoint": "x"})
        logger.log({"timestamp": now - 2 * 86400, "event": "extract_success", "endpoint": "x"})
        trend = logger.get_trend(days=7)
        assert len(trend) == 7
        counts = [d["count"] for d in trend]
        assert sum(counts) == 2

    def test_empty_log_returns_defaults(self, logger):
        stats = logger.get_stats()
        assert stats["total_calls"] == 0
        assert stats["success_rate"] == 0

    def test_stats_api_routes(self):
        from memos.web.app import app

        routes = [r.path for r in app.routes]
        assert "/api/stats/usage" in routes
        assert "/api/stats/trend" in routes
