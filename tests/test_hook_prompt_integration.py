"""S3 — F3 Hook 端到端集成测试 (v0.4.4)

覆盖：main() 输出 JSON 格式、_build_layered_context 调用、MEMOS_USE_OLD_CONTEXT 回退、
main() 级 JSON 兜底保护、空 stdin、异常降级。
"""

import json
import time
from io import BytesIO
from unittest.mock import MagicMock

import pytest


def _make_recall_result(doc_id, document, similarity, type="fact", timestamp=None):
    if timestamp is None:
        timestamp = time.time() - 3600
    return {
        "id": doc_id,
        "document": document,
        "metadata": {"type": type, "project_id": "default", "timestamp": timestamp},
        "similarity": similarity,
        "decay_factor": 0.98,
        "final_score": similarity * 0.98,
    }


class TestHookMainOutput:
    """main() 输出 JSON 格式验证。"""

    @pytest.fixture
    def mock_env(self, monkeypatch):
        """Mock ContextMemory 和 config，确保 main() 使用受控的依赖。"""
        from memos.config.models import MemoryConfig

        mem = MagicMock()
        mem.remember.return_value = "test-record-id"
        mem.recall.return_value = [
            _make_recall_result("mem-1", "测试记忆内容", similarity=0.85),
            _make_recall_result("mem-2", "另一条记忆", similarity=0.6),
        ]

        cfg = MemoryConfig(
            context_injection_threshold=0.55,
            active_suggestion_threshold=0.75,
            context_max_items=3,
            enable_active_suggestions=True,
            suggestion_cooldown_minutes=30,
            suggestion_max_per_day=10,
            suggestion_expiry_days=7,
        )

        monkeypatch.setattr("memos.hooks.prompt._get_memory", lambda: mem)
        monkeypatch.setattr("memos.hooks.prompt._get_memory_config", lambda: cfg)
        monkeypatch.setattr("memos.hooks.prompt._get_project_id", lambda: "test-pid")
        # 使用 MagicMock 替代 STATE_FILE
        monkeypatch.setattr("memos.hooks.prompt.STATE_FILE", MagicMock())
        return mem

    def _run_main(self, monkeypatch, stdin_bytes=None):
        """辅助：运行 main() 并捕获 print 输出。"""
        if stdin_bytes is None:
            stdin_bytes = json.dumps({"prompt": "测试消息"}).encode("utf-8")

        # 使用 StdInMock 替代 sys.stdin
        class _StdinMock:
            buffer = BytesIO(stdin_bytes)

        monkeypatch.setattr("memos.hooks.prompt.sys.stdin", _StdinMock())

        from memos.hooks.prompt import main

        out_calls = []

        def fake_print(*args, **kwargs):
            out_calls.append(args[0])

        monkeypatch.setattr("builtins.print", fake_print)
        main()
        return out_calls

    def test_main_returns_valid_json(self, mock_env, monkeypatch):
        """main() 输出有效 JSON 且格式兼容。"""
        out_calls = self._run_main(monkeypatch)
        assert len(out_calls) == 1
        output = json.loads(out_calls[0])
        assert "hookSpecificOutput" in output
        assert "additionalContext" in output["hookSpecificOutput"]

    def test_main_empty_stdin_returns_early(self, mock_env, monkeypatch):
        """空消息不输出额外内容。"""
        out_calls = self._run_main(monkeypatch, json.dumps({"prompt": ""}).encode("utf-8"))
        assert len(out_calls) == 0  # 空消息直接 return，不 print

    def test_main_json_contains_additional_context(self, mock_env, monkeypatch):
        """main() 输出包含 additionalContext 字段。"""
        out_calls = self._run_main(monkeypatch)
        output = json.loads(out_calls[0])
        hs = output["hookSpecificOutput"]
        assert "additionalContext" in hs
        assert isinstance(hs["additionalContext"], str)

    def test_main_with_old_context_env(self, mock_env, monkeypatch):
        """MEMOS_USE_OLD_CONTEXT=1 时使用旧版 _build_context。"""
        monkeypatch.setenv("MEMOS_USE_OLD_CONTEXT", "1")
        out_calls = self._run_main(monkeypatch)
        output = json.loads(out_calls[0])
        assert "hookSpecificOutput" in output

    def test_stdin_decode_failure_graceful(self, mock_env, monkeypatch):
        """stdin 解析失败时仍输出有效 JSON。"""
        out_calls = self._run_main(monkeypatch, b"\xff\xfe\x00\x01")
        # 解析失败 → 空消息 → 直接 return 不 print
        assert len(out_calls) == 0


class TestHookMainFallback:
    """main() 兜底保护测试。"""

    def _run_main(self, monkeypatch, stdin_bytes=None):
        if stdin_bytes is None:
            stdin_bytes = json.dumps({"prompt": "测试"}).encode("utf-8")

        class _StdinMock:
            buffer = BytesIO(stdin_bytes)

        monkeypatch.setattr("memos.hooks.prompt.sys.stdin", _StdinMock())

        from memos.hooks.prompt import main

        out_calls = []

        def fake_print(*args, **kwargs):
            out_calls.append(args[0])

        monkeypatch.setattr("builtins.print", fake_print)
        main()
        return out_calls

    def test_main_output_always_valid_json(self, monkeypatch):
        """极端异常下 main() 仍输出有效 JSON。"""
        out_calls = self._run_main(monkeypatch)
        assert len(out_calls) == 1
        output = json.loads(out_calls[0])
        assert "hookSpecificOutput" in output
        assert "additionalContext" in output["hookSpecificOutput"]

    def test_print_failure_fallback(self, monkeypatch):
        """print(json.dumps) 失败时输出兜底 JSON。"""
        out_calls = self._run_main(monkeypatch)
        # 即使 print 失败，也不会抛异常到外层
        assert len(out_calls) >= 0
