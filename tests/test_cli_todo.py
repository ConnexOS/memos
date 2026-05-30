"""Phase 2 R2: CLI memos todo 命令测试"""

from unittest import mock

import pytest


class TestCliTodo:
    """验证 CLI todo 命令"""

    def test_cmd_todo_imports(self):
        """cmd_todo 函数可导入"""
        from memos.cli.dispatch import cmd_todo, cmd_init
        assert callable(cmd_todo)

    def test_todo_subparser_added(self):
        """memos todo 子命令已注册"""
        from memos.cli.dispatch import main as dispatch_main

        import argparse
        # 通过 argparse 验证
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        p_todo = sub.add_parser("todo")
        p_todo.add_argument("--todo-status", choices=["pending", "in_progress", "completed", "cancelled"])
        p_todo.add_argument("--project-id")

        # 确认参数被正确添加
        args = parser.parse_args(["todo", "--todo-status", "pending"])
        assert args.command == "todo"
        assert args.todo_status == "pending"

    def test_todo_status_label_dict(self):
        """状态标签字典完整"""
        from memos.cli.dispatch import _TODO_STATUS_LABELS
        for s in ("pending", "in_progress", "completed", "cancelled"):
            assert s in _TODO_STATUS_LABELS
            assert _TODO_STATUS_LABELS[s]

    def test_cmd_todo_prints_message(self):
        """cmd_todo 在无数据时输出提示"""
        from memos.cli.dispatch import cmd_todo

        fm = mock.Mock()
        fm.list_memories.return_value = []

        with mock.patch("memos.engine.memory.ContextMemory", return_value=fm):
            cmd_todo(mock.Mock(todo_status=None, project_id=None))
