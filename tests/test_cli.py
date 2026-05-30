"""测试 CLI 入口和子命令。"""

import argparse
import os
import tempfile
from pathlib import Path
from unittest import mock


class TestCLIHelp:
    """测试 CLI 帮助输出。"""

    def test_main_shows_help_without_command(self, capsys):
        """无子命令时显示帮助信息。"""
        from memos.cli import main

        with mock.patch.object(argparse._sys, "argv", ["memos"]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 0
        captured = capsys.readouterr()
        assert "init" in captured.out
        assert "server" in captured.out
        assert "dashboard" in captured.out

    def test_init_help(self, capsys):
        """memos init --help 显示帮助。"""
        with mock.patch.object(argparse._sys, "argv", ["memos", "init", "--help"]):
            try:
                from memos.cli import main

                main()
            except SystemExit:
                pass
        captured = capsys.readouterr()
        assert "model-path" in captured.out
        assert "force" in captured.out


class TestCmdInit:
    """测试 memos init 子命令。"""

    def test_init_creates_directories(self, monkeypatch, capsys):
        """init 创建目录结构、写入配置。"""
        tmp = tempfile.mkdtemp(prefix="memos-cli-test-")
        try:
            home_path = os.path.join(tmp, ".memos")
            monkeypatch.setenv("MEMOS_HOME", home_path)
            model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "model", "bge-large-zh-v1.5"))
            # 跳过 LLM 交互
            monkeypatch.setattr("builtins.input", lambda _: "")

            from memos.cli.dispatch import cmd_init

            args = argparse.Namespace(model_path=model_path, force=False, migrate_from=None)
            cmd_init(args)

            # 验证目录创建
            home = Path(home_path)
            assert (home / "etc").is_dir()
            assert (home / "memdb").is_dir()
            assert (home / "model").is_dir()
            assert (home / "etc" / "config.json").exists()
            assert (home / "etc" / "prompts" / "index.json").exists()

            captured = capsys.readouterr()
            assert "初始化完成" in captured.out
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_init_with_model_path_updates_config(self, monkeypatch):
        """--model-path 指定后更新 config.model.path。"""
        tmp = tempfile.mkdtemp(prefix="memos-cli-test-")
        try:
            home_path = os.path.join(tmp, ".memos")
            monkeypatch.setenv("MEMOS_HOME", home_path)
            model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "model", "bge-large-zh-v1.5"))
            monkeypatch.setattr("builtins.input", lambda _: "")

            from memos.cli.dispatch import cmd_init

            args = argparse.Namespace(model_path=model_path, force=False, migrate_from=None)
            cmd_init(args)

            from memos.config import MemoConfig

            cfg = MemoConfig.load()
            assert cfg.model.path == model_path
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_init_force_overwrites_config(self, monkeypatch):
        """--force 覆盖已有配置。"""
        tmp = tempfile.mkdtemp(prefix="memos-cli-test-")
        try:
            home_path = os.path.join(tmp, ".memos")
            monkeypatch.setenv("MEMOS_HOME", home_path)
            model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "model", "bge-large-zh-v1.5"))
            monkeypatch.setattr("builtins.input", lambda _: "")

            from memos.cli.dispatch import cmd_init

            # 第一次 init
            args1 = argparse.Namespace(model_path=model_path, force=False, migrate_from=None)
            cmd_init(args1)

            # 第二次 init with --force
            args2 = argparse.Namespace(model_path=model_path, force=True, migrate_from=None)
            cmd_init(args2)

            # 不应抛异常，配置应可重载
            from memos.config import MemoConfig

            cfg = MemoConfig.load()
            assert cfg.model.path == model_path
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


class TestCmdStatus:
    """测试 memos status 子命令。"""

    def test_status_runs_without_error(self, monkeypatch, capsys):
        """status 命令正常执行不抛异常。"""
        tmp = tempfile.mkdtemp(prefix="memos-cli-test-")
        try:
            home_path = os.path.join(tmp, ".memos")
            monkeypatch.setenv("MEMOS_HOME", home_path)
            # 确保目录存在
            for sub in ["etc", "memdb", "model"]:
                os.makedirs(os.path.join(home_path, sub), exist_ok=True)

            from memos.cli.dispatch import cmd_status

            cmd_status(argparse.Namespace())
            captured = capsys.readouterr()
            assert "MEMOS_HOME" in captured.out
            assert "版本" in captured.out
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


