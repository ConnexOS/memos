"""MEMOS Web 仪表板 —— FastAPI 应用 + 路由 + 模板 + 认证。

v0.4.3 Phase 10：整合 dashboard/ + auth.py + templates/ 到 web/ 子包。
"""

from memos.web.app import app, main

__all__ = ["app", "main"]
