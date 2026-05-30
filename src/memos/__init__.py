from memos._version import __version__
from memos.engine.extractor import MemoryExtractor, _estimate_tokens, format_conversation
from memos.engine.memory import DEFAULT_DECAY_LAMBDA, SIMILARITY_THRESHOLD, ContextMemory

# dashboard → web 迁移（向后兼容）
from memos.web import app, main


def __getattr__(name: str):
    """惰性加载 server 模块，避免未安装 pywin32 时导入失败。"""
    if name == "mcp":
        from memos.server.mcp import mcp as _mcp

        return _mcp
    if name == "_detect_project_id":
        from memos.server.mcp import _detect_project_id as _fn

        return _fn
    raise AttributeError(f"module 'memos' has no attribute {name!r}")


# 向后兼容别名（v0.4.2 改名，v0.5.0 移除）
LongTermMemory = ContextMemory

__all__ = [
    "__version__",
    "ContextMemory",
    "LongTermMemory",  # 向后兼容别名
    "SIMILARITY_THRESHOLD",
    "DEFAULT_DECAY_LAMBDA",
    "MemoryExtractor",
    "format_conversation",
    "_estimate_tokens",
    "mcp",
    "_detect_project_id",
    "app",
    "main",
]
