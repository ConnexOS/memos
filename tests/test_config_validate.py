"""测试 F6 - 配置合法性校验（Schema 校验 + 备份恢复 + CLI validate）"""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from memos.config import (
    MemoConfig,
    backup_config,
    ensure_memos_home,
    get_config_schema,
    restore_from_backup,
    validate_config,
)


class TestConfigSchema:
    """JSON Schema 生成与缓存"""

    def test_get_config_schema_returns_valid_schema(self, monkeypatch, tmp_path):
        """生成的 Schema 自身是合法 JSON Schema"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        schema = get_config_schema(force_refresh=True)
        assert schema["type"] == "object"
        assert "properties" in schema
        for name in [
            "chroma",
            "model",
            "llm",
            "memory",
            "buffer",
            "dashboard",
            "server",
            "auth",
            "backup",
            "notification",
        ]:
            assert name in schema["properties"], f"schema 缺少段: {name}"
        # prompt 在 schema 中但为宽松的 object 类型（单独持久化管理）
        assert "prompt" in schema["properties"]
        assert schema["properties"]["prompt"] == {"type": "object"}

    def test_get_config_schema_llm_endpoint_inlined(self, monkeypatch, tmp_path):
        """LLMEndpoint 的 $ref 已被内联，无残留 $ref"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        schema = get_config_schema(force_refresh=True)
        schema_str = json.dumps(schema)
        assert "$ref" not in schema_str, f"schema 中残留 $ref: {schema_str}"

    def test_get_config_schema_cached(self, monkeypatch, tmp_path):
        """第二次调用使用缓存"""
        (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))

        # 初始生成
        schema1 = get_config_schema(force_refresh=True)
        # 应读取缓存（不报错）
        schema2 = get_config_schema()
        assert schema1 == schema2


class TestValidateConfig:
    """validate_config() 校验逻辑"""

    def _make_valid_data(self, tmp_path, **overrides):
        """构造合法配置的最小数据集"""
        etc = tmp_path / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        data = {
            "chroma": {"mode": "persistent", "path": str(tmp_path / "memdb")},
            "model": {"path": str(tmp_path / "model" / "bge-large-zh-v1.5"), "vector_dim": 1024},
            "llm": {"endpoints": [{"name": "default"}], "active": "default"},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
            "auth": {},
        }
        data.update(overrides)
        return data

    def test_valid_config_passes(self, tmp_path):
        """合法配置校验通过（空错误列表）"""
        data = self._make_valid_data(tmp_path)
        errors = validate_config(data)
        assert errors == [], f"合法配置不应有错误，实际: {errors}"

    def test_missing_section_fails(self, tmp_path):
        """缺少必填子配置段应报错"""
        data = self._make_valid_data(tmp_path)
        del data["chroma"]
        errors = validate_config(data)
        assert len(errors) > 0
        assert any("chroma" in e.lower() for e in errors)

    def test_wrong_type_fails(self, tmp_path):
        """字段类型错误应报错"""
        data = self._make_valid_data(tmp_path)
        data["model"]["vector_dim"] = "not_a_number"  # 应为 int
        errors = validate_config(data)
        assert len(errors) > 0

    def test_empty_config_fails(self):
        """空配置应报错"""
        errors = validate_config({})
        assert len(errors) > 0

    def test_extra_section_ignored(self, tmp_path):
        """未知段（如旧版 prompt）不影响校验——Pydantic 默认忽略额外字段"""
        data = self._make_valid_data(tmp_path)
        data["prompt"] = {"templates": []}
        errors = validate_config(data)
        assert errors == []

    def test_llm_endpoints_list_valid(self, tmp_path):
        """LLM endpoints 列表配置校验通过"""
        data = self._make_valid_data(tmp_path)
        data["llm"]["endpoints"] = [
            {"name": "default", "api_base": "http://localhost:8080/v1"},
            {"name": "deepseek", "api_base": "https://api.deepseek.com/v1"},
        ]
        errors = validate_config(data)
        assert errors == []