class TestCmdConfig:
    """测试 memos config 子命令。"""

    def test_config_show(self, monkeypatch, capsys):
        """config show 输出当前配置。"""
        tmp = tempfile.mkdtemp(prefix="memos-cli-test-")
        try:
            home_path = os.path.join(tmp, ".memos")
            monkeypatch.setenv("MEMOS_HOME", home_path)
            for sub in ["etc", "memdb", "model"]:
                os.makedirs(os.path.join(home_path, sub), exist_ok=True)

            from memos.cli.dispatch import cmd_config

            class Args:
                action = "show"
                key = None
                value = None

            cmd_config(Args())
            captured = capsys.readouterr()
            assert "chroma.path" in captured.out
            assert "model.path" in captured.out
            assert "llm.active" in captured.out
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_config_set_valid_key(self, monkeypatch, capsys):
        """config set 更新有效配置项。"""
        tmp = tempfile.mkdtemp(prefix="memos-cli-test-")
        try:
            home_path = os.path.join(tmp, ".memos")
            monkeypatch.setenv("MEMOS_HOME", home_path)
            for sub in ["etc", "memdb", "model"]:
                os.makedirs(os.path.join(home_path, sub), exist_ok=True)

            from memos.cli.dispatch import cmd_config

            class Args:
                action = "set"
                key = "llm.temperature"
                value = "0.5"

            cmd_config(Args())
            captured = capsys.readouterr()
            assert "OK" in captured.out or "llm.temperature" in captured.out

            # 验证配置已持久化
            import json

            config_file = Path(home_path) / "etc" / "config.json"
            with open(config_file, encoding="utf-8") as f:
                data = json.load(f)
            assert data["llm"]["temperature"] == 0.5
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_config_set_invalid_key(self, monkeypatch, capsys):
        """config set 无效键应报错。"""
        tmp = tempfile.mkdtemp(prefix="memos-cli-test-")
        try:
            home_path = os.path.join(tmp, ".memos")
            monkeypatch.setenv("MEMOS_HOME", home_path)
            for sub in ["etc", "memdb", "model"]:
                os.makedirs(os.path.join(home_path, sub), exist_ok=True)

            from memos.cli.dispatch import cmd_config

            class Args:
                action = "set"
                key = "nonexistent.field"
                value = "test"

            import sys

            with mock.patch.object(sys, "exit") as mock_exit:
                cmd_config(Args())
                mock_exit.assert_called_once_with(1)
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_config_reload(self, monkeypatch, capsys):
        """config reload 重载配置。"""
        tmp = tempfile.mkdtemp(prefix="memos-cli-test-")
        try:
            home_path = os.path.join(tmp, ".memos")
            monkeypatch.setenv("MEMOS_HOME", home_path)
            for sub in ["etc", "memdb", "model"]:
                os.makedirs(os.path.join(home_path, sub), exist_ok=True)

            from memos.cli.dispatch import cmd_config

            class Args:
                action = "reload"
                key = None
                value = None

            cmd_config(Args())
            captured = capsys.readouterr()
            assert "OK" in captured.out
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


class TestCmdDoctor:
    """测试 memos doctor 子命令。"""

    def test_doctor_runs_without_error(self, monkeypatch, capsys):
        """doctor 命令正常执行。"""
        tmp = tempfile.mkdtemp(prefix="memos-cli-test-")
        try:
            home_path = os.path.join(tmp, ".memos")
            monkeypatch.setenv("MEMOS_HOME", home_path)
            for sub in ["etc", "memdb", "model"]:
                os.makedirs(os.path.join(home_path, sub), exist_ok=True)

            from memos.cli.dispatch import cmd_doctor

            result = cmd_doctor(argparse.Namespace())
            # doctor 返回 None (no issues) 或 1 (issues found)
            # 模型未下载，应该有 issues
            assert result in (None, 0, 1)
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
