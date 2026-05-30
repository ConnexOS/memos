"""测试 F1 - 安装向导（InitWizard 状态机 + --non-interactive + --force）"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from memos.config import MemoConfig, ensure_memos_home, get_memos_home


@pytest.fixture
def temp_cfg(monkeypatch):
    """提供临时隔离的 MemoConfig + MEMOS_HOME。"""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "etc" / "prompts").mkdir(parents=True, exist_ok=True)
        (home / "etc").mkdir(parents=True, exist_ok=True)
        (home / "model" / "bge-large-zh-v1.5").mkdir(parents=True, exist_ok=True)
        # 写入最小合法配置
        cfg_data = {
            "chroma": {"mode": "persistent", "path": str(home / "memdb")},
            "model": {"path": str(home / "model" / "bge-large-zh-v1.5")},
            "llm": {"endpoints": [{"name": "default"}], "active": "default"},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        config_file = home / "etc" / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(cfg_data, indent=2, ensure_ascii=False))

        monkeypatch.setenv("MEMOS_HOME", str(home))
        cfg = MemoConfig.load()
        # 清除 init 状态文件
        state_file = home / "etc" / ".init_state.json"
        if state_file.exists():
            state_file.unlink()
        yield cfg, home


class TestInitWizardSteps:
    """各步骤方法独立测试"""

    def test_step_1_environment_check(self, temp_cfg):
        """步骤 1: 环境检测总是返回 True（不阻塞）"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        wizard = InitWizard(cfg, force=False, home=home)
        result = wizard._step_1()
        assert result is True

    def test_step_2_select_model_default(self, temp_cfg, monkeypatch):
        """步骤 2: 默认选择 bge-large"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        wizard = InitWizard(cfg, force=False, home=home)

        with mock.patch("builtins.input", return_value=""):
            result = wizard._step_2()
        assert result is True
        assert wizard._state["model_name"] == "bge-large-zh-v1.5"

    def test_step_2_select_minilm(self, temp_cfg, monkeypatch):
        """步骤 2: 选择 miniLM"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        wizard = InitWizard(cfg, force=False, home=home)

        with mock.patch("builtins.input", return_value="2"):
            result = wizard._step_2()
        assert result is True
        assert wizard._state["model_name"] == "all-MiniLM-L6-v2"

    def test_step_3_skip_llm_config(self, temp_cfg, monkeypatch):
        """步骤 3: 跳过 LLM 配置（空输入）"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        wizard = InitWizard(cfg, force=False, home=home)

        with mock.patch("builtins.input", return_value=""):
            result = wizard._step_3()
        assert result is True

    def test_step_3_config_ollama_template(self, temp_cfg, monkeypatch):
        """步骤 3: 选择 Ollama 模板"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        wizard = InitWizard(cfg, force=False, home=home)

        inputs = ["1", "", "qwen2.5"]  # Ollama, no key, model qwen2.5
        input_iter = iter(inputs)

        with mock.patch("builtins.input", side_effect=lambda *a: next(input_iter, "")):
            result = wizard._step_3()
        assert result is True
        assert cfg.llm.endpoints[0].api_base == "http://localhost:11434/v1"

    def test_step_5_generates_token(self, temp_cfg, monkeypatch):
        """步骤 5: 生成认证 Token"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        wizard = InitWizard(cfg, force=False, home=home)

        result = wizard._step_5()
        assert result is True
        assert cfg.auth.token_hash != ""
        assert cfg.auth.secret_key != ""
        assert wizard._state.get("token_generated") is True


class TestInitWizardState:
    """中断恢复机制测试"""

    def test_state_save_and_load(self, temp_cfg):
        """保存状态后重新加载"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        wizard = InitWizard(cfg, force=False, home=home)
        wizard._state["model_name"] = "all-MiniLM-L6-v2"
        wizard._state["step_1"] = True
        wizard._save_state()

        wizard2 = InitWizard(cfg, force=False, home=home)
        assert wizard2._state["model_name"] == "all-MiniLM-L6-v2"
        assert wizard2._state["step_1"] is True

    def test_force_mode_clears_no_state(self, temp_cfg, monkeypatch):
        """force 模式不依赖状态文件"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("memos.storage.embeddings.download_model", lambda *a, **kw: True)
        monkeypatch.setattr("memos.storage.embeddings.model_exists", lambda p: True)

        wizard = InitWizard(cfg, force=True, home=home)
        # force 模式不应抛出异常
        result = wizard._run_force_mode()
        assert result is True


class TestNonInteractive:
    """--non-interactive 模式"""

    def test_non_interactive_from_config(self, temp_cfg, monkeypatch, tmp_path):
        """从 JSON 配置文件全自动初始化"""
        # 创建 init 配置文件
        init_config = {
            "model_name": "all-MiniLM-L6-v2",
            "llm": {
                "name": "default",
                "api_base": "http://localhost:11434/v1",
                "api_key": "",
                "model": "qwen2.5",
            },
        }
        config_file = tmp_path / "init.json"
        config_file.write_text(json.dumps(init_config, indent=2, ensure_ascii=False))

        from memos.cli.dispatch import _init_non_interactive

        cfg, home = temp_cfg

        # Mock 模型下载
        monkeypatch.setattr("memos.storage.embeddings.download_model", lambda *a, **kw: True)
        monkeypatch.setattr("memos.storage.embeddings.model_exists", lambda p: True)

        _init_non_interactive(cfg, home, str(config_file))

        assert cfg.llm.endpoints[0].api_base == "http://localhost:11434/v1"
        assert cfg.llm.endpoints[0].model == "qwen2.5"

    def test_non_interactive_file_not_found(self, temp_cfg):
        """配置文件不存在时退出"""
        from memos.cli.dispatch import _init_non_interactive

        cfg, home = temp_cfg
        with pytest.raises(SystemExit):
            _init_non_interactive(cfg, home, "/nonexistent/init.json")


class TestForceMode:
    """--force 模式"""

    def test_force_mode_completes(self, temp_cfg, monkeypatch):
        """force 模式正常完成（mock 模型下载）"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        # Mock 模型已就绪
        monkeypatch.setattr("memos.storage.embeddings.model_exists", lambda p: True)

        wizard = InitWizard(cfg, force=True, home=home)
        result = wizard._run_force_mode()
        assert result is True

    def test_force_mode_generates_token_and_config(self, temp_cfg, monkeypatch):
        """force 模式写入完整配置"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("memos.storage.embeddings.model_exists", lambda p: True)

        wizard = InitWizard(cfg, force=True, home=home)
        wizard._run_force_mode()

        # 配置已持久化
        config_file = home / "etc" / "config.json"
        assert config_file.exists()
        reloaded = MemoConfig.load()
        assert reloaded.auth.token_hash != ""
        assert reloaded.auth.secret_key != ""


class TestNonTTY:
    """非交互式终端降级"""

    def test_non_tty_falls_back_to_force(self, temp_cfg, monkeypatch):
        """非 TTY 自动降级为 force 模式"""
        from memos.features.wizard import InitWizard

        cfg, home = temp_cfg
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)  # 非 TTY
        monkeypatch.setattr("memos.storage.embeddings.model_exists", lambda p: True)

        wizard = InitWizard(cfg, force=False, home=home)
        result = wizard.run()
        assert result is True


class TestCmdInit:
    """cmd_init CLI 入口"""

    def test_cmd_init_force(self, temp_cfg, monkeypatch):
        """memos init --force 正常完成"""
        import memos.config as cfg_mod
        from memos.cli.dispatch import cmd_init

        cfg, home = temp_cfg
        monkeypatch.setattr(cfg_mod, "config", cfg)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("memos.storage.embeddings.model_exists", lambda p: True)
        monkeypatch.setattr("memos.config.ensure_memos_home", lambda: home)

        from unittest.mock import MagicMock

        args = MagicMock()
        args.force = True
        args.model_path = None
        args.migrate_from = None
        args.non_interactive = None

        cmd_init(args)
