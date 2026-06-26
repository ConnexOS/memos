"""MEMOS 配置子包 —— 从 config.py 拆分（v0.4.3 架构重整 Phase 6）。

提供：
- 所有子配置模型 (ChromaConfig ~ NotificationConfig)
- 提示词模板管理 (PromptTemplate, PromptManager)
- 配置加载/校验/Schema生成
- 全局 config 单例
- load_behavior_guide() 从 etc/behavior_guide.json 读取行为引导文本
"""

import json
import logging
import os
import platform
import subprocess

from memos.config.loader import (
    MemoConfig,
    _get_config_file,
    _get_schema_path,  # noqa: F401  # 测试引用
    backup_config,
    get_config,
    get_config_schema,
    restore_from_backup,
    validate_config,
)
from memos.config.models import (
    _DEFAULT_BRIEFING_SYSTEM_PROMPT,
    _DEFAULT_CONFLICT_PROMPT,
    _DEFAULT_DAILY_REVIEW_PROMPT,
    _DEFAULT_PROMPT_FRAME,
    _DEFAULT_SYSTEM_PROMPT,
    _NEW_EXTRACT_SYSTEM_PROMPT,
    PROMPT_TEMPLATE_TYPES,
    AuthConfig,
    BackupConfig,
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

logger = logging.getLogger(__name__)


def __getattr__(name):
    """向后兼容：from memos.config import config → get_config()"""
    if name == "config":
        return get_config()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_DEFAULT_BEHAVIOR_GUIDE = (
    "当你帮用户解决了一个报错/做出了技术决策/完成了一个里程碑，"
    "调用 save_knowledge 记录。判断权在你——只有你确认有价值再写。宁少写不错写。"
)


def load_behavior_guide() -> str:
    """从 etc/behavior_guide.json 读取行为引导文本。

    读取优先级：文件 > 代码硬编码兜底。
    文件不存在/损坏/缺失 text 字段时，静默降级返回默认文本，不报错。
    """
    from memos.config.models import get_memos_home

    path = get_memos_home() / "etc" / "behavior_guide.json"
    try:
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            text = data.get("text", "")
            if isinstance(text, str) and text.strip():
                return text
    except (json.JSONDecodeError, OSError, IOError):
        logger.debug("behavior_guide.json 读取失败，使用默认值", exc_info=True)
    return _DEFAULT_BEHAVIOR_GUIDE


_LOCAL_TZ_CACHE: str | None = None
_WIN_TO_IANA = {
    "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "Taipei Standard Time": "Asia/Taipei",
    "Hong Kong Standard Time": "Asia/Hong_Kong",
    "Singapore Standard Time": "Asia/Singapore",
    "India Standard Time": "Asia/Kolkata",
    "US Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "GMT Standard Time": "Europe/London",
    "Central Europe Standard Time": "Europe/Berlin",
    "Central European Standard Time": "Europe/Berlin",
    "Eastern European Standard Time": "Europe/Bucharest",
    "W. Europe Standard Time": "Europe/Paris",
    "SE Asia Standard Time": "Asia/Bangkok",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "New Zealand Standard Time": "Pacific/Auckland",
}


def get_local_timezone(fallback: str = "Asia/Shanghai") -> str:
    """自动检测本地 IANA 时区，结果缓存。

    支持 Windows (PowerShell Get-TimeZone) 和 Linux/macOS (/etc/localtime)。
    失败时回退 fallback 参数（默认 Asia/Shanghai）。
    """
    global _LOCAL_TZ_CACHE
    if _LOCAL_TZ_CACHE is not None:
        return _LOCAL_TZ_CACHE

    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(Get-TimeZone).Id"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                win_tz = result.stdout.strip()
                tz = _WIN_TO_IANA.get(win_tz, win_tz)
                _LOCAL_TZ_CACHE = tz
                logger.debug("检测到时区(Windows): %s → %s", win_tz, tz)
                return tz
        else:
            # Linux/macOS: /etc/timezone 文件或 /etc/localtime 符号链接
            tz_path = "/etc/timezone"
            if os.path.exists(tz_path):
                tz = open(tz_path, encoding="utf-8").read().strip()
                if tz:
                    _LOCAL_TZ_CACHE = tz
                    logger.debug("检测到时区(/etc/timezone): %s", tz)
                    return tz
            localtime_path = "/etc/localtime"
            if os.path.exists(localtime_path):
                link = os.readlink(localtime_path)
                if "zoneinfo" in link:
                    tz = link.split("zoneinfo/")[-1]
                    _LOCAL_TZ_CACHE = tz
                    logger.debug("检测到时区(/etc/localtime): %s", tz)
                    return tz
    except Exception as e:
        logger.debug("时区自动检测失败: %s，使用回退: %s", e, fallback)

    _LOCAL_TZ_CACHE = fallback
    return fallback


__all__ = [
    "config",
    "get_config",
    "MemoConfig",
    "get_memos_home",
    "load_behavior_guide",
    "_DEFAULT_BEHAVIOR_GUIDE",
    "ensure_memos_home",
    "get_config_schema",
    "validate_config",
    "backup_config",
    "get_local_timezone",
    "restore_from_backup",
    # 子配置模型
    "ChromaConfig",
    "ModelConfig",
    "LLMEndpoint",
    "LLMConfig",
    "MemoryConfig",
    "SuggestionConfig",
    "DashboardConfig",
    "ServerConfig",
    "HookProxyConfig",
    "AuthConfig",
    "BackupConfig",
    "NotificationConfig",
    # 提示词模板
    "PromptVersion",
    "PromptTemplate",
    "PromptManager",
    "PROMPT_TEMPLATE_TYPES",
    # 默认提示词
    "_DEFAULT_SYSTEM_PROMPT",
    "_NEW_EXTRACT_SYSTEM_PROMPT",
    "_DEFAULT_BRIEFING_SYSTEM_PROMPT",
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
