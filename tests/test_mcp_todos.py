"""Phase 2 R2: MCP list_todos / update_todo / create_todo 工具测试"""

import json
from unittest import mock

import pytest

# 直接从模块导入工具函数（FastMCP @tool() 返回原始函数）
from memos.server.mcp import create_todo, list_todos, update_todo


class TestMcpListTodos:
    """验证 list_todos MCP 工具"""

    def test_list_todos_default_filter(self):
        """默认按 pending 过滤"""
        fm = mock.Mock()
        fm.list_todos.return_value = []
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            result = list_todos()
            assert result == "暂无待办事项。"

    def test_list_todos_with_data(self):
        """有待办时返回 JSON"""
        fm = mock.Mock()
        fm.list_todos.return_value = [
            {
                "id": "todo-001",
                "document": "测试待办",
                "metadata": {
                    "type": "todo",
                    "todo_status": "pending",
                    "priority": "high",
                    "timestamp": 1000,
                },
            }
        ]
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            result = list_todos()
            data = json.loads(result)
            assert data["total"] == 1
            assert data["todos"][0]["content"] == "测试待办"
            assert data["todos"][0]["todo_status"] == "pending"

    def test_list_todos_filter_by_status(self):
        """按状态过滤"""
        fm = mock.Mock()
        fm.list_todos.return_value = []
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            result = list_todos(todo_status="completed")
            fm.list_todos.assert_called_once()
            kwargs = fm.list_todos.call_args[1]
            assert kwargs["todo_status"] == "completed"

    def test_list_todos_invalid_status(self):
        """无效状态返回错误"""
        result = list_todos(todo_status="invalid")
        assert "无效" in result


class TestMcpUpdateTodo:
    """验证 update_todo MCP 工具"""

    def test_update_todo_success(self):
        """成功的状态变更"""
        fm = mock.Mock()
        fm.get_memory.return_value = {
            "id": "todo-001",
            "document": "测试",
            "metadata": {
                "type": "todo",
                "todo_status": "pending",
                "status_history": "[]",
            },
        }
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            result = update_todo("todo-001", "in_progress")
            assert "pending" in result
            assert "in_progress" in result
            fm.update_memory.assert_called_once()

    def test_update_todo_nonexistent(self):
        """不存在的待办返回错误"""
        fm = mock.Mock()
        fm.get_memory.return_value = None
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            result = update_todo("nonexistent", "completed")
            assert "不存在" in result

    def test_update_todo_not_todo_type(self):
        """非 todo 类型返回错误"""
        fm = mock.Mock()
        fm.get_memory.return_value = {
            "id": "mem-001",
            "document": "测试",
            "metadata": {"type": "fact"},
        }
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            result = update_todo("mem-001", "completed")
            assert "不是待办类型" in result

    def test_update_todo_invalid_transition(self):
        """非法转换返回错误"""
        fm = mock.Mock()
        fm.get_memory.return_value = {
            "id": "todo-001",
            "document": "测试",
            "metadata": {
                "type": "todo",
                "todo_status": "completed",
                "status_history": "[]",
            },
        }
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            result = update_todo("todo-001", "in_progress")
            assert "无法从" in result
            fm.update_memory.assert_not_called()

    def test_update_todo_invalid_status_value(self):
        """无效状态值返回错误"""
        result = update_todo("todo-001", "invalid")
        assert "无效" in result

    def test_update_todo_empty_id(self):
        """空 ID 返回错误"""
        result = update_todo("", "pending")
        assert "不能为空" in result


class TestMcpCreateTodo:
    """MCP create_todo 工具测试（v0.4.8 新增）"""

    def test_create_todo_success(self):
        """成功创建待办，验证 metadata 完整写入"""
        fm = mock.Mock()
        fm.remember.return_value = "todo-001"
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            result = create_todo(content="测试待办", priority="high")
            data = json.loads(result)
            assert data["id"] == "todo-001"
            assert data["message"] == "待办已创建"
            fm.remember.assert_called_once()
            args, kwargs = fm.remember.call_args
            assert args[0] == "测试待办"
            meta = kwargs["metadata"]
            assert meta["type"] == "todo"
            assert meta["todo_status"] == "pending"
            assert meta["priority"] == "high"
            assert meta["active"] is True
            assert meta["source"] == "mcp"
            assert json.loads(meta["status_history"]) == []

    def test_create_todo_empty_content(self):
        """content 为空时返回错误"""
        result = create_todo(content="")
        assert "不能为空" in result

        result = create_todo(content="   ")
        assert "不能为空" in result

    def test_create_todo_default_priority(self):
        """默认 priority 为 medium"""
        fm = mock.Mock()
        fm.remember.return_value = "todo-002"
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            create_todo(content="默认优先级待办")
            _, kwargs = fm.remember.call_args
            assert kwargs["metadata"]["priority"] == "medium"

    def test_create_todo_invalid_priority(self):
        """无效 priority 返回错误"""
        result = create_todo(content="测试", priority="urgent")
        assert "无效" in result
        assert "high" in result or "medium" in result or "low" in result

    def test_create_todo_with_due_date(self):
        """含 due_date 时写入 metadata"""
        fm = mock.Mock()
        fm.remember.return_value = "todo-003"
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            create_todo(content="带截止日期的待办", due_date="2026-06-15")
            _, kwargs = fm.remember.call_args
            assert kwargs["metadata"]["due_date"] == "2026-06-15"

    def test_create_todo_without_due_date(self):
        """无 due_date 时不写入 metadata"""
        fm = mock.Mock()
        fm.remember.return_value = "todo-004"
        with mock.patch("memos.server.mcp._get_memory", return_value=fm):
            create_todo(content="不带动截止日期的待办")
            _, kwargs = fm.remember.call_args
            assert "due_date" not in kwargs["metadata"]
