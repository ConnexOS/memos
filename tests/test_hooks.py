"""测试 Hook 模块和 CLI 安装/卸载。"""

import json
import os
import tempfile
from pathlib import Path


class TestHookConfig:
    """测试 Hook 配置生成和 settings.json 写入。"""

    def test_make_hook_config_structure(self):
        """_make_hook_config 返回正确结构。"""
        from memos.cli.dispatch import _make_hook_config

        config = _make_hook_config()
        assert "UserPromptSubmit" in config
        assert "Stop" in config
        assert "memos.hooks.prompt" in config["UserPromptSubmit"][0]["hooks"][0]["command"]
        assert "memos.hooks.stop" in config["Stop"][0]["hooks"][0]["command"]

    def test_install_creates_settings_file(self, monkeypatch):
        """install 创建新的 settings.json。"""
        tmp = tempfile.mkdtemp(prefix="hook-test-")
        try:
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", tmp)
            from memos.cli.dispatch import _install_hooks, _get_settings_path

            _install_hooks(global_mode=False)

            settings_path = _get_settings_path(global_mode=False)
            assert settings_path.exists()
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            assert "hooks" in data
            assert "UserPromptSubmit" in data["hooks"]
            assert "Stop" in data["hooks"]
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_uninstall_removes_memos_hooks(self, monkeypatch):
        """uninstall 移除 memos hooks 但保留其他配置。"""
        tmp = tempfile.mkdtemp(prefix="hook-test-")
        try:
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", tmp)
            from memos.cli.dispatch import _install_hooks, _uninstall_hooks, _get_settings_path

            _install_hooks(global_mode=False)

            settings_path = _get_settings_path(global_mode=False)
            # 添加一个非 memos 的配置
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            data["hooks"]["UserPromptSubmit"].append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "echo hello",
                            "timeout": 5,
                        }
                    ]
                }
            )
            data["some_other_key"] = "keep_me"
            settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

            _uninstall_hooks(global_mode=False)

            result = json.loads(settings_path.read_text(encoding="utf-8"))
            # memos hook 已移除，但其他配置保留
            prompt_hooks = result["hooks"]["UserPromptSubmit"]
            memos_commands = [h.get("command") for e in prompt_hooks for h in e.get("hooks", [])]
            assert "python -m memos.hooks.prompt" not in memos_commands
            assert "echo hello" in memos_commands
            assert result["some_other_key"] == "keep_me"
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_status_shows_not_installed(self, monkeypatch, capsys):
        """status 在未安装时正确提示。"""
        tmp = tempfile.mkdtemp(prefix="hook-test-")
        try:
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", tmp)
            from memos.cli.dispatch import _hook_status

            _hook_status(global_mode=False)
            captured = capsys.readouterr()
            assert "未安装" in captured.out or "不存在" in captured.out
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_status_shows_installed(self, monkeypatch, capsys):
        """status 在已安装时正确显示。"""
        tmp = tempfile.mkdtemp(prefix="hook-test-")
        try:
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", tmp)
            from memos.cli.dispatch import _install_hooks, _hook_status

            _install_hooks(global_mode=False)
            _hook_status(global_mode=False)
            captured = capsys.readouterr()
            assert "已安装" in captured.out
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_idempotent_install(self, monkeypatch):
        """重复安装不会产生重复配置。"""
        tmp = tempfile.mkdtemp(prefix="hook-test-")
        try:
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", tmp)
            from memos.cli.dispatch import _install_hooks, _get_settings_path

            _install_hooks(global_mode=False)
            _install_hooks(global_mode=False)
            _install_hooks(global_mode=False)

            data = json.loads(_get_settings_path(global_mode=False).read_text(encoding="utf-8"))
            # 每个事件只有一个 hook 条目
            assert len(data["hooks"]["UserPromptSubmit"]) == 1
            assert len(data["hooks"]["Stop"]) == 1
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_global_mode_uses_home(self, monkeypatch):
        """--global 模式使用 ~/.claude/settings.json。"""
        from memos.cli.dispatch import _get_settings_path

        project_path = _get_settings_path(global_mode=False)
        global_path = _get_settings_path(global_mode=True)
        assert global_path == Path.home() / ".claude" / "settings.json"
        assert project_path != global_path


