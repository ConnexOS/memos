"""F5: L2 task 注入测试。"""

import json


class TestInjectActiveTask:
    """_inject_active_task 单元测试。"""

    def test_no_mem_returns_empty(self):
        from memos.server.hook_handler import _inject_active_task

        result = _inject_active_task(None, "test")
        assert result == ""

    def test_with_data(self, monkeypatch):
        from memos.server.hook_handler import _inject_active_task

        class MockMemory:
            def list_memories(self, **kwargs):
                return [{
                    "id": "task_1",
                    "document": json.dumps({
                        "project": "test-proj",
                        "goal": "测试目标",
                        "progress": {"done": ["完成A"], "todo": ["待做B"], "blocked": []},
                        "next_steps": ["完成B"],
                    }),
                    "metadata": {"status": "active", "paused": False},
                }]

        result = _inject_active_task(MockMemory(), "test-pid")
        assert "[当前任务]" in result
        assert "test-proj" in result
        assert "测试目标" in result
        assert "完成A" in result

    def test_archived_task_skipped(self):
        from memos.server.hook_handler import _inject_active_task

        class MockMemory:
            def list_memories(self, **kwargs):
                return [{
                    "id": "task_1",
                    "document": "{}",
                    "metadata": {"status": "archived", "paused": False},
                }]

        result = _inject_active_task(MockMemory(), "test-pid")
        assert result == ""
