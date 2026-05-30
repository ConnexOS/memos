"""Dashboard 路由模块 (v0.4.3 架构重整)

路由按功能域拆分，各模块独立 APIRouter。
在 dashboard/__init__.py 中通过 app.include_router() 统一注册。

路由模块:
  pages.py          — GET /, /login, /favicon.ico
  auth.py           — POST /api/auth/login
  memories.py       — /api/memories/* CRUD + 归档 + 批量 + 导入导出
  search.py         — POST /api/search
  conversations.py  — /api/conversations/* + extract + daily-review
  prompts.py        — /api/prompts/* 提示词管理
  config_routes.py  — /api/config/* 配置管理
  backups.py        — /api/backup/* 备份管理
  notifications.py  — /api/notifications/* + /notifications
  llm.py            — /api/llm/* 端点管理
  system.py         — /api/status, /api/vacuum, /api/projects, /api/stats
"""
