"""MEMOS 配置加载器 —— MemoConfig 主类 + 加载链 + Schema 生成 + 校验。

从 config.py 拆分（v0.4.3 架构重整 Phase 6）。
"""

import json
import logging
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from memos._version import __version__
from memos.errors import ConfigCorruptedError

from .models import (
    AgentConfig,
    AuthConfig,
    BackupConfig,
    BufferConfig,
    ChromaConfig,
    DashboardConfig,
    LLMConfig,
    MemoryConfig,
    ModelConfig,
    NotificationConfig,
    ServerConfig,
    SuggestionConfig,
    SystemSuggestionConfig,
    _compute_default_project_id,
    get_memos_home,
)
from .prompts import PromptManager

logger = logging.getLogger(__name__)


def _get_config_file() -> Path:
    return get_memos_home() / "etc" / "config.json"


def _get_schema_path() -> Path:
    return get_memos_home() / "etc" / "config.schema.json"


def get_config_schema(force_refresh: bool = False) -> dict:
    """获取 MemoConfig 的 JSON Schema，首次生成后缓存到 etc/config.schema.json。"""
    schema_path = _get_schema_path()
    if not force_refresh and schema_path.exists():
        with open(schema_path, encoding="utf-8") as f:
            data = json.load(f)
        # 缓存版本校验：版本不匹配时自动刷新，避免代码模型与 Schema 不一致
        if data.get("_schema_version") == __version__:
            return data
        logger.info("Schema 版本 %s != %s，触发自动刷新", data.get("_schema_version"), __version__)

    sub_models = {
        "chroma": ChromaConfig,
        "model": ModelConfig,
        "llm": LLMConfig,
        "memory": MemoryConfig,
        "suggestion": SuggestionConfig,
        "buffer": BufferConfig,
        "dashboard": DashboardConfig,
        "server": ServerConfig,
        "auth": AuthConfig,
        "backup": BackupConfig,
        "notification": NotificationConfig,
        "system_suggestion": SystemSuggestionConfig,
        "agent": AgentConfig,
    }
    extra_defs = {}
    from .models import LLMEndpoint, SystemSuggestionTriggers

    for model_cls in [LLMEndpoint, SystemSuggestionTriggers]:
        extra_defs[model_cls.__name__] = model_cls.model_json_schema()

    properties = {}
    for name, model_cls in sub_models.items():
        sub_schema = model_cls.model_json_schema()
        _resolve_refs(sub_schema, extra_defs)
        properties[name] = {
            "type": "object",
            "properties": sub_schema.get("properties", {}),
            "required": sub_schema.get("required", []),
            "additionalProperties": False,
        }
    properties["prompt"] = {"type": "object"}
    required_sections = ["llm"]

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "MemoConfig",
        "type": "object",
        "properties": properties,
        "required": required_sections,
        "additionalProperties": False,
        "_schema_version": __version__,
    }

    schema_path.parent.mkdir(parents=True, exist_ok=True)
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
    return schema


def _resolve_refs(schema: dict, defs: dict):
    """递归将 schema 中的 $ref 引用替换为内联定义"""
    if isinstance(schema, dict):
        if "$ref" in schema:
            ref_path = schema["$ref"]
            if ref_path.startswith("#/$defs/"):
                def_name = ref_path[len("#/$defs/") :]
                if def_name in defs:
                    resolved = dict(defs[def_name])
                    schema.pop("$ref", None)
                    resolved.update(schema)
                    schema.clear()
                    schema.update(resolved)
                    _resolve_refs(schema, defs)
                    return
        for _key, value in schema.items():
            _resolve_refs(value, defs)
    elif isinstance(schema, list):
        for item in schema:
            _resolve_refs(item, defs)


def validate_config(data: dict) -> list[str]:
    """校验配置字典：先 JSON Schema 结构校验，再 Pydantic 类型校验。"""
    errors = []

    try:
        import jsonschema
    except ImportError:
        pass
    else:
        schema = get_config_schema()
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            path = " → ".join(str(p) for p in e.absolute_path) if e.absolute_path else "根"
            errors.append(f"[Schema] {path}: {e.message}")
        except jsonschema.SchemaError as e:
            errors.append(f"[Schema] JSON Schema 定义错误: {e.message}")

    pydantic_data = {k: v for k, v in data.items() if k != "prompt"}
    try:
        MemoConfig.model_validate(pydantic_data)
    except Exception as e:
        errors_list = e.errors() if hasattr(e, "errors") else []
        if errors_list:
            for err in errors_list:
                loc = " → ".join(str(p) for p in err.get("loc", []))
                msg = err.get("msg", str(err))
                errors.append(f"[Pydantic] {loc}: {msg}")
        else:
            errors.append(f"[Pydantic] {e}")

    return errors


