"""冒烟测试：CLI 入口和命令清单验证。"""

import sys
from io import StringIO
from unittest.mock import patch


def _run_memos(args: list[str]) -> str:
    """运行 memos CLI 并捕获输出。"""
    with patch.object(sys, "argv", ["memos"] + args):
        out = StringIO()
        old_out = sys.stdout
        sys.stdout = out
        try:
            try:
                from memos.cli.dispatch import main

                main()
            except SystemExit:
                pass
            return out.getvalue()
        finally:
            sys.stdout = old_out


def test_cli_help_output():
    """验证 --help 基本输出。"""
    output = _run_memos(["--help"])
    assert "usage:" in output or "memos" in output


def test_cli_expected_commands():
    """验证精简后 CLI 命令清单。"""
    output = _run_memos(["--help"])
    expected_commands = {
        "init", "server", "dashboard", "today", "status", "doctor",
        "vacuum", "reindex", "export", "import", "backup", "restore",
        "auth", "hook", "config",
    }
    for cmd in expected_commands:
        assert cmd in output, f"缺少命令: {cmd}"