class TestHookModules:
    """测试 hooks/prompt.py 和 hooks/stop.py 模块可导入。"""

    def test_prompt_module_imports(self):
        """prompt hook 模块可正常导入。"""
        from memos.hooks import prompt

        assert hasattr(prompt, "main")
        assert hasattr(prompt, "_build_context")

    def test_stop_module_imports(self):
        """stop hook 模块可正常导入。"""
        from memos.hooks import stop

        assert hasattr(stop, "main")

    def test_prompt_build_context_empty(self):
        """_build_context 在无记忆时返回空字符串。"""
        from memos.hooks.prompt import _build_context

        result = _build_context(None, "test", "pid")
        assert result == ""

    def test_stop_main_rejects_stop_hook_active(self, monkeypatch):
        """stop hook 的 stop_hook_active 防护正确触发。"""
        from unittest import mock

        from memos.hooks.stop import main as stop_main

        fake_input = json.dumps({"stop_hook_active": True, "last_assistant_message": "test"}).encode("utf-8")
        # 使用 mock.patch 避免 pytest capture stdin 冲突
        with mock.patch("sys.stdin.buffer.read", return_value=fake_input):
            stop_main()


class TestHookTiming:
    """T5: Hook 性能基线 — hook_timing 日志格式。"""

    def test_hook_timing_format_in_main(self, caplog):
        """main() 产生 hook_timing 单行日志，含 5 段 key=value 格式。"""
        caplog.set_level("INFO", logger="memos.hooks.prompt")
        from unittest import mock

        # Mock stdin 输入和 _get_memory（返回 None 跳过 ChromaDB）
        fake_input = json.dumps({"prompt": "测试消息"}).encode("utf-8")
        with mock.patch("sys.stdin.buffer.read", return_value=fake_input):
            with mock.patch("memos.hooks.prompt._get_memory", return_value=None):
                from memos.hooks.prompt import main
                main()

        # 验证 hook_timing 日志行存在
        timing_lines = [r for r in caplog.records if "hook_timing:" in r.getMessage()]
        assert len(timing_lines) >= 1, "应有 hook_timing 日志行"
        msg = timing_lines[0].getMessage()
        assert "stdin=" in msg
        assert "pipe1=" in msg
        assert "pipe2=" in msg
        assert "pipe3=" in msg
        assert "output=" in msg
        assert "total=" in msg
        # 验证单位 ms
        assert "ms" in msg


class TestFifoCleanup:
    """T7: FIFO 清理 — 已反馈建议不参与清理。"""

    def test_fifo_skips_reacted(self):
        """FIFO 清理仅淘汰 pending，reacted 记录不变。"""
        from unittest import mock

        from memos.hooks.prompt import _fifo_cleanup

        mem = mock.MagicMock()
        cfg = mock.MagicMock()
        cfg.suggestion_max_pending = 3

        # 4 条 pending → 超过 max_pending=3，触发清理
        mem.store.count.return_value = 4
        mem.store.get.return_value = {
            "ids": [
                "pending-1", "pending-2", "pending-3", "pending-4",
            ],
            "metadatas": [
                {"status": "pending", "suggestion_type": "active_push", "type": "suggestion", "project_id": "default"},
                {"status": "pending", "suggestion_type": "active_push", "type": "suggestion", "project_id": "default"},
                {"status": "pending", "suggestion_type": "active_push", "type": "suggestion", "project_id": "default"},
                {"status": "pending", "suggestion_type": "active_push", "type": "suggestion", "project_id": "default"},
            ],
        }

        _fifo_cleanup(mem, "default", cfg)

        # 验证只清理了 2 条（4 - 3 + 1 = 2），且全部被标记为 dismissed
        update_call = mem.store.update.call_args
        assert update_call is not None
        dismissed_ids = update_call[1]["ids"]
        dismissed_metas = update_call[1]["metadatas"]
        assert len(dismissed_ids) == 2
        assert all(m["status"] == "dismissed" for m in dismissed_metas)

    def test_fifo_reacted_untouched(self):
        """有 reacted 记录时，FIFO 计数不受影响（仅统计 pending）。"""
        from unittest import mock

        from memos.hooks.prompt import _fifo_cleanup

        mem = mock.MagicMock()
        cfg = mock.MagicMock()
        cfg.suggestion_max_pending = 3

        # 2 条 pending + 5 条 reacted → pending=2 < max_pending=3 → 不触发清理
        mem.store.count.return_value = 2  # 仅统计 pending

        _fifo_cleanup(mem, "default", cfg)

        # 不触发清理 → store.update 不被调用
        assert not mem.store.update.called
