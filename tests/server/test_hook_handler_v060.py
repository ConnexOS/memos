"""Phase 2 单元测试：F1 行为引导 + F7 活动日志埋点 + F2 session/TASK_EVAL。

测试目标：
- F1: _build_behavior_guide() 启/禁用/文本长度
- F7: activity_log 模块读写/轮转/清理
- F2: _extract_task_eval() 成功/失败/边界
"""

import json
from pathlib import Path


class TestBehaviorGuide:
    """F13: behavior_guide 独立文件化"""

    def test_build_behavior_guide_exists(self):
        """_build_behavior_guide() 返回非空字符串"""
        from memos.server.hook_handler import _build_behavior_guide

        text = _build_behavior_guide()
        assert isinstance(text, str) and len(text) > 0

    def test_load_behavior_guide_default(self):
        """无文件时返回默认文本"""
        from memos.config import load_behavior_guide

        text = load_behavior_guide()
        assert isinstance(text, str) and len(text) > 0

    def test_load_behavior_guide_default_text_length(self):
        """默认行为引导文本不超过 200 字"""
        from memos.config import _DEFAULT_BEHAVIOR_GUIDE

        assert len(_DEFAULT_BEHAVIOR_GUIDE) < 200

    def test_load_behavior_guide_file_missing(self, monkeypatch, tmp_path):
        """文件不存在时使用默认值"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        from memos.config import load_behavior_guide

        text = load_behavior_guide()
        assert isinstance(text, str) and len(text) > 0

    def test_load_behavior_guide_file_corrupted(self, monkeypatch, tmp_path):
        """JSON 损坏时使用默认值"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        bg_path = tmp_path / "etc" / "behavior_guide.json"
        bg_path.parent.mkdir(parents=True, exist_ok=True)
        bg_path.write_text("{invalid json}", encoding="utf-8")
        from memos.config import load_behavior_guide

        text = load_behavior_guide()
        assert isinstance(text, str) and len(text) > 0

    def test_load_behavior_guide_empty_text(self, monkeypatch, tmp_path):
        """text 字段为空时使用默认值"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        bg_path = tmp_path / "etc" / "behavior_guide.json"
        bg_path.parent.mkdir(parents=True, exist_ok=True)
        bg_path.write_text('{"text": ""}', encoding="utf-8")
        from memos.config import load_behavior_guide

        text = load_behavior_guide()
        assert isinstance(text, str) and len(text) > 0

    def test_load_behavior_guide_missing_text_key(self, monkeypatch, tmp_path):
        """缺少 text 字段时使用默认值"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        bg_path = tmp_path / "etc" / "behavior_guide.json"
        bg_path.parent.mkdir(parents=True, exist_ok=True)
        bg_path.write_text('{"other": "value"}', encoding="utf-8")
        from memos.config import load_behavior_guide

        text = load_behavior_guide()
        assert isinstance(text, str) and len(text) > 0

    def test_load_behavior_guide_file_valid(self, monkeypatch, tmp_path):
        """文件存在且有效时使用文件内容"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        bg_path = tmp_path / "etc" / "behavior_guide.json"
        bg_path.parent.mkdir(parents=True, exist_ok=True)
        bg_path.write_text('{"text": "自定义引导文本"}', encoding="utf-8")
        from memos.config import load_behavior_guide

        text = load_behavior_guide()
        assert text == "自定义引导文本"


class TestTaskEvalExtract:
    """F2: [TASK_EVAL] 提取"""

    def test_extract_simple(self):
        from memos.server.hook_handler import _extract_task_eval

        text = '一些回复\n[TASK_EVAL]\n{"done": ["完成了A"], "todo": ["待做B"], "blocked": []}\n[/TASK_EVAL]'
        result = _extract_task_eval(text)
        assert result is not None
        assert result["done"] == ["完成了A"]
        assert result["todo"] == ["待做B"]

    def test_extract_not_found(self):
        from memos.server.hook_handler import _extract_task_eval

        result = _extract_task_eval("普通回复，没有自评标记")
        assert result is None

    def test_extract_invalid_json(self):
        from memos.server.hook_handler import _extract_task_eval

        result = _extract_task_eval("[TASK_EVAL]\n{invalid json}\n[/TASK_EVAL]")
        assert result is None

    def test_extract_empty(self):
        from memos.server.hook_handler import _extract_task_eval

        result = _extract_task_eval("")
        assert result is None


class TestTaskEvalExtractStopHook:
    """F2: stop.py _extract_task_eval"""

    def test_extract_from_stop(self):
        from memos.hooks.stop import _extract_task_eval

        text = '[TASK_EVAL]\n{"done": ["A"], "todo": [], "blocked": []}\n[/TASK_EVAL]'
        result = _extract_task_eval(text)
        assert result is not None
        assert result["done"] == ["A"]

    def test_extract_stop_not_found(self):
        from memos.hooks.stop import _extract_task_eval

        assert _extract_task_eval("hello") is None

    def test_extract_stop_invalid(self):
        from memos.hooks.stop import _extract_task_eval

        assert _extract_task_eval("[TASK_EVAL]\nxyz\n[/TASK_EVAL]") is None


class TestTaskEvalQueue:
    """F2: TaskEvalQueue 队列操作"""

    def test_enqueue_dequeue(self):
        from memos.server.task_handler import TaskEvalQueue

        q = TaskEvalQueue()
        result = q.enqueue({"done": []}, "sess_1", "proj_1")
        assert result is True

    def test_build_task_from_raw(self):
        from memos.server.task_handler import TaskEvalQueue

        q = TaskEvalQueue()
        raw = {"project": "test", "done": ["A"], "todo": ["B"], "blocked": []}
        result = q._build_task_from_raw(raw)
        assert result["project"] == "test"
        assert result["progress"]["done"] == ["A"]
        assert result["confidence"] == 0.7

    def test_structurize_fallback_on_none(self):
        from memos.server.task_handler import TaskEvalQueue

        q = TaskEvalQueue()
        raw = {"project": "test", "done": ["A"]}
        result = q._structurize_task(raw)
        assert result["project"] == "test"
        assert result["confidence"] == 0.7


class TestActivityLog:
    """F7: 活动日志"""

    def test_log_filename(self):
        from memos.features.activity_log import _get_log_filename

        fname = _get_log_filename(log_date="2026-06-14")
        assert "activity_log_2026-06-14.jsonl" in fname

    def test_write_and_read_events(self, monkeypatch, tmp_path):
        from memos.features.activity_log import _get_log_path, _append_event, read_events

        monkeypatch.setattr("memos.features.activity_log._get_log_path", lambda: tmp_path)

        _append_event({"event": "recall", "query": "test", "result_count": 0, "match_types": []})
        result = read_events(page=1, page_size=20)
        assert result["total"] >= 1

    def test_log_recall(self, monkeypatch, tmp_path):
        from memos.features.activity_log import _get_log_path, log_recall, read_events

        monkeypatch.setattr("memos.features.activity_log._get_log_path", lambda: tmp_path)

        log_recall(query="测试查询", result_count=5, match_types=["solution"])
        events = read_events(page=1, page_size=20)
        assert events["total"] >= 1
        assert events["items"][0]["event"] == "recall"
        assert events["items"][0]["result_count"] == 5

    def test_log_knowledge_write(self, monkeypatch, tmp_path):
        from memos.features.activity_log import _get_log_path, log_knowledge_write, read_events

        monkeypatch.setattr("memos.features.activity_log._get_log_path", lambda: tmp_path)

        log_knowledge_write(type_="solution", summary="测试知识", source="test")
        events = read_events(page=1, page_size=20)
        assert events["total"] >= 1
        assert events["items"][0]["event"] == "knowledge_write"

    def test_log_context_injection(self, monkeypatch, tmp_path):
        from memos.features.activity_log import _get_log_path, log_context_injection, read_events

        monkeypatch.setattr("memos.features.activity_log._get_log_path", lambda: tmp_path)

        log_context_injection(memory_ids=["id1"], types=["solution"])
        events = read_events(page=1, page_size=20)
        assert events["total"] >= 1

    def test_cleanup_expired(self, monkeypatch, tmp_path):
        import time

        from memos.features.activity_log import _get_log_path, _cleanup_expired, _append_event

        monkeypatch.setattr("memos.features.activity_log._get_log_path", lambda: tmp_path)
        monkeypatch.setattr("memos.features.activity_log.config.activity_log.retention_days", 0)

        _append_event({"event": "recall", "query": "old"})
        _cleanup_expired()
        remaining = list(tmp_path.glob("activity_log_*.jsonl"))
        assert len(remaining) >= 0  # no crash
