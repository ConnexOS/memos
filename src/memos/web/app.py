"""MEMOS Dashboard — FastAPI 应用入口。

路由模块在 routes/ 目录下按功能域拆分（v0.4.3 架构重整）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

import memos

from ..config import config
from ..engine.memory import ContextMemory
from ..errors import ChromaDBError, MemoError, http_status_for
from ..i18n import _ as _i18n
from .auth import verify_session_token
from .utils import detect_project_id

# 确保所有 memos.* logger 的 info 日志能输出
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
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


# 公开路径（无需认证）
_PUBLIC_PATHS = {"/login", "/api/auth/login"}

# 知识库类型（用于项目统计）
_KB_TYPES = {"fact", "decision", "preference", "todo", "bug_fix", "feature_design", "code_optimize", "tech_knowledge"}


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
        if pid in project_map:
            project_map[pid]["memory_count"] += 1
            if is_kb:
                project_map[pid]["knowledge_count"] += 1
            ts = meta.get("timestamp", 0)
            if ts > project_map[pid]["latest_time"]:
                project_map[pid]["latest_time"] = ts
        else:
            pname = meta.get("project_name")
            if not pname or pname == pid:
                pname = current_name if pid == current_id else pid
            project_map[pid] = {
                "project_id": pid,
                "project_name": pname,
                "memory_count": 1,
                "knowledge_count": 1 if is_kb else 0,
                "latest_time": meta.get("timestamp", 0),
            }
    projects = sorted(project_map.values(), key=lambda x: -x["latest_time"])
    with _projects_cache_lock:
        _projects_cache["projects"] = projects
        _projects_cache["cached_at"] = now
    return projects


async def _todo_reminder_loop(app: FastAPI):
    """定时待办提醒：每日 daily_todo_time 汇总通知。"""
    from datetime import datetime, timedelta

    from ..features.notifications import get_notification_logger

    while True:
        try:
            now = datetime.now()
            target_str = config.memory.daily_todo_time  # "18:00"
            parts = target_str.split(":")
            target = now.replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            mem = app.state.mem
            current_pid = detect_project_id()
            pending = mem.count_memories(
                where={"type": "todo", "todo_status": "pending", "project_id": current_pid}
            )
            if pending > 0:
                notifier = get_notification_logger()
                notifier.notify(
                    type="todo_reminder",
                    title=f"待办提醒 — {pending} 条未完成",
                    message=f"你有 {pending} 条待办事项未完成",
                    metadata={"pending_count": pending},
                )
                logger.info("待办提醒推送: %d 条待处理", pending)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("待办提醒循环异常: %s", e)
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import multiprocessing
    import os as _os

    app.state.mem = ContextMemory()
    app.state.mem.warmup()
    app.state.config = config
    # 启动时清理过期备份锁
    try:
        from memos.features.backup import clean_stale_lock

        clean_stale_lock()
    except Exception:
        pass
    try:
        from memos.features.usage import usage_logger

        usage_logger.cleanup()
    except Exception:
        pass
    # v0.4.5 R2: 启动待办定时提醒后台任务
    reminder_task = asyncio.create_task(_todo_reminder_loop(app))

    try:
        yield
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    finally:
        reminder_task.cancel()
        try:
            await reminder_task
        except asyncio.CancelledError:
            pass
        try:
            if hasattr(app.state, "mem") and app.state.mem is not None:
                app.state.mem.close()
        except Exception:
            pass
        _current = multiprocessing.current_process()
        if _current.name != "MainProcess":
            _os._exit(0)


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

        if token_str and verify_session_token(token_str, config.auth.secret_key):
            await self.app(scope, receive, send)
            return

        response = JSONResponse({"detail": "未登录，请访问 /login"}, status_code=401)
        await response(scope, receive, send)


# --- FastAPI 应用实例 ---
app = FastAPI(title="长时记忆系统仪表板", lifespan=lifespan)

# Middleware order (LIFO): ProjectContext(extract pid, 1st) -> Auth(authenticate, 2nd) -> routes
app.add_middleware(AuthASGIMiddleware)
from .middleware.project_context import ProjectContextMiddleware
app.add_middleware(ProjectContextMiddleware)


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
# 路由导入必须在 app 定义之后（避免循环导入：路由模块 → .. → __init__）
from .routes.auth import router as auth_router  # noqa: E402
from .routes.backups import router as backups_router  # noqa: E402
from .routes.config_routes import router as config_router  # noqa: E402
from .routes.conversations import router as conversations_router  # noqa: E402
from .routes.llm import router as llm_router  # noqa: E402
from .routes.memories import router as memories_router  # noqa: E402
from .routes.notifications import router as notifications_router  # noqa: E402
from .routes.pages import router as pages_router  # noqa: E402
from .routes.prompts import router as prompts_router  # noqa: E402
from .routes.search import router as search_router  # noqa: E402
from .routes.suggestions import router as suggestions_router  # noqa: E402
from .routes.system import router as system_router  # noqa: E402
from .routes.todos import router as todos_router  # noqa: E402

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

logger.info("Dashboard 初始化完成（模块化架构 %s）", memos.__version__)


def main():
    """CLI 入口：启动 Dashboard 服务器"""
    import uvicorn

    uvicorn.run(
        "memos.dashboard:app",
        host=config.dashboard.host,
        port=config.dashboard.port,
        reload=False,
        log_level="info",
    )
