"""Web 模块共享工具函数。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..config import config


def detect_project_id() -> str:
    """基于进程 CWD 计算项目 ID，作为 project_id 的最终兜底值。"""
    return hashlib.md5(str(Path.cwd()).encode()).hexdigest()[: config.server.id_length]
