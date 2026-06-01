"""R2 待办 CRUD + 状态流转 + status_history 自动记录"""

import json
import logging
import time as time_mod

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...errors import InvalidStateTransitionError
from ..dependencies import get_project_id

logger = logging.getLogger(__name__)

router = APIRouter()

# 有效状态转换规则表
_VALID_TRANSITIONS = {
    "pending": {"in_progress", "completed", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": {"pending"},
    "cancelled": {"pending"},
}

TODO_STATUS_VALUES = {"pending", "in_progress", "completed", "cancelled"}

PRIORITY_VALUES = {"high", "medium", "low"}


def _validate_todo_status_transition(current: str, target: str):
    """校验待办状态流转是否合法，非法时抛出 InvalidStateTransitionError。"""
    allowed = _VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransitionError(
            message=f"待办状态无法从 {current} 转换到 {target}",
            suggestion=f"允许的转换: {current} → {', '.join(sorted(allowed)) if allowed else '无'}",
            detail=f"current={current}, target={target}",
        )


def _get_todo_status(meta: dict) -> str:
    """获取待办状态，旧数据兼容（空值视为 pending）。"""
    status = meta.get("todo_status", "")
    if not status or status not in TODO_STATUS_VALUES:
        return "pending"
    return status


def _get_priority(meta: dict) -> str:
    """获取优先级，旧数据兼容（空值视为 medium）。"""
    priority = meta.get("priority", "")
    if not priority or priority not in PRIORITY_VALUES:
        return "medium"
    return priority


@router.get("/api/todos")
def list_todos(
    request: Request,
    todo_status: str = Query(None, description="按待办状态过滤"),
    priority: str = Query(None, description="按优先级过滤"),
    sort: str = Query("created_at", description="排序: created_at / priority / custom"),
    project_id: str = Depends(get_project_id),
    show_archived: bool = Query(False, description="是否包含已归档待办"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """查询待办列表。默认排除 completed/cancelled，除非指定 todo_status。"""
    mem = request.app.state.mem

    # 构建 where 过滤条件
    where: dict = {"type": "todo"}
    if todo_status:
        where["todo_status"] = todo_status
    if priority:
        where["priority"] = priority

    results = mem.list_memories(
        project_id=project_id,
        where=where,
        limit=limit,
        offset=offset,
        include_archived=show_archived,
    )

    total = mem.count_memories(project_id=project_id, where=where, include_archived=show_archived)

    # 按 todo_status 分组统计
    stats_where: dict = {"type": "todo"}
    stats: dict[str, int] = {"total": total}
    for s in TODO_STATUS_VALUES:
        try:
            sw = {**stats_where, "todo_status": s}
            stats[s] = mem.count_memories(project_id=project_id, where=sw, include_archived=show_archived)
        except Exception:
            stats[s] = 0

    todos = []
    for item in results:
        meta = item.get("metadata", {})
        raw_history = meta.get("status_history", "[]")
        try:
            status_history = json.loads(raw_history) if isinstance(raw_history, str) else list(raw_history)
        except (json.JSONDecodeError, TypeError):
            status_history = []
        active = meta.get("active", True)
        if not isinstance(active, bool):
            active = True
        todo = {
            "id": item["id"],
            "content": item.get("document", ""),
            "todo_status": _get_todo_status(meta),
            "priority": _get_priority(meta),
            "context": meta.get("context", ""),
            "source_date": meta.get("source_date", ""),
            "due_date": meta.get("due_date", ""),
            "sort_order": meta.get("sort_order", 0),
            "created_at": meta.get("timestamp", 0),
            "source": meta.get("source", ""),
            "started_at": meta.get("started_at", None),
            "completed_at": meta.get("completed_at", None),
            "cancelled_at": meta.get("cancelled_at", None),
            "status_history": status_history,
            "active": active,
        }
        todos.append(todo)

    # 排序
    if sort == "priority":
        _priority_map = {"high": 0, "medium": 1, "low": 2}
        todos.sort(key=lambda t: _priority_map.get(t["priority"], 1))
    elif sort == "custom":
        todos.sort(key=lambda t: (float(t.get("sort_order", 0) or 0), t.get("created_at", 0)))
    else:  # created_at
        todos.sort(key=lambda t: t.get("created_at", 0), reverse=True)

    return {"todos": todos, "total": total, "stats": stats}


@router.post("/api/todos", status_code=201)
def create_todo(request: Request, body: dict, project_id: str = Depends(get_project_id)):
    """新建待办。写入 type=todo, todo_status=pending。"""
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "待办内容不能为空")

    priority = body.get("priority", "medium")
    if priority not in PRIORITY_VALUES:
        raise HTTPException(400, f"无效优先级: {priority}，可选: {', '.join(sorted(PRIORITY_VALUES))}")

    due_date = body.get("due_date", "")
    mem = request.app.state.mem

    now = time_mod.time()
    metadata = {
        "type": "todo",
        "todo_status": "pending",
        "priority": priority,
        "active": True,
        "project_id": project_id,
        "source": "user_appended",
        "status_history": json.dumps([]),
        "sort_order": now,
        "timestamp": now,
    }
    if due_date:
        metadata["due_date"] = due_date

    mid = mem.remember(content, metadata=metadata)
    if mid is None:
        raise HTTPException(500, "待办创建失败")

    logger.info("待办已创建 id=%s", mid)
    return {"id": mid, "message": "待办已创建"}


@router.put("/api/todos/{todo_id}")
def update_todo(request: Request, todo_id: str, body: dict, project_id: str = Depends(get_project_id)):
    """编辑待办内容/优先级/到期日/sort_order。todo_status 变更走 status 端点。"""
    mem = request.app.state.mem
    old = mem.get_memory(todo_id)
    if old is None:
        raise HTTPException(404, f"待办未找到: {todo_id[:8]}")

    meta = old.get("metadata", {})
    if meta.get("type") != "todo":
        raise HTTPException(400, "该记忆不是待办类型")
    if meta.get("project_id") != project_id:
        raise HTTPException(404, f"待办未找到: {todo_id[:8]}")

    new_meta = {}

    if "content" in body:
        new_content = (body["content"] or "").strip()
        if not new_content:
            raise HTTPException(400, "待办内容不能为空")
    else:
        new_content = None

    if "priority" in body:
        priority = body["priority"]
        if priority not in PRIORITY_VALUES:
            raise HTTPException(400, f"无效优先级: {priority}，可选: {', '.join(sorted(PRIORITY_VALUES))}")
        new_meta["priority"] = priority

    if "due_date" in body:
        new_meta["due_date"] = body["due_date"] or ""

    if "sort_order" in body:
        try:
            new_meta["sort_order"] = float(body["sort_order"])
        except (ValueError, TypeError):
            raise HTTPException(400, "sort_order 必须是数值")

    try:
        mem.update_memory(todo_id, new_content=new_content, new_metadata=new_meta if new_meta else None)
    except Exception as e:
        logger.warning("更新待办失败 id=%s error=%s", todo_id[:8], e)
        raise HTTPException(500, f"更新失败: {e}")

    logger.info("待办已更新 id=%s", todo_id[:8])
    return {"message": "待办已更新"}


@router.post("/api/todos/{todo_id}/status")
def change_todo_status(request: Request, todo_id: str, body: dict, project_id: str = Depends(get_project_id)):
    """待办状态流转。自动记录 status_history + 时间戳。"""
    target_status = (body.get("todo_status") or "").strip()
    if not target_status or target_status not in TODO_STATUS_VALUES:
        raise HTTPException(400, f"无效 todo_status: {target_status}，可选: {', '.join(sorted(TODO_STATUS_VALUES))}")

    mem = request.app.state.mem
    old = mem.get_memory(todo_id)
    if old is None:
        raise HTTPException(404, f"待办未找到: {todo_id[:8]}")

    meta = old.get("metadata", {})
    if meta.get("type") != "todo":
        raise HTTPException(400, "该记忆不是待办类型")
    if meta.get("project_id") != project_id:
        raise HTTPException(404, f"待办未找到: {todo_id[:8]}")

    current_status = _get_todo_status(meta)

    # 校验转换合法性
    try:
        _validate_todo_status_transition(current_status, target_status)
    except InvalidStateTransitionError as e:
        raise HTTPException(422, e.message)

    now = time_mod.time()
    # ChromaDB 不支持 list 类型，序列化为 JSON 字符串
    raw_history = meta.get("status_history", "[]")
    try:
        status_history = json.loads(raw_history) if isinstance(raw_history, str) else list(raw_history)
    except (json.JSONDecodeError, TypeError):
        status_history = []

    # 追加历史记录
    status_history.append(
        {
            "from_status": current_status,
            "to_status": target_status,
            "changed_at": now,
        }
    )

    new_meta = {
        "todo_status": target_status,
        "status_history": json.dumps(status_history),
    }

    # 记录时间戳（幂等：in_progress 首次开始才记录）
    if target_status == "in_progress":
        if not meta.get("started_at"):
            new_meta["started_at"] = now
    elif target_status == "completed":
        new_meta["completed_at"] = now
    elif target_status == "cancelled":
        new_meta["cancelled_at"] = now

    try:
        mem.update_memory(todo_id, new_metadata=new_meta)
    except Exception as e:
        logger.warning("状态变更失败 id=%s error=%s", todo_id[:8], e)
        raise HTTPException(500, f"状态变更失败: {e}")

    logger.info("待办状态变更: %s: %s → %s", todo_id[:8], current_status, target_status)
    return {
        "message": f"状态已从 {current_status} 变更为 {target_status}",
        "todo_status": target_status,
        "status_history": status_history,
    }


@router.delete("/api/todos/bulk")
def bulk_delete_todos(request: Request, todo_status: str = Query(...), project_id: str = Depends(get_project_id)):
    """批量删除指定状态的待办。"""
    if todo_status not in TODO_STATUS_VALUES:
        raise HTTPException(400, f"无效 todo_status: {todo_status}")

    mem = request.app.state.mem
    results = mem.list_memories(
        project_id=project_id,
        where={"type": "todo", "todo_status": todo_status},
        limit=200,
        offset=0,
    )

    deleted = 0
    for item in results:
        if item.get("metadata", {}).get("project_id") != project_id:
            continue  # skip items not belonging to current project
        try:
            mem.delete_memory(item["id"])
            deleted += 1
        except Exception as e:
            logger.warning("批量删除跳过 id=%s error=%s", item["id"][:8], e)

    logger.info("批量删除待办: status=%s, count=%d", todo_status, deleted)
    return {"message": f"已删除 {deleted} 条待办", "count": deleted}


@router.post("/api/todos/bulk-archive")
def bulk_archive_todos(request: Request, todo_status: str = Query(...), project_id: str = Depends(get_project_id)):
    """批量归档指定状态的待办。"""
    if todo_status not in TODO_STATUS_VALUES:
        raise HTTPException(400, f"无效 todo_status: {todo_status}")

    mem = request.app.state.mem
    results = mem.list_memories(
        project_id=project_id,
        where={"type": "todo", "todo_status": todo_status, "active": True},
        limit=200,
        offset=0,
    )

    archived = 0
    for item in results:
        if item.get("metadata", {}).get("project_id") != project_id:
            continue  # skip items not belonging to current project
        try:
            mem.update_memory(item["id"], new_metadata={"active": False})
            archived += 1
        except Exception as e:
            logger.warning("批量归档跳过 id=%s error=%s", item["id"][:8], e)

    logger.info("批量归档待办: status=%s, count=%d", todo_status, archived)
    return {"message": f"已归档 {archived} 条待办", "count": archived}


@router.post("/api/todos/{todo_id}/archive")
def archive_todo(request: Request, todo_id: str, project_id: str = Depends(get_project_id)):
    """归档单个待办。"""
    mem = request.app.state.mem
    old = mem.get_memory(todo_id)
    if old is None:
        raise HTTPException(404, f"待办未找到: {todo_id[:8]}")
    if old.get("metadata", {}).get("type") != "todo":
        raise HTTPException(400, "该记忆不是待办类型")
    if old.get("metadata", {}).get("project_id") != project_id:
        raise HTTPException(404, f"待办未找到: {todo_id[:8]}")
    try:
        mem.update_memory(todo_id, new_metadata={"active": False})
    except Exception as e:
        logger.warning("归档待办失败 id=%s error=%s", todo_id[:8], e)
        raise HTTPException(500, f"归档失败: {e}")
    logger.info("待办已归档 id=%s", todo_id[:8])
    return {"message": "待办已归档"}


@router.delete("/api/todos/{todo_id}")
def delete_todo(request: Request, todo_id: str, project_id: str = Depends(get_project_id)):
    """删除待办。"""
    mem = request.app.state.mem
    old = mem.get_memory(todo_id)
    if old is None:
        raise HTTPException(404, f"待办未找到: {todo_id[:8]}")
    if old.get("metadata", {}).get("type") != "todo":
        raise HTTPException(400, "该记忆不是待办类型")
    if old.get("metadata", {}).get("project_id") != project_id:
        raise HTTPException(404, f"待办未找到: {todo_id[:8]}")
    try:
        mem.delete_memory(todo_id)
    except Exception as e:
        logger.warning("删除待办失败 id=%s error=%s", todo_id[:8], e)
        raise HTTPException(500, f"删除失败: {e}")
    logger.info("待办已删除 id=%s", todo_id[:8])
    return {"message": "待办已删除"}
