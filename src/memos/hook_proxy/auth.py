# src/memos/hook_proxy/auth.py

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CREDENTIALS_DIR = Path.home() / ".memos" / "etc"
_CREDENTIALS_FILE = _CREDENTIALS_DIR / "credentials.json"
_PROJECT_CREDENTIALS_NAME = "memos-credentials.json"


def _get_project_credentials_path(project_dir: str | None = None) -> Path | None:
    """检测项目级凭据路径 .claude/memos-credentials.json，仅在项目根存在 .memos-project 时返回。"""
    try:
        cwd = Path(project_dir).resolve() if project_dir else Path.cwd()
        if (cwd / ".memos-project").exists():
            return cwd / ".claude" / _PROJECT_CREDENTIALS_NAME
    except Exception:
        logger.debug("获取项目凭据路径失败", exc_info=True)
    return None


def load_credentials() -> dict | None:
    """加载凭据：优先项目 .claude/memos-credentials.json，再 fallback 全局 ~/.memos/etc/credentials.json。"""
    # 项目级优先
    project_path = _get_project_credentials_path()
    if project_path and project_path.exists():
        try:
            with open(project_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # 全局 fallback
    if not _CREDENTIALS_FILE.exists():
        return None
    try:
        with open(_CREDENTIALS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_credentials(server_url: str, token: str):
    """保存凭据到项目 .claude/memos-credentials.json。检测不到项目根时 fallback 到全局。"""
    data = {"server_url": server_url, "token": token}

    # 项目级优先
    project_path = _get_project_credentials_path()
    if project_path:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        with open(project_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(project_path, 0o600)
        except Exception:
            logger.warning("设置项目凭据文件权限失败: %s", project_path)
        return

    # 全局 fallback
    _CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_CREDENTIALS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(_CREDENTIALS_FILE, 0o600)
    except Exception:
        logger.warning("设置全局凭据文件权限失败: %s", _CREDENTIALS_FILE)


def clear_credentials() -> bool:
    """清除全局凭据文件。项目级文件可手动删除。"""
    if _CREDENTIALS_FILE.exists():
        _CREDENTIALS_FILE.unlink()
        return True
    return False
