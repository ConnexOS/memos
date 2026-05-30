"""MEMOS 国际化支持 — Translator 类 + JSON 翻译文件加载。"""

import json
import logging
from pathlib import Path

from memos.config import config

logger = logging.getLogger(__name__)

_LOCALES_DIR = Path(__file__).resolve().parent.parent.parent / "etc" / "locales"

_translator_cache: dict[str, "Translator"] = {}
_current: "Translator | None" = None


class Translator:
    """轻量翻译器，从 JSON 文件加载键值对。"""

    def __init__(self, lang: str):
        self.lang = lang
        self._data: dict[str, str] = {}
        self._load()

    def _load(self):
        file_path = _LOCALES_DIR / f"{self.lang}.json"
        if not file_path.exists():
            logger.warning("翻译文件不存在: %s，回退到空翻译", file_path)
            return
        try:
            with open(file_path, encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception as e:
            logger.warning("翻译文件加载失败 (%s): %s", file_path, e)

    def t(self, key: str, default: str = None) -> str:
        """按 key 获取翻译文本，不存在时返回 default 或 key 本身。"""
        return self._data.get(key, default if default is not None else key)


def get_translator(lang: str = None) -> Translator:
    """获取指定语言的 Translator 实例（带缓存）。"""
    lang = lang or config.dashboard.locale if hasattr(config.dashboard, "locale") else "zh"
    if lang not in _translator_cache:
        _translator_cache[lang] = Translator(lang)
    return _translator_cache[lang]


def _(key: str, default: str = None) -> str:
    """简写函数，用于模板中：{{ _('key') }}"""
    return get_translator().t(key, default)
