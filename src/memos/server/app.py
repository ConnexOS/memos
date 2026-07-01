"""Unified 模式下的 FastAPI 应用工厂。

合并 Dashboard + MCP Handler + Hook Handler 到同一进程。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import config
from ..engine.memory import ContextMemory
from .task_handler import TaskEvalQueue

logger = logging.getLogger(__name__)
_MEMOS_LOG_PREFIX = f"[MEMOS v{__import__('memos').__version__}]"

# F2: TaskEvalQueue 全局实例（lifespan 中注入依赖后启动）
_task_queue = TaskEvalQueue()

# 优雅关闭信号 — 通知 SSE 等长连接主动退出
_shutdown_event = asyncio.Event()


def _get_llm_caller():
    """获取 LLM 调用函数（适配 MEMOS LLM 端点配置）。"""
    import requests

    def _call_llm(system_prompt: str, user_prompt: str) -> str | None:
        llm_url = f"{config.llm.api_base.rstrip('/')}/chat/completions"
        payload = {
            "model": config.llm.active_endpoint.model or "default",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        headers = {}
        if config.llm.api_key:
            headers["Authorization"] = f"Bearer {config.llm.api_key}"
        try:
            resp = requests.post(llm_url, json=payload, headers=headers, timeout=config.llm.request_timeout)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error("LLM 调用失败: %s", e)
            return None

    return _call_llm


async def _shutdown_with_timeout(app: FastAPI, timeout: int = 5):
    """带超时的关闭包装（v3.0 NB2/NB3）"""
    try:
        await asyncio.wait_for(_do_shutdown(app), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("关闭超时（>%ss），强制退出", timeout)


async def _do_shutdown(app: FastAPI):
    """执行关闭逻辑"""
    if hasattr(app.state, "context_memory") and app.state.context_memory is not None:
        app.state.context_memory.close()
    logger.info("memos server 优雅关闭")


def _make_lifespan(collection_name: str = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """FastAPI 生命周期管理（v3.0 修订）"""
        nonlocal collection_name
        # 测试环境：优先使用环境变量指定的 collection，避免污染生产数据
        if collection_name is None:
            collection_name = os.environ.get("MEMOS_TEST_COLLECTION")
        # === 启动阶段 ===
        logger.info(
            f"{_MEMOS_LOG_PREFIX} 启动中 mode=unified | http://%s:%s",
            config.server.host,
            config.server.port,
        )

        context_memory = ContextMemory(collection_name=collection_name)
        context_memory.warmup()
        app.state.context_memory = context_memory
        logger.info(f"{_MEMOS_LOG_PREFIX} ChromaDB 已连接")

        # 首次启动自动创建 admin 用户
        from ..web.auth import create_admin_on_first_start

        admin_token = create_admin_on_first_start()
        if admin_token:
            logger.info("[MEMOS] 首次启动, 已创建 admin 用户. Token (前缀): %s...", admin_token[:8])
            # 完整 token 仅输出到控制台（stderr），不写入日志文件
            print(f"\n{'=' * 50}", file=sys.stderr)
            print("  首次启动: 管理员 Token 已生成", file=sys.stderr)
            print(f"  Token: {admin_token}", file=sys.stderr)
            print("  请妥善保存此 Token，它不会再次显示", file=sys.stderr)
            print(f"{'=' * 50}\n", file=sys.stderr)

        # 注入 ContextMemory 到 server/mcp 模块（NC5 — Phase 2.2）
        from ..server.mcp import set_memory as _inject_memory

        _inject_memory(context_memory)

        logger.info(
            f"{_MEMOS_LOG_PREFIX} 嵌入模型已加载 | %s (%s)",
            config.model.name,
            config.model.vector_dim,
        )

        # F2: 注入 TaskEvalQueue 依赖并启动
        _task_queue._memory = context_memory
        _task_queue._llm_caller = _get_llm_caller()
        _task_queue.start()
        app.state.task_queue = _task_queue
        logger.info(f"{_MEMOS_LOG_PREFIX} TaskEvalQueue 已启动")

        # F6: 启动 SchedulerThread（每日 23:00 自动生成简报 + TTL 遗忘扫描）
        from ..dashboard import init_scheduler

        init_scheduler(context_memory)
        logger.info(f"{_MEMOS_LOG_PREFIX} SchedulerThread 已启动")

        yield

        # === 关闭阶段 ===
        # 通知长连接（SSE 等）主动退出，等待最长检测周期后自然关闭
        _shutdown_event.set()
        from ..web.routes.v2_routes import _get_sse_shutdown_ev

        _get_sse_shutdown_ev().set()
        logger.info("[MEMOS] 关闭信号已广播，等待长连接退出")
        await asyncio.sleep(2.5)  # 覆盖 SSE 内部 2s 检测周期 + 缓冲
        _task_queue.stop()
        logger.info(f"{_MEMOS_LOG_PREFIX} TaskEvalQueue 已停止")
        await _shutdown_with_timeout(app, timeout=5)

    return lifespan


def _derive_secret_key() -> str:
    """首次运行时自动生成并持久化 secret_key"""
    import secrets

    from ..config import get_memos_home

    key_file = get_memos_home() / "etc" / "secret_key.txt"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(64)
    key_file.write_text(key, encoding="utf-8")
    return key


def create_unified_app(collection_name: str = None) -> FastAPI:
    """创建 unified 模式的 FastAPI 应用

    Args:
        collection_name: ChromaDB collection 名称，为 None 时优先使用
                         MEMOS_TEST_COLLECTION 环境变量，最后回退到默认配置
    """
    app = FastAPI(title="长时记忆系统（Unified）", lifespan=_make_lifespan(collection_name))

    # FR3: mcp_handler + hook_handler ready (Phase 2.2 + 2.3)
    from ..server.hook_handler import router as hook_router
    from ..server.mcp_handler import InjectProjectContextMiddleware

    # 中间件：注入 project_id 到 contextvars（v3.0 NB1）
    # 使用原始 ASGI 中间件替代 @app.middleware("http")，避免 BaseHTTPMiddleware shutdown 崩溃
    app.add_middleware(InjectProjectContextMiddleware)

    # 复用现有 Dashboard 中间件
    from ..web.app import AuthASGIMiddleware

    app.add_middleware(AuthASGIMiddleware)

    # 复用现有 Dashboard ProjectContext 中间件
    from ..web.middleware.project_context import ProjectContextMiddleware

    app.add_middleware(ProjectContextMiddleware)

    # Session 中间件（评审 B4）：login/logout 端点依赖 request.session
    from starlette.middleware.sessions import SessionMiddleware

    _secret_key = config.auth.secret_key or _derive_secret_key()
    app.add_middleware(SessionMiddleware, secret_key=_secret_key)

    # 注册 Dashboard 路由（复用 web 子包）
    from ..web.app import register_routes

    register_routes(app)

    # 挂载 SSE MCP 应用，通过 ProjectAwareSSEWrapper 支持项目隔离
    from ..server.mcp import mcp
    from ..server.sse_wrapper import ProjectAwareSSEWrapper

    wrapper = ProjectAwareSSEWrapper(mcp.sse_app())
    app.mount("/mcp", wrapper)

    # 注册 Hook HTTP 路由
    # Phase 2.3 启用
    app.include_router(hook_router, prefix="/api/hooks")

    # 健康检查端点
    from ..server.mcp_handler import health

    app.add_api_route("/api/health", health, methods=["GET"])

    # F2: Task Eval 接收端点（Stop Hook 转发 TASK_EVAL → 异步队列）

    @app.post("/api/task/eval")
    async def receive_task_eval(request: Request):
        body = await request.json()
        task_eval = body.get("task_eval")
        session_id = body.get("session_id", "")
        project_id = body.get("project_id", "")

        if not task_eval:
            return JSONResponse({"error": "缺少 task_eval"}, status_code=400)

        for key in ("done", "todo", "blocked"):
            if key not in task_eval:
                task_eval[key] = []

        _task_queue.enqueue(task_eval, session_id, project_id)
        return JSONResponse({"status": "queued"}, status_code=200)

    logger.info("MEMOS Unified Server 初始化完成")
    return app
