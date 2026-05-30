"""MCP Server 入口。"""

import logging
from pathlib import Path

from memos.server.mcp import mcp

_log_file = Path(__file__).resolve().parents[4] / "data" / "logs" / "mcp_server.log"
_log_file.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(_log_file), encoding="utf-8", mode="a"),
        logging.StreamHandler(),
    ],
)
mcp.run()
