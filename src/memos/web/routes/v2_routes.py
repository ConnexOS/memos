"""L5 Dashboard v2 API 路由 —— 六面板数据接口。"""

import asyncio
import itertools
import json
import logging
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

from ...config import config
from ...errors import ChromaDBError
from ...features.event_bus import touch_event
from ..dependencies import get_project_id

logger = logging.getLogger(__name__)
router = APIRouter()


class _SSECleanResponse(StreamingResponse):
    """SSE 专用响应类：绕过 anyio TaskGroup 以避免 uvicorn 关闭时异常栈。

    Starlette StreamingResponse 在 ASGI spec < 2.4（uvicorn HTTP = 2.3）时
    使用 anyio.create_task_group() 管理 listen_for_disconnect + stream_response
    两个并发任务。uvicorn 关闭时 listen_for_disconnect 的 CancelledError 经
    TaskGroup 传播到控制台，产生 "Cancel 1 running task(s)" 异常栈。

    此类强制使用 ASGI >= 2.4 简化路径（仅 stream_response），将关闭/断开的
    异常静默捕获，消除控制台噪音。代价：失去即时客户端断开检测（对本地
    Dashboard 工具无实际影响，SSE 生成器自身有 shutdown_ev 轮询机制）。
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.stream_response(send)
            if self.background is not None:
                await self.background()
            return

        try:
            await self.stream_response(send)
        except asyncio.CancelledError:
            pass
        except OSError:
            pass

        if self.background is not None:
            await self.background()

# 内存缓存（5s TTL）
_activity_log_cache: dict = {"data": None, "cached_at": 0.0, "cache_key": ""}


def _get_memory(request: Request = None):
    """获取 ContextMemory 实例，优先复用 app.state 中的统一单例。"""
    if request is not None:
        mem = getattr(request.app.state, "context_memory", None)
        if mem is not None:
            return mem
    # 回退到模块级单例
    if not hasattr(_get_memory, "_instance"):
        from ...engine.memory import ContextMemory

        _get_memory._instance = ContextMemory()  # type: ignore
    return _get_memory._instance  # type: ignore


@router.get("/api/v2/activity-log")
async def get_activity_log(
    request: Request,
    date: str = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    project_id: str = None,
):
    """获取活动日志（当日+前 3 天合并，5s 缓存）。按 project_id 隔离。"""
    from ...features.activity_log import read_events

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    cache_key = f"{date}_{page}_{page_size}_{project_id or ''}"
    now = time.time()
    if _activity_log_cache["cache_key"] == cache_key and now - _activity_log_cache["cached_at"] < 5:
        return JSONResponse(_activity_log_cache["data"])

    base = datetime.strptime(date, "%Y-%m-%d")
    all_items = []
    for i in range(4):
        d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
        result = read_events(project_id=project_id, date=d, page=1, page_size=100)
        all_items.extend(result.get("items", []))

    all_items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

    # 过滤系统内部噪声事件（不展示在 Dashboard 事件看板）
    _noise_events = {"hook_latency", "hook_error"}
    visible = [item for item in all_items if item.get("event") not in _noise_events]

    # 去重：连续 3 秒内 event+query 相同的条目只保留第一条
    deduped = []
    last_key = None
    last_ts = 0
    for item in visible:
        key = (item.get("event"), item.get("query", ""), str(item.get("type", "")), str(item.get("types", "")))
        ts = item.get("timestamp", 0)
        if key == last_key and abs(ts - last_ts) < 3:
            continue
        deduped.append(item)
        last_key = key
        last_ts = ts

    # 数据清洗：raw match_types 包含 ChromaDB $in 结构，提取可读字符串列表
    for item in deduped:
        mt = item.get("match_types")
        if isinstance(mt, list) and mt and isinstance(mt[0], dict):
            item["match_types"] = [str(v) for d in mt for v in d.values() for v2 in (v if isinstance(v, list) else [v])]
        if isinstance(item.get("timestamp"), (int, float)):
            item["timestamp"] = int(item["timestamp"])

    total = len(deduped)
    start = (page - 1) * page_size
    end = start + page_size

    data = {"items": deduped[start:end], "total": total, "page": page, "page_size": page_size}
    _activity_log_cache["data"] = data
    _activity_log_cache["cached_at"] = now
    _activity_log_cache["cache_key"] = cache_key
    return JSONResponse(data)


@router.get("/api/v2/watchlist")
async def get_watchlist(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """获取待关注列表（ChromaDB type=watchlist AND processed=false）。"""
    mem = _get_memory(request)
    results = mem.list_memories(type_filter="watchlist", limit=page_size, offset=(page - 1) * page_size)

    now = time.time()
    archived_count = 0
    for item in results:
        meta = item.get("metadata", {})
        created_at = meta.get("timestamp", 0)
        # F5: 使用 status 替代 archived bool
        if created_at > 0 and now - created_at > 30 * 86400 and meta.get("status", "active") != "archived":
            mem.update_memory(item["id"], new_metadata={"status": "archived"})
            archived_count += 1

    return JSONResponse({
        "items": results,
        "total": len(results),
        "page": page,
        "page_size": page_size,
        "archived_count": archived_count,
    })


@router.post("/api/v2/watchlist/{memory_id}/structurize")
async def watchlist_structurize(memory_id: str, request: Request):
    """调用 MEMOS LLM 将 watchlist 内容结构化为指定类型。"""
    body = await request.json()
    target_type = body.get("type")
    if target_type not in ("solution", "decision", "lesson", "process"):
        return JSONResponse({"error": "无效类型"}, status_code=400)

    mem = _get_memory(request)
    existing = mem.get_memory(memory_id)
    if existing is None:
        return JSONResponse({"error": "记忆不存在"}, status_code=404)

    original_text = existing.get("document", "")

    system_prompt = (
        f"你是一个知识结构化引擎。将以下待关注内容提炼为 {target_type} 类型的结构化知识。\n\n"
        f"输出格式必须是符合 {target_type} 类型的结构化 Markdown，简洁清晰。"
    )
    user_prompt = f"请将以下内容结构化：\n\n{original_text}"

    llm_result = None
    try:
        _caller = _get_llm_caller_simple()
        if _caller:
            llm_result = _caller(system_prompt, user_prompt)
    except Exception as e:
        logger.warning("LLM 结构化失败: %s", e)

    structured = llm_result or original_text
    return JSONResponse({"structured_text": structured, "source": "llm" if llm_result else "original"})


def _get_llm_caller_simple():
    """简易 LLM 调用函数（供 structurize 端点使用）。"""
    import requests

    def _call(system_prompt: str, user_prompt: str) -> str | None:
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
        except Exception:
            return None

    return _call


@router.post("/api/v2/watchlist/{memory_id}/to-knowledge")
async def watchlist_to_knowledge(memory_id: str, request: Request):
    """将待关注转为知识。body: {type, preview_edit}"""
    body = await request.json()
    target_type = body.get("type")
    preview_edit = body.get("preview_edit")

    if target_type not in ("solution", "decision", "lesson", "process"):
        return JSONResponse({"error": "无效类型，可选: solution/decision/lesson/process"}, status_code=400)

    mem = _get_memory(request)
    existing = mem.get_memory(memory_id)
    if existing is None:
        return JSONResponse({"error": "记忆不存在"}, status_code=404)

    text = preview_edit or existing.get("document", "")
    meta = existing.get("metadata", {})
    new_meta = {
        "type": target_type,
        "source": "watchlist_conversion",
        "project_id": meta.get("project_id", ""),
        "timestamp": time.time(),
    }
    new_id = mem.remember(text, metadata=new_meta)
    if new_id:
        mem.update_memory(memory_id, new_metadata={"processed": True, "processed_at": time.time()})

    return JSONResponse({"id": new_id, "status": "converted"})


@router.post("/api/v2/watchlist/{memory_id}/ignore")
async def watchlist_ignore(memory_id: str, request: Request):
    mem = _get_memory(request)
    existing = mem.get_memory(memory_id)
    if existing is None:
        return JSONResponse({"error": "记忆不存在"}, status_code=404)
    mem.update_memory(memory_id, new_metadata={"processed": True, "processed_at": time.time()})
    return JSONResponse({"status": "ignored"})


@router.post("/api/v2/watchlist/{memory_id}/note")
async def watchlist_note(memory_id: str, request: Request):
    body = await request.json()
    note = body.get("note", "")
    mem = _get_memory(request)
    existing = mem.get_memory(memory_id)
    if existing is None:
        return JSONResponse({"error": "记忆不存在"}, status_code=404)
    old_meta = existing.get("metadata", {})
    old_meta["note"] = note
    mem.update_memory(memory_id, new_metadata=old_meta)
    return JSONResponse({"status": "noted"})


@router.get("/api/v2/task/current")
async def get_current_task(request: Request):
    """获取当前 active task（按 updated_at 倒序取最新）。"""
    mem = _get_memory(request)
    results = mem.list_memories(type_filter="task", limit=1)
    if not results:
        return JSONResponse({"task": None, "message": "上次会话未记录 task"})

    task = results[0]
    meta = task.get("metadata", {})
    # F5: 使用 status 判断是否活跃
    if meta.get("status", "active") != "active" or meta.get("paused", False):
        return JSONResponse({"task": None, "message": "所有 task 已归档或暂停"})

    return JSONResponse({"task": task})


@router.get("/api/v2/tasks")
async def list_tasks(
    request: Request,
    project_id: str = Depends(get_project_id),
    status: str = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """获取 task 列表（含活跃/已完成/已暂停分类），支持按状态筛选。"""
    mem = _get_memory(request)
    results = mem.list_memories(type_filter="task", project_id=project_id, limit=limit, offset=offset)

    # 按 status 分组（v0.7.1: 增加 pending 分组）
    active = []
    pending = []
    completed = []
    paused_archived = []
    for r in results:
        meta = r.get("metadata", {})
        s = meta.get("status", "active")
        is_paused = meta.get("paused", False)

        if s == "active" and not is_paused:
            active.append(r)
        elif s == "active" and is_paused:
            paused_archived.append(r)
        elif s == "pending":
            pending.append(r)
        elif s == "completed":
            completed.append(r)
        else:
            paused_archived.append(r)

    # 可选状态过滤
    if status == "active":
        tasks = active
    elif status == "completed":
        tasks = completed
    elif status in ("paused", "archived"):
        tasks = paused_archived
    else:
        tasks = results

    # 按创建时间倒序
    tasks.sort(key=lambda x: x.get("metadata", {}).get("created_at", 0), reverse=True)

    return JSONResponse({
        "tasks": tasks,
        "counts": {
            "active": len(active),
            "pending": len(pending),
            "completed": len(completed),
            "paused_archived": len(paused_archived),
            "total": len(results),
        },
    })


# ==== Task 管理端点 ====


@router.put("/api/v2/tasks/{task_id}")
async def update_task(task_id: str, body: dict, request: Request):
    """编辑 task 内容。"""
    mem = _get_memory(request)
    item = mem.get_memory(task_id)
    if not item:
        raise HTTPException(404, "记忆不存在")
    if item.get("metadata", {}).get("type") != "task":
        raise HTTPException(404, "该记忆不是 Task 类型")
    update_kwargs = {"ids": [task_id]}
    if "document" in body:
        update_kwargs["documents"] = [body["document"]]
    meta_update = {"updated_at": time.time()}
    if "context" in body:
        meta_update["context"] = body["context"]
    update_kwargs["metadatas"] = [meta_update]
    mem.store.update(**update_kwargs)
    touch_event("task")
    return {"ok": True}


@router.post("/api/v2/tasks/{task_id}/pause")
async def pause_task(task_id: str, request: Request):
    """暂停 task。"""
    mem = _get_memory(request)
    item = mem.get_memory(task_id)
    if not item:
        raise HTTPException(404, "记忆不存在")
    if item.get("metadata", {}).get("type") != "task":
        raise HTTPException(404, "该记忆不是 Task 类型")
    mem.store.update(ids=[task_id], metadatas=[{"paused": True, "updated_at": time.time()}])
    touch_event("task")
    return {"ok": True, "status": "paused"}


@router.post("/api/v2/tasks/{task_id}/resume")
async def resume_task(task_id: str, request: Request):
    """恢复 task（同时承担 reopen 职责：completed→active 同样适用）。"""
    mem = _get_memory(request)
    item = mem.get_memory(task_id)
    if not item:
        raise HTTPException(404, "记忆不存在")
    if item.get("metadata", {}).get("type") != "task":
        raise HTTPException(404, "该记忆不是 Task 类型")
    current_status = item.get("metadata", {}).get("status", "active")
    logger.info("resume task %s from status=%s", task_id[:8], current_status)
    mem.store.update(ids=[task_id], metadatas=[{"paused": False, "status": "active", "updated_at": time.time()}])
    touch_event("task")
    return {"ok": True, "status": "active"}


@router.post("/api/v2/tasks/{task_id}/complete")
async def complete_task(task_id: str, request: Request):
    """标记 task 为已完成。"""
    mem = _get_memory(request)
    item = mem.get_memory(task_id)
    if not item:
        raise HTTPException(404, "记忆不存在")
    if item.get("metadata", {}).get("type") != "task":
        raise HTTPException(404, "该记忆不是 Task 类型")
    mem.store.update(ids=[task_id], metadatas=[{
        "status": "completed", "paused": False, "updated_at": time.time(),
    }])
    touch_event("task")
    return {"ok": True, "status": "completed"}


@router.post("/api/v2/tasks/{task_id}/archive")
async def archive_task(task_id: str, request: Request):
    """归档 task。"""
    mem = _get_memory(request)
    item = mem.get_memory(task_id)
    if not item:
        raise HTTPException(404, "记忆不存在")
    if item.get("metadata", {}).get("type") != "task":
        raise HTTPException(404, "该记忆不是 Task 类型")
    mem.archive_memory(task_id)
    touch_event("task")
    return {"ok": True, "status": "archived"}


@router.delete("/api/v2/tasks/{task_id}")
async def delete_task(task_id: str, request: Request):
    """删除 task。"""
    mem = _get_memory(request)
    item = mem.get_memory(task_id)
    if not item:
        raise HTTPException(404, "记忆不存在")
    if item.get("metadata", {}).get("type") != "task":
        raise HTTPException(404, "该记忆不是 Task 类型")
    mem.store.delete(ids=[task_id])
    touch_event("task")
    return {"ok": True}


@router.post("/api/v2/tasks/{task_id}/activate")
async def activate_task(task_id: str, request: Request, project_id: str = Depends(get_project_id)):
    """激活指定 task（设为 active），原活跃 task 自动 completed。"""
    mem = _get_memory(request)
    item = mem.get_memory(task_id)
    if not item:
        raise HTTPException(404, "记忆不存在")
    if item.get("metadata", {}).get("type") != "task":
        raise HTTPException(404, "该记忆不是 Task 类型")

    # 1. 原活跃 task → completed
    active_tasks = mem.store.get(
        where={"$and": [{"type": "task"}, {"status": "active"}, {"project_id": project_id}]}
    )
    for tid in active_tasks.get("ids", []):
        if tid != task_id:
            mem.store.update(ids=[tid], metadatas=[
                {"status": "completed", "paused": False, "updated_at": time.time()}
            ])

    # 2. 目标 task → active
    mem.store.update(ids=[task_id], metadatas=[
        {"status": "active", "paused": False, "updated_at": time.time()}
    ])
    touch_event("task")
    return {"ok": True, "status": "active"}


@router.get("/api/v2/tasks/mode")
async def get_task_mode(request: Request, project_id: str = Depends(get_project_id)):
    """获取当前项目的任务模式: 'auto' 或 'manual'。"""
    from ...config.models import get_memos_home

    mode_file = get_memos_home() / "etc" / f".task_mode_{project_id}"
    mode = "manual"
    if mode_file.exists():
        try:
            data = json.loads(mode_file.read_text(encoding="utf-8"))
            mode = data.get("mode", "manual")
        except (json.JSONDecodeError, OSError):
            pass
    return JSONResponse({"mode": mode, "project_id": project_id})


@router.post("/api/v2/tasks/mode")
async def set_task_mode(request: Request, project_id: str = Depends(get_project_id)):
    """设置当前项目的任务模式。
    Body: {"mode": "auto"} 或 {"mode": "manual"}
    """
    from ...config.models import get_memos_home

    body = await request.json()
    mode = body.get("mode", "manual")
    if mode not in ("auto", "manual"):
        return JSONResponse({"error": "mode 必须是 'auto' 或 'manual'"}, status_code=400)

    mode_file = get_memos_home() / "etc" / f".task_mode_{project_id}"
    mode_file.parent.mkdir(parents=True, exist_ok=True)
    mode_file.write_text(json.dumps({"mode": mode}, ensure_ascii=False), encoding="utf-8")

    return JSONResponse({"ok": True, "mode": mode, "project_id": project_id})


@router.get("/api/v2/review")
async def get_review_list(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    confirmed: bool = None,
    type: str = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """获取待修正条目。"""
    mem = _get_memory(request)
    results = mem.list_memories(
        type_filter=type or None,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    now = time.time()
    cutoff = now - days * 86400
    filtered = []
    for item in results:
        meta = item.get("metadata", {})
        ts = meta.get("timestamp", 0)
        if ts < cutoff:
            continue
        if confirmed is False and meta.get("confirmed", False):
            continue
        if confirmed is True and not meta.get("confirmed", False):
            continue
        filtered.append(item)

    return JSONResponse({"items": filtered, "total": len(filtered), "page": page, "page_size": page_size})


def _make_briefing_response(b, display_date: str) -> JSONResponse:
    """构造简报响应体，统一处理 document 解析。"""
    doc = b.get("document", "")
    meta = b.get("metadata", {})
    try:
        parsed = json.loads(doc) if isinstance(doc, str) else doc
    except (json.JSONDecodeError, TypeError):
        parsed = {"summary": doc[:200]}
    return JSONResponse({
        "exists": True,
        "quality": meta.get("quality", "simple"),
        "source": meta.get("source", ""),
        "delivered": bool(meta.get("delivered", False)),
        "briefing_id": b.get("id", ""),
        "date": display_date,
        "briefing_date": meta.get("briefing_date", display_date),
        "content": parsed,
    })


@router.get("/api/v2/briefing/current")
async def get_current_briefing(
    request: Request,
    project_id: str = Depends(get_project_id),
    date: str = Query(default=None, description="查询日期 YYYY-MM-DD，不传则查最近5天最新简报"),
):
    """获取简报内容。

    传 date → 精确匹配指定日期
    不传 date → 取最近5天最新简报（简报工作台当前项目简报用）
    """
    from zoneinfo import ZoneInfo

    from ...config import get_local_timezone

    mem = _get_memory(request)
    now = datetime.now(ZoneInfo(get_local_timezone()))

    if date:
        # 精确匹配指定日期
        target_date = date
        briefings = mem.list_memories(type_filter="briefing", project_id=project_id, limit=10)
        for b in briefings:
            meta = b.get("metadata", {})
            if meta.get("briefing_date") == target_date:
                return _make_briefing_response(b, target_date)
        return JSONResponse({"exists": False, "date": target_date})
    else:
        # 取最近5天最新简报
        today = now.strftime("%Y-%m-%d")
        five_days_ago = (now - timedelta(days=5)).strftime("%Y-%m-%d")
        briefings = mem.list_memories(type_filter="briefing", project_id=project_id, limit=10)
        candidates = []
        for b in briefings:
            meta = b.get("metadata", {})
            bd = meta.get("briefing_date", "")
            if five_days_ago <= bd <= today:
                candidates.append(b)
        if not candidates:
            return JSONResponse({"exists": False, "date": today})
        candidates.sort(key=lambda x: x["metadata"].get("briefing_date", ""), reverse=True)
        latest = candidates[0]
        bd = latest["metadata"].get("briefing_date", today)
        return _make_briefing_response(latest, bd)


@router.get("/api/v2/briefing/history")
async def list_briefing_history(
    request: Request,
    project_id: str = Depends(get_project_id),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    since_date: str = None,
):
    """获取历史简报列表（不含 content 完整内容，展开时懒加载）。
    Args:
        limit: 每页条数（默认 20）
        offset: 偏移量
        since_date: 起始日期 "YYYY-MM-DD"，按 briefing_date >= since_date 过滤
    """
    mem = _get_memory(request)
    all_raw = mem.list_memories(type_filter="briefing", project_id=project_id, limit=500)
    filtered = []
    for b in all_raw:
        meta = b.get("metadata", {})
        bd = meta.get("briefing_date", "")
        if since_date and bd < since_date:
            continue
        doc = b.get("document", "")
        summary = ""
        try:
            parsed = json.loads(doc) if isinstance(doc, str) else doc
            summary = (parsed.get("summary") or "")[:200]
        except (json.JSONDecodeError, TypeError):
            summary = (doc or "")[:200]
        filtered.append({
            "briefing_date": bd,
            "id": b.get("id", ""),
            "quality": meta.get("quality", "simple"),
            "summary": summary,
            "session_count": meta.get("session_count", 0),
            "new_knowledge_count": meta.get("new_knowledge_count", 0),
            "task_done_count": meta.get("task_done_count", 0),
            "task_todo_count": meta.get("task_todo_count", 0),
            "generated_at": meta.get("generated_at", 0),
        })
    filtered.sort(key=lambda x: x["briefing_date"], reverse=True)
    total = len(filtered)
    page = filtered[offset:offset + limit]
    return JSONResponse({"briefings": page, "total": total})


@router.get("/api/v2/briefing/{briefing_id}")
async def get_briefing_detail(briefing_id: str, request: Request, project_id: str = Depends(get_project_id)):
    """获取单条简报完整内容（含 content 对象）。"""
    mem = _get_memory(request)
    result = mem.store.get(ids=[briefing_id], include=["metadatas", "documents"])
    if not result or not result.get("ids"):
        return JSONResponse({"error": "not_found"}, status_code=404)
    meta = result["metadatas"][0]
    doc = result["documents"][0]
    content = {}
    try:
        content = json.loads(doc) if isinstance(doc, str) else (doc or {})
    except (json.JSONDecodeError, TypeError):
        content = {"summary": (doc or "")[:200]}
    return JSONResponse({
        "id": result["ids"][0],
        "briefing_date": meta.get("briefing_date", ""),
        "quality": meta.get("quality", "simple"),
        "summary": (content.get("summary") or "")[:200],
        "content": content,
    })


@router.delete("/api/v2/briefing/{briefing_id}")
async def delete_briefing(briefing_id: str, request: Request, project_id: str = Depends(get_project_id)):
    """删除指定简报记录。"""
    mem = _get_memory(request)
    try:
        mem.delete_memory(briefing_id)
        return JSONResponse({"status": "deleted"})
    except Exception as e:
        logger.error("删除简报失败: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v2/briefing/generate")
async def manual_generate_briefing(request: Request, project_id: str = Depends(get_project_id)):
    """手动触发生成简报。支持可选参数：date, llm_endpoint, prompt_id。
    date 格式 YYYY-MM-DD，指定生成哪天的简报（数据范围为当日 00:00~24:00）。
    不传 date 时使用默认范围（今日 00:00~现在）。"""
    from ...dashboard.__init__ import _generate_full_briefing

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass  # 无 body 时使用默认配置

    logger.info("[简报] === 收到手动生成请求 (date=%s, llm_endpoint=%s, prompt_id=%s, project_id=%s) ===",
                body.get("date"), body.get("llm_endpoint"), body.get("prompt_id"), project_id)

    _generate_full_briefing(
        date=body.get("date"),
        llm_endpoint=body.get("llm_endpoint"),
        prompt_id=body.get("prompt_id"),
        memory_instance=getattr(request.app.state, 'context_memory', None),
    )
    # F9: SSE 事件总线通知
    try:
        from ...features.event_bus import touch_event as _touch

        _touch("briefing")
    except Exception:
        logger.debug("SSE 事件总线 touch 失败（briefing）", exc_info=True)
    return JSONResponse({"status": "generated"})


# ==== F9 SSE 实时推送 + 连接监测 ====

# 全局关闭标记：用于正常关闭或 Ctrl+C 时通知 SSE 生成器退出
_sse_shutdown_ev = None
_sse_conn_counter = itertools.count(1)
_sse_active_connections: dict[int, dict] = {}


def _get_sse_shutdown_ev():
    global _sse_shutdown_ev
    if _sse_shutdown_ev is None:
        _sse_shutdown_ev = asyncio.Event()
    return _sse_shutdown_ev


@router.get("/api/v2/sse-health")
async def sse_health():
    """SSE 连接状态监测端点（调试/监控用）。"""
    now = time.time()
    connections = []
    for cid, info in list(_sse_active_connections.items()):
        duration = now - info.get("started_at", now)
        connections.append({
            "id": cid,
            "client": info.get("client", "unknown"),
            "duration": round(duration, 1),
            "started_at": info.get("started_at", 0),
        })
    return JSONResponse({
        "active_connections": len(connections),
        "connections": connections,
        "shutdown": _sse_shutdown_ev.is_set() if _sse_shutdown_ev else False,
    })


@router.get("/api/v2/events")
async def sse_event_stream(request: Request):
    """SSE 端点：实时推送 Dashboard 面板更新通知。

    客户端通过 EventSource 连接，每 2 秒轮询事件时间戳总线。
    当某个事件类型的时间戳发生变化时，推送事件通知。
    每 15 秒发送 keepalive 保持连接。
    """
    from ...features.event_bus import get_event_timestamps

    shutdown_ev = _get_sse_shutdown_ev()

    conn_id = next(_sse_conn_counter)
    client_host = request.client.host if request.client else "unknown"
    start_time = time.time()

    _sse_active_connections[conn_id] = {"client": client_host, "started_at": start_time}
    logger.info("[SSE#%s] 客户端连接: client=%s", conn_id, client_host)

    async def event_generator():
        last_ts = get_event_timestamps()
        keepalive_counter = 0
        # 告知浏览器：连接关闭后 30s 自动重连
        yield "retry: 30000\n\n"
        try:
            while not shutdown_ev.is_set():
                for _ in range(7):
                    # 等待 2s 或被 shutdown 信号唤醒（任一先到）
                    task_s = asyncio.create_task(shutdown_ev.wait())
                    task_sleep = asyncio.create_task(asyncio.sleep(2))
                    done, pending = await asyncio.wait(
                        [task_s, task_sleep],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if shutdown_ev.is_set():
                        return
                    current_ts = get_event_timestamps()
                    for event_type, ts in current_ts.items():
                        if ts > last_ts.get(event_type, 0):
                            data = json.dumps({"type": event_type, "timestamp": ts}, ensure_ascii=False)
                            yield f"event: {event_type}\ndata: {data}\n\n"
                            last_ts[event_type] = ts

                keepalive_counter += 1
                # 每 5 次 keepalive（约 70s）记录一次活性日志
                if keepalive_counter % 5 == 0:
                    logger.info("[SSE#%s] 连接活性: keepalive#%d duration=%.1fs", conn_id, keepalive_counter, time.time() - start_time)
                yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _sse_active_connections.pop(conn_id, None)
            duration = time.time() - start_time
            if shutdown_ev.is_set():
                logger.info("[SSE#%s] 服务端关闭: client=%s duration=%.1fs", conn_id, client_host, duration)
            else:
                logger.info("[SSE#%s] 客户端断开: client=%s duration=%.1fs", conn_id, client_host, duration)

    return _SSECleanResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/api/v2/memories/{memory_id}/restore")
async def restore_memory_endpoint(memory_id: str, request: Request):
    """恢复已遗忘记忆为 active 状态，重置 updated_at。"""
    mem = request.app.state.context_memory
    try:
        mem.restore_memory(memory_id)
        updated_at = time.time()
        return {"ok": True, "status": "active", "updated_at": updated_at}
    except ChromaDBError as e:
        if "不存在" in str(e) or "未找到" in str(e):
            raise HTTPException(404, detail=str(e))
        raise HTTPException(400, detail=str(e))


@router.get("/api/v2/monitor/overview")
async def monitor_overview(request: Request, project_id: str = ""):
    """聚合监控面板数据：4 卡片状态 + 注入详情 + 指令面板。"""
    from ...config.models import get_memos_home
    from ...features.activity_log import read_events

    mem = request.app.state.context_memory
    now = time.time()

    # 从 project_id 获取：查询参数 → _project_id_ctx（统一出口）→ 空
    if not project_id:
        from ...server.mcp import _project_id_ctx as _pid_ctx
        project_id = _pid_ctx.get()

    # 读取 injected_records 文件
    injected_path = get_memos_home() / "etc" / f".injected_records_{project_id}.json"
    injection_records = []
    try:
        if injected_path.exists():
            data = json.loads(injected_path.read_text(encoding="utf-8"))
            injection_records = data.get("records", [])
    except Exception:
        pass

    # 读取 activity_log 最近 5 条用户建议记录
    activity_items = []
    try:
        events = read_events(project_id=project_id or None, page=1, page_size=50)
        activity_items = [
            e for e in events.get("items", [])
            if e.get("injection_type") == "manual"
        ][:5]
    except Exception:
        pass

    # 读取最新 task
    task_status = "none"
    task_detail = ""
    try:
        tasks = mem.list_memories(project_id=project_id, type_filter="task", where={"status": "active"}, limit=1)
        if tasks:
            task_meta = tasks[0].get("metadata", {})
            if task_meta.get("status") == "active":
                task_status = "active"
                content = tasks[0].get("document", "")
                try:
                    content_obj = json.loads(content) if content else {}
                    task_detail = content_obj.get("goal", content[:100])
                except (json.JSONDecodeError, TypeError):
                    task_detail = content[:100] if content else ""
            else:
                task_status = "inactive"
                content = tasks[0].get("document", "")
                try:
                    content_obj = json.loads(content) if content else {}
                    task_detail = content_obj.get("goal", content[:100])
                except (json.JSONDecodeError, TypeError):
                    task_detail = content[:100] if content else ""
    except Exception:
        pass

    # 读取简报状态（最近5天最新简报，联动简报工作台）
    briefing_status = "none"
    briefing_date = ""
    briefing_summary = ""
    briefing_id = ""
    briefing_delivered = False
    briefing_allow_toggle = False
    today_str = datetime.utcnow().strftime("%Y-%m-%d")  # fallback
    try:
        briefings = mem.list_memories(project_id=project_id, type_filter="briefing", limit=10)
        from zoneinfo import ZoneInfo

        from ...config import get_local_timezone
        now_local = datetime.now(ZoneInfo(get_local_timezone()))
        today_str = now_local.strftime("%Y-%m-%d")
        five_days_ago = (now_local - timedelta(days=5)).strftime("%Y-%m-%d")

        # 筛选最近5天简报
        candidates = []
        for item in briefings:
            meta = item.get("metadata", {})
            bd = meta.get("briefing_date", "")
            if five_days_ago <= bd <= today_str:
                candidates.append(item)

        if candidates:
            candidates.sort(key=lambda x: x["metadata"].get("briefing_date", ""), reverse=True)
            latest = candidates[0]
            meta = latest.get("metadata", {})
            briefing_date = meta.get("briefing_date", "")
            briefing_id = latest.get("id", "")
            briefing_delivered = bool(meta.get("delivered", False))
            doc = latest.get("document", "")
            try:
                obj = json.loads(doc) if doc else {}
                briefing_summary = obj.get("summary", doc[:200])
            except (json.JSONDecodeError, TypeError):
                briefing_summary = doc[:200]
            # 注入控制开关：5天内 quality=full 均可切换
            quality = meta.get("quality", "")
            briefing_allow_toggle = (quality == "full")

        # 检查注入记录中是否有 briefing
        briefing_injected = any(
            r.get("source_type") == "briefing" for r in injection_records
        )
        briefing_status = "injected" if briefing_injected else ("exists" if briefing_date else "none")
    except Exception:
        pass

    # 统计知识注入
    knowledge_count = 0
    knowledge_types = []
    knowledge_scores = []
    for r in injection_records:
        if r.get("source_type") in ("task", "briefing", "solution", "decision", "lesson", "process"):
            knowledge_count += 1
            if r.get("source_type") not in knowledge_types:
                knowledge_types.append(r.get("source_type"))
            if r.get("final_score"):
                knowledge_scores.append(r["final_score"])

    avg_score = round(sum(knowledge_scores) / len(knowledge_scores), 2) if knowledge_scores else 0
    suggestion_count = len(activity_items)

    return {
        "last_session_at": datetime.now().isoformat(),
        "cards": {
            "task": {
                "status": task_status,
                "label": {"active": "活跃中", "inactive": "已暂停", "none": "无"}.get(task_status, "无"),
                "detail": task_detail,
            },
            "briefing": {
                "status": briefing_status,
                "label": {"injected": "已注入", "exists": "已生成", "none": "未生成"}.get(briefing_status, "未生成"),
                "date": today_str,
                "briefing_date": briefing_date,
                "summary": briefing_summary,
                "delivered": briefing_delivered,
                "allow_toggle": briefing_allow_toggle,
                "briefing_id": briefing_id,
            },
            "knowledge": {
                "status": "injected" if knowledge_count > 0 else "empty",
                "count": knowledge_count,
                "label": f"{knowledge_count} 条" if knowledge_count > 0 else "无",
                "avg_score": avg_score,
                "types": knowledge_types,
            },
            "suggestion": {
                "status": "triggered" if suggestion_count > 0 else "idle",
                "label": f"触发 {suggestion_count} 次" if suggestion_count > 0 else "未触发",
                "count": suggestion_count,
            },
        },
        "injection_timeline": [
            {
                "time": datetime.fromtimestamp(r.get("timestamp", now)).strftime("%Y-%m-%d %H:%M:%S"),
                "type": r.get("source_type", "unknown"),
                "content": r.get("content", ""),
                "score": r.get("final_score", 0),
                "id": r.get("id", ""),
            }
            for r in injection_records[:10]
        ],
        "instruction_panel": {
            # TASK_EVAL 指令由 Hook F2 管道注入，条件仅为 project_id 非空，与 task 状态无关
            "task_eval_injected": bool(project_id),
            "task_eval_variant": "cold_start",
            "task_eval_content": task_detail if task_status == "active" else "",
            "task_eval_instruction": (
                "请在本轮回复末尾附加任务进度自评，格式：\n"
                "[TASK_EVAL]\n"
                '{"done": [...], "todo": [...], "blocked": [...]}\n'
                "[/TASK_EVAL]"
            ) if project_id else "",
        },
    }


@router.get("/api/v2/behavior-guide")
async def get_behavior_guide(request: Request):
    """获取行为引导的实际内容和来源状态。"""
    from ...config import load_behavior_guide as _load_bg
    from ...config.models import get_memos_home

    guide_path = get_memos_home() / "etc" / "behavior_guide.json"
    file_exists = guide_path.exists()
    guide_text = _load_bg()
    source = str(guide_path) if file_exists else "(代码默认兜底，可创建文件自定义)"

    return {
        "loaded": bool(guide_text),
        "content": guide_text[:300] if guide_text else "",
        "source": source,
        "file_exists": file_exists,
    }


@router.post("/api/v2/briefing/toggle-injection")
async def toggle_briefing_injection(request: Request, project_id: str = ""):
    """切换简报注入开关：更新 delivered 标记，控制是否注入。"""
    mem = request.app.state.context_memory
    if not project_id:
        from ...server.mcp import _project_id_ctx as _pid_ctx
        project_id = _pid_ctx.get()

    if not project_id:
        return {"ok": False, "error": "缺少项目 ID"}

    try:
        body = await request.json()
        briefing_id = body.get("briefing_id", "")
        enabled = body.get("enabled", True)  # true=允许注入( delivered=false )
    except Exception:
        return {"ok": False, "error": "请求参数错误"}

    if not briefing_id:
        return {"ok": False, "error": "缺少 briefing_id"}

    try:
        mem.update_memory(briefing_id, new_metadata={"delivered": not enabled})
        logger.info(
            "简报注入开关: %s delivered=%s (briefing_id=%s)",
            "开启" if enabled else "关闭",
            not enabled,
            briefing_id[:8],
        )
        return {"ok": True, "delivered": not enabled}
    except Exception as e:
        logger.error("简报 toggle-injection 失败: %s", e)
        return {"ok": False, "error": str(e)}
