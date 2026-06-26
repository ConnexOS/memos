"""F6: 简报生成测试。"""

from memos.features.briefing import build_fallback_briefing, build_full_briefing


class TestFallbackBriefing:
    """兜底简报测试。"""

    def test_no_task(self):
        result = build_fallback_briefing(today_task=None, today_events=[])
        assert result["quality"] == "simple"
        assert result["source"] == "lazy_hook"
        assert "今日无活跃任务记录" in result["summary"]

    def test_with_task(self):
        task = {
            "project": "test",
            "goal": "测试目标",
            "progress": {"done": ["A"], "todo": ["B"], "blocked": []},
            "next_steps": ["完成B"],
        }
        result = build_fallback_briefing(today_task=task, today_events=[], event_count=10)
        assert result["quality"] == "simple"
        assert result["source"] == "lazy_hook"
        assert "10 轮对话" in result["summary"]
        assert result["task_status"] == "已完成: 1/2"
        assert result["plan_tomorrow"] == "无"

    def test_with_events(self):
        events = [
            {"timestamp": 1000, "event": "recall", "query": "报错"},
            {"timestamp": 2000, "event": "knowledge_write", "type": "solution"},
        ]
        result = build_fallback_briefing(today_task=None, today_events=events)
        assert result["quality"] == "simple"
        assert result["source"] == "lazy_hook"
        assert "今日无活跃任务记录" in result["summary"]
        assert result["key_events"] == []


class TestFullBriefing:
    """完整简报测试。"""

    def test_llm_failure_returns_none(self):
        result = build_full_briefing({}, [], [], "", "", lambda s, u: None)
        assert result is None


class TestTaskInjectionIntegration:
    """F5 集成测试：确认 additionalContext 包含 task 注入"""

    def test_format_context_includes_task(self):
        from memos.server.hook_handler import _format_additional_context

        class MockMemory:
            def list_memories(self, **kwargs):
                if kwargs.get("type_filter") == "task":
                    return [{
                        "id": "t1",
                        "document": '{"project":"p","goal":"g","progress":{"done":["x"],"todo":[],"blocked":[]}}',
                        "metadata": {"status": "active", "paused": False},
                    }]
                return []

        result = _format_additional_context(suggestions=[], project_id="test-pid", mem=MockMemory())
        assert "[当前任务]" in result
