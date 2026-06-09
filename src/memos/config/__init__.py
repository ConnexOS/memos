"""MEMOS 配置子包 —— 从 config.py 拆分（v0.4.3 架构重整 Phase 6）。

提供：
- 所有子配置模型 (ChromaConfig ~ NotificationConfig)
- 提示词模板管理 (PromptTemplate, PromptManager)
- 配置加载/校验/Schema生成
- 全局 config 单例
"""

from memos.config.loader import (
    MemoConfig,
    _get_config_file,
    _get_schema_path,  # noqa: F401  # 测试引用
    backup_config,
    config,
    get_config_schema,
    restore_from_backup,
    validate_config,
)
from memos.config.models import (
    _DEFAULT_CONFLICT_PROMPT,
    _DEFAULT_DAILY_REVIEW_PROMPT,
    _DEFAULT_PROMPT_FRAME,
    _DEFAULT_SYSTEM_PROMPT,
    _NEW_EXTRACT_SYSTEM_PROMPT,
    PROMPT_TEMPLATE_TYPES,
    AuthConfig,
    BackupConfig,
    BufferConfig,
    ChromaConfig,
    DashboardConfig,
    HookProxyConfig,
    LLMConfig,
    LLMEndpoint,
    MemoryConfig,
    ModelConfig,
    NotificationConfig,
    ServerConfig,
    SuggestionConfig,
    SystemSuggestionConfig,
    SystemSuggestionTriggers,
    ensure_memos_home,
    get_memos_home,
)
from memos.config.prompts import (
    PromptManager,
    PromptTemplate,
    PromptVersion,
    _get_prompts_dir,
    _get_prompts_file,
    _get_prompts_index,
    _get_template_dir,
    _get_template_file,
    _get_version_file,
)

__all__ = [
    "config",
    "MemoConfig",
    "get_memos_home",
    "ensure_memos_home",
    "get_config_schema",
    "validate_config",
    "backup_config",
    "restore_from_backup",
    # 子配置模型
    "ChromaConfig",
    "ModelConfig",
    "LLMEndpoint",
    "LLMConfig",
    "MemoryConfig",
    "SuggestionConfig",
    "BufferConfig",
    "DashboardConfig",
    "ServerConfig",
    "HookProxyConfig",
    "AuthConfig",
    "BackupConfig",
    "NotificationConfig",
    "SystemSuggestionConfig",
    "SystemSuggestionTriggers",
    # 提示词模板
    "PromptVersion",
    "PromptTemplate",
    "PromptManager",
    "PROMPT_TEMPLATE_TYPES",
    # 默认提示词
    "_DEFAULT_SYSTEM_PROMPT",
    "_NEW_EXTRACT_SYSTEM_PROMPT",
    "_DEFAULT_CONFLICT_PROMPT",
    "_DEFAULT_PROMPT_FRAME",
    "_DEFAULT_DAILY_REVIEW_PROMPT",
    # 内部工具函数（测试/外部引用）
    "_get_config_file",
    "_get_prompts_dir",
    "_get_prompts_file",
    "_get_prompts_index",
    "_get_template_dir",
    "_get_template_file",
    "_get_version_file",
]
