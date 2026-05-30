"""S1 — F6 配置基础：SuggestionConfig 字段默认值、环境变量覆盖、schema 校验。"""

import json
from unittest import mock

import pytest

from memos.config.models import SuggestionConfig


class TestSuggestionConfigDefaults:
    """默认值验证：SuggestionConfig 的字段默认值。"""

    def test_new_fields_have_defaults(self):
        cfg = SuggestionConfig()
        assert cfg.enable_active_suggestions is True
        assert cfg.active_suggestion_threshold == 0.65
        assert cfg.context_injection_threshold == 0.50
        assert cfg.context_max_items == 3
        assert cfg.suggestion_cooldown_minutes == 30
        assert cfg.suggestion_max_per_day == 10
        assert cfg.suggestion_expiry_days == 7

    def test_missing_fields_fallback(self):
        """缺失字段回退：仅提供部分字段时，其余用默认值。"""
        cfg = SuggestionConfig()
        assert cfg.enable_active_suggestions is True
        assert cfg.suggestion_max_per_day == 10
        assert cfg.suggestion_expiry_days == 7

    def test_partial_override(self):
        """部分覆盖：提供部分字段时，未提供的字段仍用默认值。"""
        cfg = SuggestionConfig.model_validate({"context_max_items": 5})
        assert cfg.context_max_items == 5
        assert cfg.enable_active_suggestions is True
        assert cfg.suggestion_cooldown_minutes == 30


class TestSuggestionConfigAlias:
    """别名兼容：suggestion_max_per_session → suggestion_max_per_day。"""

    def test_old_key_maps_to_new_key(self):
        data = {"suggestion_max_per_session": 5}
        cfg = SuggestionConfig.model_validate(data)
        assert cfg.suggestion_max_per_day == 5

    def test_new_key_takes_precedence(self):
        """新旧键同时存在时，新键优先。"""
        data = {"suggestion_max_per_session": 5, "suggestion_max_per_day": 8}
        cfg = SuggestionConfig.model_validate(data)
        assert cfg.suggestion_max_per_day == 8

    def test_alias_overrides_default(self):
        data = {"suggestion_max_per_session": 3}
        cfg = SuggestionConfig.model_validate(data)
        assert cfg.suggestion_max_per_day == 3
        assert cfg.suggestion_max_per_day != 10


class TestSuggestionConfigFieldValidation:
    """字段校验约束：ge/le 边界确保非法值被拒绝。"""

    def test_enable_active_suggestions_false(self):
        cfg = SuggestionConfig.model_validate({"enable_active_suggestions": False})
        assert cfg.enable_active_suggestions is False

    @pytest.mark.parametrize(
        "field,value",
        [
            ("active_suggestion_threshold", -0.1),
            ("active_suggestion_threshold", 1.5),
            ("context_injection_threshold", -0.1),
            ("context_injection_threshold", 1.5),
            ("context_max_items", 0),
            ("context_max_items", 11),
            ("suggestion_cooldown_minutes", -1),
            ("suggestion_max_per_day", -1),
            ("suggestion_expiry_days", -1),
        ],
    )
    def test_invalid_values_raises(self, field, value):
        with pytest.raises((ValueError, AssertionError)):
            SuggestionConfig.model_validate({field: value})

    def test_expiry_days_zero_is_valid(self):
        """suggestion_expiry_days=0 是合法的（不过期语义）。"""
        cfg = SuggestionConfig.model_validate({"suggestion_expiry_days": 0})
        assert cfg.suggestion_expiry_days == 0

    def test_context_max_items_boundary(self):
        cfg = SuggestionConfig.model_validate({"context_max_items": 1})
        assert cfg.context_max_items == 1
        cfg = SuggestionConfig.model_validate({"context_max_items": 10})
        assert cfg.context_max_items == 10

    def test_threshold_boundary(self):
        cfg = SuggestionConfig.model_validate({"active_suggestion_threshold": 0.0})
        assert cfg.active_suggestion_threshold == 0.0
        cfg = SuggestionConfig.model_validate({"active_suggestion_threshold": 1.0})
        assert cfg.active_suggestion_threshold == 1.0


_MINIMAL_FULL_CONFIG = json.dumps(
    {
        "chroma": {"mode": "persistent"},
        "model": {"name": "test"},
        "llm": {"endpoints": [{"name": "default"}]},
        "memory": {},
        "buffer": {},
        "dashboard": {},
        "server": {},
        "auth": {},
    }
)


class TestSuggestionConfigEnvOverride:
    """环境变量 MEMOS_SUGGESTION_* 覆盖生效。"""

    def _setup_minimal_config(self, tmp_path):
        etc_dir = tmp_path / "etc"
        etc_dir.mkdir(parents=True)
        prompts_dir = etc_dir / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "index.json").write_text("{}", encoding="utf-8")
        (etc_dir / "config.json").write_text(_MINIMAL_FULL_CONFIG, encoding="utf-8")

    def test_env_bool_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMOS_SUGGESTION_ENABLE_ACTIVE_SUGGESTIONS", "false")
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        self._setup_minimal_config(tmp_path)
        with mock.patch("memos.config.loader.PromptManager.load"):
            from memos.config.loader import MemoConfig

            cfg = MemoConfig.load()
        assert cfg.suggestion.enable_active_suggestions is False

    def test_env_int_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMOS_SUGGESTION_SUGGESTION_MAX_PER_DAY", "5")
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        self._setup_minimal_config(tmp_path)
        with mock.patch("memos.config.loader.PromptManager.load"):
            from memos.config.loader import MemoConfig

            cfg = MemoConfig.load()
        assert cfg.suggestion.suggestion_max_per_day == 5

    def test_env_float_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMOS_SUGGESTION_ACTIVE_SUGGESTION_THRESHOLD", "0.5")
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        self._setup_minimal_config(tmp_path)
        with mock.patch("memos.config.loader.PromptManager.load"):
            from memos.config.loader import MemoConfig

            cfg = MemoConfig.load()
        assert cfg.suggestion.active_suggestion_threshold == 0.5

    def test_env_does_not_affect_other_fields(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMOS_SUGGESTION_ENABLE_ACTIVE_SUGGESTIONS", "false")
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))
        self._setup_minimal_config(tmp_path)
        with mock.patch("memos.config.loader.PromptManager.load"):
            from memos.config.loader import MemoConfig

            cfg = MemoConfig.load()
        assert cfg.suggestion.context_max_items == 3  # 不受影响


class TestSuggestionConfigSchema:
    """JSON Schema 校验通过。"""

    def test_schema_validation_with_new_fields(self):
        from memos.config.loader import validate_config

        config_data = {
            "chroma": {"mode": "persistent"},
            "model": {"name": "test"},
            "llm": {"endpoints": [{"name": "default"}]},
            "suggestion": {
                "enable_active_suggestions": True,
                "active_suggestion_threshold": 0.75,
                "context_injection_threshold": 0.55,
                "context_max_items": 3,
                "suggestion_cooldown_minutes": 30,
                "suggestion_max_per_day": 10,
                "suggestion_expiry_days": 7,
            },
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        errors = validate_config(config_data)
        assert errors == [], f"Schema 校验失败: {errors}"

    def test_schema_without_new_fields_still_passes(self):
        """旧配置无建议字段时，schema 校验也通过。"""
        from memos.config.loader import validate_config

        config_data = {
            "chroma": {"mode": "persistent"},
            "model": {"name": "test"},
            "llm": {"endpoints": [{"name": "default"}]},
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "auth": {},
        }
        errors = validate_config(config_data)
        assert errors == [], f"Schema 校验失败: {errors}"
