"""Web 模块共享工具函数。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from ..config import config


def detect_project_id() -> str:
    """基于进程 CWD 计算项目 ID，作为 project_id 的最终兜底值。"""
    return hashlib.md5(str(Path.cwd()).encode()).hexdigest()[: config.server.id_length]


async def run_sync(func, *args, **kwargs):
    """在后台线程中执行同步函数，避免阻塞 event loop。

    P0 修复：所有 async def 路由处理器中的同步 ChromaDB 调用需通过此函数执行，
    防止 event loop 被阻塞导致 SSE 断流、MCP 超时、CTRL+C 无响应。

    用法：
        results = await run_sync(mem.list_memories, type_filter="task", limit=10)
        item = await run_sync(mem.get_memory, mem_id)
    """
    return await asyncio.to_thread(func, *args, **kwargs)