def backup_config(config_path: Path) -> None:
    """备份配置文件到 .bak"""
    bak_path = config_path.with_suffix(".json.bak")
    try:
        shutil.copy2(config_path, bak_path)
        logger.debug("配置已备份到 %s", bak_path)
    except OSError as e:
        logger.warning("配置备份失败: %s", e)


def restore_from_backup(config_path: Path) -> dict:
    """从 .bak 恢复配置，失败抛 FileNotFoundError"""
    bak_path = config_path.with_suffix(".json.bak")
    if not bak_path.exists():
        raise ConfigCorruptedError(f"配置文件备份不存在: {bak_path}", detail="运行 memos init --force 重新初始化")
    with open(bak_path, encoding="utf-8") as f:
        return json.load(f)


class MemoConfig(BaseModel):
    chroma: ChromaConfig = Field(default_factory=ChromaConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    suggestion: SuggestionConfig = Field(default_factory=SuggestionConfig)
    buffer: BufferConfig = Field(default_factory=BufferConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    system_suggestion: SystemSuggestionConfig = Field(default_factory=SystemSuggestionConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    prompt: PromptManager = Field(default_factory=PromptManager)

    @model_validator(mode="before")
    @classmethod
    def _migrate_memory_to_suggestion(cls, data):
        """迁移旧版 memory 中的建议字段到独立的 suggestion 节。"""
        if isinstance(data, dict):
            memory = data.get("memory")
            if isinstance(memory, dict):
                suggestion_keys = {
                    "enable_active_suggestions",
                    "active_suggestion_threshold",
                    "context_injection_threshold",
                    "context_max_items",
                    "suggestion_cooldown_minutes",
                    "suggestion_max_per_day",
                    "suggestion_expiry_days",
                    "suggestion_max_pending",
                    "suggestion_display_limit",
                    "suggestion_manual_daily_limit",
                    "suggestion_max_per_session",
                }
                migrated = {k: memory.pop(k) for k in list(memory.keys()) if k in suggestion_keys}
                if migrated:
                    data.setdefault("suggestion", {})
                    # 不覆盖 suggestion 节已存在的值，防止旧 memory 默认值污染新配置
                    for k, v in migrated.items():
                        data["suggestion"].setdefault(k, v)
                    logger.info("已迁移 %d 个建议字段从 memory → suggestion", len(migrated))
        return data

    def save(self):
        data = self.model_dump()
        for section_name, section in [
            ("chroma", self.chroma),
            ("model", self.model),
            ("llm", self.llm),
            ("memory", self.memory),
            ("suggestion", self.suggestion),
            ("buffer", self.buffer),
            ("dashboard", self.dashboard),
            ("server", self.server),
            ("auth", self.auth),
            ("backup", self.backup),
            ("notification", self.notification),
            ("system_suggestion", self.system_suggestion),
            ("agent", self.agent),
        ]:
            if type(section).model_computed_fields:
                for key in type(section).model_computed_fields:
                    data[section_name].pop(key, None)
        # prompt 节由 PromptManager 独立管理（etc/prompts/ 目录），不在 config.json 中持久化
        data.pop("prompt", None)
        config_file = _get_config_file()
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls) -> "MemoConfig":
        config_file = _get_config_file()
        file_data = {}

        if config_file.exists():
            with open(config_file, encoding="utf-8") as f:
                try:
                    file_data = json.load(f)
                except json.JSONDecodeError as e:
                    logger.warning("配置文件 JSON 解析失败: %s，尝试从备份恢复", e)
                    try:
                        file_data = restore_from_backup(config_file)
                        logger.info("已从备份恢复配置")
                    except (FileNotFoundError, json.JSONDecodeError):
                        logger.warning("备份文件也不可用，使用默认配置")

            if file_data:
                errors = validate_config(file_data)
                if errors:
                    logger.warning("配置校验失败 (%d 个错误):\n  %s", len(errors), "\n  ".join(errors))
                    try:
                        file_data = restore_from_backup(config_file)
                        logger.info("已从备份恢复配置")
                    except (FileNotFoundError, json.JSONDecodeError):
                        logger.warning("备份文件不可用，尝试使用有问题的配置继续")

        cfg = cls.model_validate({k: v for k, v in file_data.items() if k != "prompt"})
        cfg.prompt = PromptManager.load()

        for env_key in os.environ:
            if env_key.startswith("MEMOS_"):
                parts = env_key.removeprefix("MEMOS_").lower().split("_", 1)
                if len(parts) == 2:
                    section, field = parts
                    section_obj = getattr(cfg, section, None)
                    if section_obj and hasattr(section_obj, field):
                        current = getattr(section_obj, field)
                        if isinstance(current, (list, dict)):
                            continue
                        val = os.environ[env_key]
                        if isinstance(current, bool):
                            val = val.lower() in ("true", "1", "yes")
                        elif isinstance(current, int):
                            val = int(val)
                        elif isinstance(current, float):
                            val = float(val)
                        setattr(section_obj, field, val)

        if file_data:
            backup_config(config_file)

        # 数据迁移：清理 config.json 中遗留的 "default" 占位符
        if cfg.memory.default_project_id in ("default", ""):
            _new_pid = _compute_default_project_id()
            logger.warning("default_project_id 已从 '%s' 迁移到 '%s'", cfg.memory.default_project_id, _new_pid)
            cfg.memory.default_project_id = _new_pid
            try:
                _cf = _get_config_file()
                if _cf.exists():
                    _raw = json.loads(_cf.read_text(encoding="utf-8"))
                    if _raw.get("memory", {}).get("default_project_id", "") in ("default", ""):
                        _raw.setdefault("memory", {})["default_project_id"] = _new_pid
                        _cf.write_text(json.dumps(_raw, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        return cfg

    def _flatten_section(self, result: dict, prefix: str, obj) -> None:
        """递归扁平化配置对象，处理嵌套子对象（如 triggers）。"""
        for field_name, field_value in obj.model_dump().items():
            key = f"{prefix}.{field_name}"
            if isinstance(field_value, dict):
                self._flatten_nested(result, key, field_value)
            else:
                result[key] = field_value

    def _flatten_nested(self, result: dict, prefix: str, data: dict) -> None:
        """扁平化嵌套字典为两层 key 格式。"""
        for k, v in data.items():
            key = f"{prefix}.{k}"
            result[key] = v

    def flatten(self) -> dict:
        result = {}
        for section_name, section in [
            ("chroma", self.chroma),
            ("model", self.model),
            ("llm", self.llm),
            ("memory", self.memory),
            ("suggestion", self.suggestion),
            ("buffer", self.buffer),
            ("dashboard", self.dashboard),
            ("server", self.server),
            ("auth", self.auth),
        ]:
            for field_name, field_value in section.model_dump().items():
                key = f"{section_name}.{field_name}"
                if key == "auth.token_hash" and field_value:
                    result[key] = f"{field_value[:4]}****{field_value[-4:]}"
                elif key == "auth.secret_key" and field_value:
                    result[key] = "****"
                else:
                    result[key] = field_value
        self._flatten_section(result, "system_suggestion", self.system_suggestion)
        self._flatten_section(result, "agent", self.agent)

        result["llm.api_base"] = self.llm.api_base
        result["llm.api_key"] = "******" if self.llm.api_key else ""
        result.pop("llm.endpoints", None)
        result["prompt.template_count"] = len(self.prompt.templates)
        return result

    def update_field(self, key: str, value):
        parts = key.split(".", 1)
        if len(parts) != 2:
            return False
        section_name, field_name = parts
        section = getattr(self, section_name, None)
        if not section or not hasattr(section, field_name):
            return False
        current = getattr(section, field_name)
        if isinstance(current, (list, dict)):
            return False
        try:
            if isinstance(current, bool):
                parsed = value if isinstance(value, bool) else value.lower() in ("true", "1", "yes")
            else:
                parsed = type(current)(value)
        except (ValueError, TypeError):
            return False
        setattr(section, field_name, parsed)
        return True

    @classmethod
    def reload(cls) -> "MemoConfig":
        new = cls.load()
        return new


# 全局单例配置实例
config = MemoConfig.load()