class TestBackupRestore:
    """backup_config / restore_from_backup"""

    def test_backup_creates_bak_file(self, tmp_path):
        """backup 创建 .bak 文件且内容一致"""
        config_path = tmp_path / "config.json"
        data = {"key": "value", "nested": {"a": 1}}
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        backup_config(config_path)

        bak_path = tmp_path / "config.json.bak"
        assert bak_path.exists()
        with open(bak_path, encoding="utf-8") as f:
            restored = json.load(f)
        assert restored == data

    def test_restore_from_backup_returns_data(self, tmp_path):
        """restore 返回备份数据"""
        bak_path = tmp_path / "config.json.bak"
        data = {"chroma": {"mode": "persistent"}, "model": {"path": "/tmp/model"}}
        with open(bak_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        result = restore_from_backup(tmp_path / "config.json")
        assert result == data

    def test_restore_from_backup_raises_when_no_bak(self, tmp_path):
        """无 .bak 文件时抛出 ConfigCorruptedError"""
        from memos.errors import ConfigCorruptedError

        with pytest.raises(ConfigCorruptedError):
            restore_from_backup(tmp_path / "nonexistent.json")


class TestMemoConfigLoad:
    """MemoConfig.load() 校验 + 备份恢复集成"""

    def test_load_backups_on_success(self, monkeypatch, tmp_path):
        """加载成功后自动备份"""
        home = tmp_path / ".memos"
        monkeypatch.setenv("MEMOS_HOME", str(home))
        ensure_memos_home()

        # 写一份合法配置
        config_path = home / "etc" / "config.json"
        config_data = {
            "chroma": {"mode": "persistent", "path": str(home / "memdb")},
            "model": {"path": str(home / "model" / "bge-large-zh-v1.5")},
            "llm": {"endpoints": [{"name": "default"}], "active": "default"},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f)

        # 触发加载
        cfg = MemoConfig.load()
        assert cfg is not None
        assert (home / "etc" / "config.json.bak").exists()

    def test_load_fallback_on_json_parse_error(self, monkeypatch, tmp_path):
        """JSON 解析失败时回退到 .bak"""
        home = tmp_path / ".memos"
        monkeypatch.setenv("MEMOS_HOME", str(home))
        ensure_memos_home()

        config_path = home / "etc" / "config.json"
        bak_path = home / "etc" / "config.json.bak"

        # 先写一份合法的 .bak
        valid_data = {
            "chroma": {"mode": "persistent", "path": str(home / "memdb")},
            "model": {"path": str(home / "model" / "bge-large-zh-v1.5")},
            "llm": {"endpoints": [{"name": "default"}], "active": "default"},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        with open(bak_path, "w", encoding="utf-8") as f:
            json.dump(valid_data, f)

        # 写入损坏的 JSON
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("这不是合法的 JSON {{{")

        # 应能加载（从 backup 恢复）
        cfg = MemoConfig.load()
        assert cfg is not None

    def test_load_fallback_on_validation_error(self, monkeypatch, tmp_path):
        """配置校验失败时回退到 .bak"""
        home = tmp_path / ".memos"
        monkeypatch.setenv("MEMOS_HOME", str(home))
        ensure_memos_home()

        config_path = home / "etc" / "config.json"
        bak_path = home / "etc" / "config.json.bak"

        # 先写合法 .bak
        valid_data = {
            "chroma": {"mode": "persistent", "path": str(home / "memdb")},
            "model": {"path": str(home / "model" / "bge-large-zh-v1.5")},
            "llm": {"endpoints": [{"name": "default"}], "active": "default"},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        with open(bak_path, "w", encoding="utf-8") as f:
            json.dump(valid_data, f)

        # 写入缺少必填段 chroma 的配置
        bad_data = {k: v for k, v in valid_data.items() if k != "chroma"}
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(bad_data, f)

        # 应能加载（从 backup 恢复）
        cfg = MemoConfig.load()
        assert cfg is not None

    def test_load_uses_defaults_when_all_corrupt(self, monkeypatch, tmp_path):
        """配置文件和 .bak 都损坏时使用默认配置（不崩溃）"""
        home = tmp_path / ".memos"
        monkeypatch.setenv("MEMOS_HOME", str(home))
        ensure_memos_home()

        config_path = home / "etc" / "config.json"
        bak_path = home / "etc" / "config.json.bak"

        # 两者都写损坏 JSON
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("{{{ 损坏")
        with open(bak_path, "w", encoding="utf-8") as f:
            f.write("{{{ 也损坏")

        # 不应崩溃
        cfg = MemoConfig.load()
        assert cfg is not None
        assert cfg.chroma.mode == "persistent"  # 默认值


class TestCLIConfigValidate:
    """memos config validate CLI 命令"""

    def test_validate_valid_config(self, monkeypatch, tmp_path, capsys):
        """校验合法配置 → 输出 OK"""
        home = tmp_path / ".memos"
        monkeypatch.setenv("MEMOS_HOME", str(home))
        ensure_memos_home()

        config_file = home / "etc" / "config.json"
        valid_data = {
            "chroma": {"mode": "persistent", "path": str(home / "memdb")},
            "model": {"path": str(home / "model" / "bge-large-zh-v1.5")},
            "llm": {"endpoints": [{"name": "default"}], "active": "default"},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(valid_data, f)

        import argparse
        from memos.cli.dispatch import cmd_config

        args = argparse.Namespace(action="validate", file=None, key=None, value=None)
        result = cmd_config(args)
        captured = capsys.readouterr()
        assert "配置校验通过" in captured.out

    def test_validate_invalid_config(self, monkeypatch, tmp_path, capsys):
        """校验非法配置 → 输出错误详情"""
        home = tmp_path / ".memos"
        monkeypatch.setenv("MEMOS_HOME", str(home))
        ensure_memos_home()

        config_file = home / "etc" / "config.json"
        # 非法配置：vector_dim 应为 int
        invalid_data = {
            "chroma": {"mode": "persistent", "path": str(home / "memdb")},
            "model": {"path": str(home / "model"), "vector_dim": "bad"},
            "llm": {"endpoints": [{"name": "default"}], "active": "default"},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(invalid_data, f)

        import argparse
        from memos.cli.dispatch import cmd_config

        args = argparse.Namespace(action="validate", file=None, key=None, value=None)
        try:
            cmd_config(args)
        except SystemExit as e:
            assert e.code == 1
        captured = capsys.readouterr()
        assert "配置校验失败" in captured.out

    def test_validate_with_file_arg(self, tmp_path, capsys):
        """--file 指定文件路径校验"""
        config_file = tmp_path / "my-config.json"
        valid_data = {
            "chroma": {"mode": "persistent", "path": str(tmp_path / "memdb")},
            "model": {"path": str(tmp_path / "model")},
            "llm": {"endpoints": [{"name": "default"}], "active": "default"},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(valid_data, f)

        import argparse
        from memos.cli.dispatch import cmd_config

        args = argparse.Namespace(action="validate", file=str(config_file), key=None, value=None)
        cmd_config(args)
        captured = capsys.readouterr()
        assert "配置校验通过" in captured.out

    def test_validate_nonexistent_file(self, capsys):
        """校验不存在的文件 → 报错"""
        import argparse
        from memos.cli.dispatch import cmd_config

        args = argparse.Namespace(action="validate", file="/nonexistent/config.json", key=None, value=None)
        try:
            cmd_config(args)
        except SystemExit as e:
            assert e.code == 1
        captured = capsys.readouterr()
        assert "不存在" in captured.out or "不存在" in captured.err


class TestAgentConfig:
    """AgentConfig 配置模型（Sprint 3 T4）"""

    def test_defaults(self):
        """AgentConfig 默认值正确"""
        from memos.config.models import AgentConfig

        cfg = AgentConfig()
        assert cfg.enabled is True
        assert cfg.pattern_detection_enabled is True
        assert cfg.daily_briefing_enabled is True
        assert cfg.daily_briefing_time == "09:00"
        assert cfg.topic_cluster_window_days == 7
        assert cfg.recurrence_threshold == 3
        assert cfg.bug_match_similarity == 0.70
        assert cfg.max_daily_briefing_items == 3
        assert cfg.briefing_cooldown_hours == 24
        assert cfg.signal_cooldown_hours == 6
        assert cfg.max_active_signals == 5

    def test_round_trip(self, monkeypatch, tmp_path):
        """序列化/反序列化 round-trip 正确"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        from memos.config.loader import MemoConfig

        cfg1 = MemoConfig.load()
        cfg1.agent.enabled = False
        cfg1.agent.max_active_signals = 10
        cfg1.save()

        cfg2 = MemoConfig.load()
        assert cfg2.agent.enabled is False
        assert cfg2.agent.max_active_signals == 10

    def test_env_override(self, monkeypatch, tmp_path):
        """MEMOS_AGENT_ENABLED=false 可覆盖配置"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        monkeypatch.setenv("MEMOS_AGENT_ENABLED", "false")
        from memos.config.loader import MemoConfig

        cfg = MemoConfig.load()
        assert cfg.agent.enabled is False

    def test_auto_complete_old_config(self, monkeypatch, tmp_path):
        """旧 config.json 无 agent 节时自动补全默认值"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        from memos.config import ensure_memos_home
        from memos.config.loader import MemoConfig

        ensure_memos_home()
        config_path = tmp_path / "etc" / "config.json"
        old_data = {
            "chroma": {"mode": "persistent", "path": str(tmp_path / "memdb")},
            "model": {"path": str(tmp_path / "model")},
            "llm": {"endpoints": [{"name": "default"}], "active": "default"},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(old_data, f)

        cfg = MemoConfig.load()
        assert hasattr(cfg, "agent")
        assert cfg.agent.enabled is True
        assert cfg.agent.max_active_signals == 5

    def test_env_override_cli(self, monkeypatch, tmp_path, capsys):
        """MEMOS_AGENT_ENABLED=false 在 CLI config show 中可见。"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        monkeypatch.setenv("MEMOS_AGENT_ENABLED", "false")
        from memos.config import ensure_memos_home
        from memos.config.loader import MemoConfig

        ensure_memos_home()
        config_path = tmp_path / "etc" / "config.json"
        old_data = {
            "chroma": {"mode": "persistent", "path": str(tmp_path / "memdb")},
            "model": {"path": str(tmp_path / "model")},
            "llm": {"endpoints": [{"name": "default"}], "active": "default"},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(old_data, f)

        import argparse
        from memos.cli.dispatch import cmd_config

        args = argparse.Namespace(action="show", file=None, key=None, value=None)
        cmd_config(args)
        captured = capsys.readouterr()
        assert "agent.enabled" in captured.out
        assert "False" in captured.out