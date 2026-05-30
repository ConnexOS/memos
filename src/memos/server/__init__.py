"""MEMOS MCP 服务层。

v0.4.3 架构重整 Phase 10。
"""

from memos.server.mcp import _detect_project_id, _reset_for_test, mcp

__all__ = ["mcp", "_detect_project_id", "_reset_for_test"]
