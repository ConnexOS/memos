from memos._version import __version__


def __getattr__(name: str):
    """惰性加载 server 模块，避免未安装 pywin32 时导入失败。"""
    if name == "mcp":
        from memos.server.mcp import mcp as _mcp

        return _mcp
    if name == "_detect_project_id":
        import warnings

        warnings.warn(
            "_detect_project_id 已废弃，仅作 SSE 连接前的 project_id 兜底，"
            "请使用 resolve_project_id() 从 .memos-project 读取",
            DeprecationWarning,
            stacklevel=2,
        )
        from memos.server.mcp import _detect_project_id as _fn

        return _fn
    raise AttributeError(f"module 'memos' has no attribute {name!r}")


__all__ = [
    "__version__",
    "mcp",
    "_detect_project_id",
]
