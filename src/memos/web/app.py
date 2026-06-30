"""MEMOS Dashboard — FastAPI 应用入口。

路由模块在 routes/ 目录下按功能域拆分（v0.4.3 架构重整）。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from ..config import config
from ..engine.memory import ContextMemory
from ..errors import ChromaDBError, MemoError, http_status_for
from ..i18n import _ as _i18n
from .auth import verify_session_token
from .utils import detect_project_id

# 确保所有 memos.* logger 的 info 日志能输出
# 注：uvicorn 启动后可能通过 dictConfig 覆盖 root logger 配置，
# 因此直接为 memos logger 添加显式 handler（不受 uvicorn dictConfig 影响）
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_memos_logger = logging.getLogger('memos')
_memos_logger.handlers.clear()
_memos_logger.addHandler(logging.StreamHandler(sys.stderr))
_memos_logger.handlers[0].setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
_memos_logger.setLevel(logging.INFO)
_memos_logger.propagate = False
logger = logging.getLogger(__name__)

# Jinja2 模板目录
_templates_dir = Path(__file__).parent / "templates"
_jinja_loader = FileSystemLoader(str(_templates_dir))
_jinja_env = Environment(loader=_jinja_loader, auto_reload=True)
_jinja_env.globals["_"] = _i18n
templates = Jinja2Templates(env=_jinja_env)

# 系统状态缓存（15s TTL）
_system_status_cache = {"llama_server_ok": False, "cached_at": 0.0}
_status_cache_lock = threading.Lock()

# 项目列表缓存（30s TTL）
_projects_cache = {"projects": None, "cached_at": 0.0}
_projects_cache_lock = threading.Lock()


def _invalidate_projects_cache():
    """失效项目列表缓存，下次访问时重新加载。"""
    with _projects_cache_lock:
        _projects_cache["cached_at"] = 0.0


# 公开路径（无需 session 认证，Hook 使用 X-Auth-Token 头）
_PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/hooks/prompt", "/api/hooks/stop", "/api/health"}

# 知识库类型（用于项目统计）
_KB_TYPES = {"solution", "decision", "lesson", "process", "todo", "task", "briefing"}


def _get_projects_from_db(mem: "ContextMemory") -> list[dict]:
    now = time.time()
    ttl = config.dashboard.projects_cache_ttl
    with _projects_cache_lock:
        if _projects_cache["projects"] and now - _projects_cache["cached_at"] < ttl:
            return _projects_cache["projects"]
    try:
        result = mem.store.get(include=["metadatas"], limit=10000)
    except ChromaDBError:
        logger.warning("获取项目列表失败（ChromaDB 索引不一致），返回缓存或空列表")
        with _projects_cache_lock:
            return _projects_cache["projects"] or []
    current_id = detect_project_id()
    current_name = Path.cwd().name
    project_map: dict[str, dict] = {}
    for meta in result["metadatas"]:
        pid = meta.get("project_id", detect_project_id())
        is_kb = meta.get("type") in _KB_TYPES
        t = meta.get("type", "unknown")
        # 统一时间戳为数值，兼容历史 ISO 字符串（v0.5.0 hook_handler 遗留）
        raw_ts = meta.get("timestamp", 0)
        if isinstance(raw_ts, str):
            try:
                ts = float(raw_ts) if raw_ts.replace(".", "", 1).replace("-", "", 1).isdigit() else 0
            except (ValueError, TypeError):
                ts = 0
        else:
            ts = float(raw_ts) if raw_ts else 0
        if pid in project_map:
            entry = project_map[pid]
            entry["memory_count"] += 1
            entry["by_type"][t] = entry["by_type"].get(t, 0) + 1
            if is_kb:
                entry["knowledge_count"] += 1
            if ts > entry["latest_time"]:
                entry["latest_time"] = ts
            # 后续记录中有有效项目名时，覆盖PID占位符
            if entry.get("project_name") in (None, "", pid):
                pname = meta.get("project_name")
                if pname and pname != pid:
                    entry["project_name"] = pname
        else:
            pname = meta.get("project_name")
            if not pname or pname == pid:
                pname = None  # 暂缺，待补全
            project_map[pid] = {
                "project_id": pid,
                "project_name": pname,
                "memory_count": 1,
                "knowledge_count": 1 if is_kb else 0,
                "by_type": {t: 1},
                "latest_time": ts,
            }
    # 补全无项目名的条目
    for pid, entry in project_map.items():
        if not entry["project_name"]:
            entry["project_name"] = current_name if pid == current_id else pid
    projects = sorted(project_map.values(), key=lambda x: -x["latest_time"])
    with _projects_cache_lock:
        _projects_cache["projects"] = projects
        _projects_cache["cached_at"] = now
    return projects


class AuthASGIMiddleware:
    """全局鉴权 ASGI 中间件：/api/* 路径需登录，/login 和静态资源豁免。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if config.auth.disable:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path

        if path in _PUBLIC_PATHS or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        token_str = request.cookies.get("memos_session")
        if not token_str:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token_str = auth_header[7:]

        if token_str:
            # 先检查 JWT session cookie（无需 token_hash 守卫，verify_session_token 仅依赖 secret_key）
            if verify_session_token(token_str, config.auth.secret_key):
                await self.app(scope, receive, send)
                return
            # 再检查 users.json 多用户 Token
            from ..web.auth import verify_token_against_users

            user = verify_token_against_users(token_str)
            if user:
                scope["user"] = user
                await self.app(scope, receive, send)
                return

        response = JSONResponse({"detail": "未登录，请访问 /login"}, status_code=401)
        await response(scope, receive, send)


def register_routes(app: FastAPI) -> None:
    """注册所有 Dashboard 路由（供 unified 模式工厂调用）。"""

    # 开发模式禁用静态文件缓存
    class _NoCacheStaticFiles(StaticFiles):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

        async def get_response(self, path: str, scope):
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response

    app.mount(
        "/static",
        _NoCacheStaticFiles(directory=str(_templates_dir / "static")),
        name="static",
    )

    @app.exception_handler(MemoError)
    def memo_error_handler(request: Request, exc: MemoError):
        logger.warning("MemoError code=%s message=%s", exc.code, exc.message)
        return JSONResponse(
            status_code=http_status_for(exc),
            content=exc.to_dict(),
        )

    # --- 注册所有功能域路由 ---
    from .routes.auth import router as auth_router
    from .routes.backups import router as backups_router
    from .routes.config_routes import router as config_router
    from .routes.conversations import router as conversations_router
    from .routes.inbox import router as inbox_router
    from .routes.llm import router as llm_router
    from .routes.memories import router as memories_router
    from .routes.notifications import router as notifications_router
    from .routes.pages import router as pages_router
    from .routes.prompts import router as prompts_router
    from .routes.search import router as search_router
    from .routes.suggestions import router as suggestions_router
    from .routes.system import router as system_router
    from .routes.todos import router as todos_router

    app.include_router(pages_router)
    app.include_router(auth_router)
    app.include_router(memories_router)
    app.include_router(search_router)
    app.include_router(conversations_router)
    app.include_router(prompts_router)
    app.include_router(config_router)
    app.include_router(backups_router)
    app.include_router(notifications_router)
    app.include_router(llm_router)
    app.include_router(suggestions_router)
    app.include_router(system_router)
    app.include_router(todos_router)
    app.include_router(inbox_router)

    # v0.6.0: v2 API 路由
    from .routes.v2_routes import router as v2_router

    app.include_router(v2_router)

    logger.info("Dashboard 路由注册完成（模块化架构 %s）", __import__("memos").__version__)


# --- 向后兼容垫片（测试用） ---
# v0.5.0 起模块级 app 已移除，统一使用 create_unified_app()。
# 以下惰性加载垫片供遗留测试代码继续 import from memos.web.app。


def __getattr__(name: str):
    if name == "app":
        from memos.server.app import create_unified_app

        _app = create_unified_app()
        return _app
    if name == "main":

        def _main():
            """启动 unified server（legacy main 垫片）"""
            import uvicorn

            from ..config import config as _cfg

            uvicorn.run(
                create_unified_app(),
                host=_cfg.server.host,
                port=_cfg.server.port,
            )

        return _main
    raise AttributeError(f"module 'memos.web.app' has no attribute {name!r}")
