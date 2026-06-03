"""MCP Server 入口。"""

import logging

from memos.server.mcp import mcp

# 文件日志由 mcp.py 模块级统一设置，__main__ 仅配控制台
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
mcp.run()
