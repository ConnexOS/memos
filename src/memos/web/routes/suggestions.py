"""用户建议管理 API — manual_suggestion 类型（v0.7.0 保留）。

仅保留 manual_suggestion (L4) 类型相关路由。
旧管道一/二/三的 suggestion 类型路由已移除。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from ..dependencies import get_project_id
from ..models.requests import ManualSuggestionCreateRequest

logger = logging.getLogger(__name__)

router = APIRouter()

# 进程级项目内存缓存：pid → [injected_records]（Layer 1 注入记录）
_injected_cache: dict[str, list[dict]] = {}
_injected_cache_time: dict[str, float] = {}
_INJECTED_CACHE_TTL = 5
_injected_cache_lock = threading.Lock()


def _get_injected_records(pid: str) -> list[dict]:
    """获取最近会话的 Layer 1 注入记录（进程级内存缓存 + 文件持久化）。"""
    now = time.time()
    last_load = _injected_cache_time.get(pid, 0)
    if pid in _injected_cache and (now - last_load) < _INJECTED_CACHE_TTL:
        return _injected_cache[pid]

    with _injected_cache_lock:
        last_load = _injected_cache_time.get(pid, 0)
        if pid in _injected_cache and (now - last_load) < _INJECTED_CACHE_TTL:
            return _injected_cache[pid]

        from ...config.models import get_memos_home

        _file_paths = [
            get_memos_home() / "etc" / f".injected_records_{pid}.json",
            Path.home() / ".memos" / "etc" / f".injected_records_{pid}.json",
        ]
        for path in _file_paths:
            try:
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    records = data.get("records", [])
                    _injected_cache[pid] = records
                    _injected_cache_time[pid] = now
                    return records
            except Exception:
                continue

        _injected_cache[pid] = []
        _injected_cache_time[pid] = now
        return []


# --- 用户建议管理 API ---


@router.post("/api/manual-suggestions")
def create_manual_suggestion(
    request: Request, req: ManualSuggestionCreateRequest, project_id: str = Depends(get_project_id)
):
    """创建用户建议（含 trigger_keywords json.dumps 序列化）。"""

    mem = request.app.state.context_memory
    pid = project_id

    for kw in req.trigger_keywords:
        if len(kw) > 50:
            raise HTTPException(422, f"关键词长度不能超过 50 字符: {kw}")

    expires_at = (time.time() + req.validity_minutes * 60) if req.validity_minutes > 0 else 0

    meta = {
        "type": "manual_suggestion",
        "project_id": pid,
        "scope": "personal",
        "creator_id": request.session.get("creator_id", "unknown") if hasattr(request, "session") else "unknown",
        "source": "manual",
        "trigger_keywords": json.dumps(req.trigger_keywords),
        "trigger_mode": req.trigger_mode,
        "priority": req.priority,
        "cooldown_minutes": req.cooldown_minutes,
        "validity_minutes": req.validity_minutes,
        "expires_at": expires_at,
        "disabled": False,
        "status": "active",
        "hit_count": 0,
        "last_triggered": 0,
        "created_by": "user",
        "timestamp": time.time(),
    }

    mid = mem.remember(req.content, metadata=meta)
    if not mid:
        raise HTTPException(500, "保存用户建议失败")

    return {
        "ok": True,
        "id": mid,
        "content": req.content,
        "trigger_keywords": req.trigger_keywords,
        "trigger_mode": req.trigger_mode,
    }


@router.get("/api/manual-suggestions")
def list_manual_suggestions(request: Request, project_id: str = Depends(get_project_id)):
    """列出当前项目所有用户建议。"""
    mem = request.app.state.context_memory
    pid = project_id

    results = mem.store.get(
        where={"$and": [{"type": "manual_suggestion"}, {"project_id": pid}]},
        include=["documents", "metadatas"],
    )

    ids = results.get("ids", [])
    documents = results.get("documents", [])
    metadatas = results.get("metadatas", [])

    items = []
    for doc_id, doc, meta in zip(ids, documents, metadatas):
        if meta.get("status") == "dismissed":
            continue
        raw_kw = meta.get("trigger_keywords", "[]")
        if isinstance(raw_kw, str):
            try:
                keywords = json.loads(raw_kw)
            except (json.JSONDecodeError, TypeError):
                keywords = [raw_kw]
        else:
            keywords = raw_kw

        items.append(
            {
                "id": doc_id,
                "content": doc,
                "trigger_keywords": keywords,
                "trigger_mode": meta.get("trigger_mode", "keyword"),
                "priority": meta.get("priority", "medium"),
                "cooldown_minutes": meta.get("cooldown_minutes", 60),
                "validity_minutes": meta.get("validity_minutes", 0),
                "expires_at": meta.get("expires_at", 0),
                "disabled": bool(meta.get("disabled", False)),
                "created_by": meta.get("created_by", "user"),
                "hit_count": meta.get("hit_count", 0),
                "last_triggered": meta.get("last_triggered", 0),
                "timestamp": meta.get("timestamp", 0),
            }
        )

    return {"items": items, "total": len(items)}


@router.delete("/api/manual-suggestions/{suggestion_id}")
def delete_manual_suggestion(request: Request, suggestion_id: str, project_id: str = Depends(get_project_id)):
    """删除用户建议 —— 软删除：标记 dismissed，不再在用户建议列表中展示。"""
    mem = request.app.state.context_memory

    results = mem.store.get(ids=[suggestion_id], include=["metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "用户建议不存在")

    meta = dict(results["metadatas"][0])
    if meta.get("project_id") != project_id:
        raise HTTPException(404, "用户建议不存在")
    if meta.get("type") != "manual_suggestion":
        raise HTTPException(400, "该记录不是用户建议")

    meta["status"] = "dismissed"
    meta["suggestion_type"] = "manual_trigger"
    if not meta.get("expires_at") or meta["expires_at"] < time.time():
        meta["expires_at"] = time.time() + 365 * 86400
    mem.store.update(ids=[suggestion_id], metadatas=[meta])
    return {"ok": True}


@router.put("/api/manual-suggestions/{suggestion_id}")
def update_manual_suggestion(
    request: Request, suggestion_id: str, req: ManualSuggestionCreateRequest, project_id: str = Depends(get_project_id)
):
    """更新用户建议（用 remember 重建，保持 hit_count 不变）。"""
    mem = request.app.state.context_memory
    pid = project_id

    results = mem.store.get(ids=[suggestion_id], include=["metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "用户建议不存在")

    old_meta = results["metadatas"][0]
    if old_meta.get("project_id") != pid:
        raise HTTPException(404, "用户建议不存在")
    if old_meta.get("type") != "manual_suggestion":
        raise HTTPException(400, "该记录不是用户建议")

    for kw in req.trigger_keywords:
        if len(kw) > 50:
            raise HTTPException(422, f"关键词长度不能超过 50 字符: {kw}")

    mem.store.delete(ids=[suggestion_id])

    expires_at = (time.time() + req.validity_minutes * 60) if req.validity_minutes > 0 else 0
    new_meta = {
        "type": "manual_suggestion",
        "project_id": old_meta.get("project_id", pid),
        "scope": old_meta.get("scope", "personal"),
        "creator_id": old_meta.get(
            "creator_id", request.session.get("creator_id", "unknown") if hasattr(request, "session") else "unknown"
        ),
        "source": "manual",
        "trigger_keywords": json.dumps(req.trigger_keywords),
        "trigger_mode": req.trigger_mode,
        "priority": req.priority,
        "cooldown_minutes": req.cooldown_minutes,
        "validity_minutes": req.validity_minutes,
        "expires_at": expires_at,
        "disabled": old_meta.get("disabled", False),
        "status": "active",
        "hit_count": old_meta.get("hit_count", 0),
        "last_triggered": old_meta.get("last_triggered", 0),
        "created_by": old_meta.get("created_by", "user"),
        "timestamp": old_meta.get("timestamp", time.time()),
    }
    new_id = mem.remember(req.content, metadata=new_meta)
    if not new_id:
        raise HTTPException(500, "更新用户建议失败")

    return {
        "ok": True,
        "id": new_id,
        "content": req.content,
        "trigger_keywords": req.trigger_keywords,
        "trigger_mode": req.trigger_mode,
    }


@router.put("/api/manual-suggestions/{suggestion_id}/toggle-disable")
def toggle_manual_suggestion_disable(request: Request, suggestion_id: str, project_id: str = Depends(get_project_id)):
    """切换用户建议的临时失效/启用状态。"""
    mem = request.app.state.context_memory

    results = mem.store.get(ids=[suggestion_id], include=["metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "用户建议不存在")

    meta = dict(results["metadatas"][0])
    if meta.get("project_id") != project_id:
        raise HTTPException(404, "用户建议不存在")
    if meta.get("type") != "manual_suggestion":
        raise HTTPException(400, "该记录不是用户建议")

    current = bool(meta.get("disabled", False))
    meta["disabled"] = not current
    mem.store.update(ids=[suggestion_id], metadatas=[meta])

    return {"ok": True, "disabled": meta["disabled"]}


# --- v0.7.1: 暂停/恢复推送（免打扰文件） ---

_NO_SUGGESTIONS_FILE: Path | None = None


def _get_no_suggestions_file() -> Path:
    global _NO_SUGGESTIONS_FILE
    if _NO_SUGGESTIONS_FILE is None:
        _NO_SUGGESTIONS_FILE = Path.cwd() / ".claude" / "no_suggestions"
    return _NO_SUGGESTIONS_FILE


@router.get("/api/suggestions/no-suggestions-status")
def no_suggestions_status():
    """检查免打扰文件是否存在，返回暂停状态。"""
    f = _get_no_suggestions_file()
    return {"enabled": f.exists()}


@router.post("/api/suggestions/toggle-pause")
def toggle_suggestions_pause():
    """切换免打扰文件存在状态。"""
    f = _get_no_suggestions_file()
    if f.exists():
        f.unlink()
        enabled = False
    else:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("", encoding="utf-8")
        enabled = True
    return {"enabled": enabled}
