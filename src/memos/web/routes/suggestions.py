"""F5 主动建议 API — 6 个 RESTful 端点 (v0.4.4)

所有端点复用现有 JWT 认证中间件和 ContextMemory 查询接口。
suggestion 数据由 F3 Hook 分层检索写入 ChromaDB (type=suggestion)。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ...config import config
from ...config.models import SuggestionConfig, SystemSuggestionConfig
from ..app import _detect_project_id
from ..models.requests import (
    ManualSuggestionCreateRequest,
    SuggestionFeedbackRequest,
    SuggestionSettingsRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# 进程级项目内存缓存：pid → [injected_records]（Layer 1 注入记录）
# 数据由 Hook 在每次会话注入时写入文件，Dashboard 加载到内存
_injected_cache: dict[str, list[dict]] = {}
_injected_cache_time: dict[str, float] = {}
_INJECTED_CACHE_TTL = 5  # 秒：防 Dashboard 在 Hook 写文件过程中读到半写文件
_injected_cache_lock = threading.Lock()  # 保护缓存读写的线程安全（FastAPI 同步路由在线程池中运行）


def _get_injected_records(pid: str) -> list[dict]:
    """获取最近会话的 Layer 1 注入记录（进程级内存缓存 + 文件持久化）。

    线程安全：快速路径（缓存命中）无锁，慢速路径（文件加载）由
    _injected_cache_lock 保护 + 双重检查锁定，避免并发重复加载。
    """
    now = time.time()
    # 快速路径：缓存命中（无锁，Python dict 读取是原子的）
    last_load = _injected_cache_time.get(pid, 0)
    if pid in _injected_cache and (now - last_load) < _INJECTED_CACHE_TTL:
        return _injected_cache[pid]

    # 慢速路径：加锁 + 二次检查
    with _injected_cache_lock:
        last_load = _injected_cache_time.get(pid, 0)
        if pid in _injected_cache and (now - last_load) < _INJECTED_CACHE_TTL:
            return _injected_cache[pid]

        try:
            from ...config.models import get_memos_home

            path = get_memos_home() / "etc" / f".injected_records_{pid}.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                records = data.get("records", [])
                _injected_cache[pid] = records
                _injected_cache_time[pid] = now
                return records
        except Exception:
            pass

        _injected_cache[pid] = []
        _injected_cache_time[pid] = now
        return []


def _get_project_id(mem, pid: str | None = None) -> str:
    """获取有效的 project_id，优先使用请求参数，否则使用默认值。"""
    if pid:
        return pid
    return _detect_project_id()


def _build_suggestion_query(
    project_id: str,
    status: str | None = None,
    include_expired: bool = False,
    suggestion_types: list[str] | None = None,
) -> dict:
    """构建 ChromaDB where 子句，统一过期过滤逻辑。
    status 支持逗号分隔的多个状态值（如 "dismissed,reacted"）。
    suggestion_types 可选，过滤三管道类型（如 ["active_push", "system_alert", "manual_trigger"]）。
    """
    clauses = [
        {"type": "suggestion"},
        {"project_id": project_id},
    ]
    if suggestion_types:
        if len(suggestion_types) == 1:
            clauses.append({"suggestion_type": suggestion_types[0]})
        else:
            clauses.append({"suggestion_type": {"$in": suggestion_types}})
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            clauses.append({"status": statuses[0]})
        else:
            clauses.append({"$or": [{"status": s} for s in statuses]})
    if not include_expired:
        clauses.append({"expires_at": {"$gt": time.time()}})
    return {"$and": clauses} if len(clauses) > 1 else clauses[0]


def _meta_to_suggestion(doc_id: str, document: str, meta: dict) -> dict:
    """将 ChromaDB 原始结果转为建议列表项。"""
    return {
        "id": doc_id,
        "content": document,
        "source_memory_id": meta.get("source_memory_id", ""),
        "similarity": meta.get("similarity", 0),
        "query": meta.get("query", ""),
        "status": meta.get("status", "pending"),
        "suggestion_type": meta.get("suggestion_type", "active_push"),
        "source_date": meta.get("source_date", ""),
        "source_type": meta.get("source_type", ""),
        "timestamp": meta.get("timestamp", 0),
        "expires_at": meta.get("expires_at", 0),
        "feedback": meta.get("feedback"),
        # 管道二
        "event_type": meta.get("event_type", ""),
        # 管道三
        "trigger_keywords": meta.get("trigger_keywords", "[]"),
        "trigger_mode": meta.get("trigger_mode", ""),
        "hit_count": meta.get("hit_count", 0),
        "created_by": meta.get("created_by", ""),
        "reuse_count": int(meta.get("reuse_count", 0) or 0),
    }


@router.get("/api/suggestions")
def list_suggestions(
    request: Request,
    limit: int | None = None,
    offset: int = 0,
    status: str = "pending",
    suggestion_types: str | None = None,
):
    if limit is None:
        limit = config.suggestion.suggestion_display_limit
    """查询建议列表（分页+状态过滤+过期排除），默认仅返回待处理。
    审计修复（P2-3）：增加 suggestion_types 查询参数，支持未来三管道展示。
    """
    if suggestion_types:
        types_list = [t.strip() for t in suggestion_types.split(",") if t.strip()]
    else:
        types_list = ["active_push", "system_alert"]  # 默认同时返回待处理知识匹配和系统提醒

    mem = request.app.state.mem
    pid = _get_project_id(mem)

    where = _build_suggestion_query(pid, status=status, suggestion_types=types_list)
    results = mem.store.get(
        where=where,
        limit=limit,
        offset=offset,
        include=["documents", "metadatas"],
    )

    items = [
        _meta_to_suggestion(doc_id, doc, meta)
        for doc_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"])
    ]

    # 批量查询源记忆的 reuse_count
    source_ids = [s["source_memory_id"] for s in items if s.get("source_memory_id")]
    if source_ids:
        src_results = mem.store.get(ids=source_ids, include=["metadatas"])
        src_reuse = {}
        for sid, smeta in zip(src_results["ids"], src_results["metadatas"]):
            src_reuse[sid] = int(smeta.get("reuse_count", 0) or 0)
        for s in items:
            s["reuse_count"] = src_reuse.get(s.get("source_memory_id", ""), 0)

    total = mem.store.count(where=where)

    # 历史建议同时包含已关闭的手工建议（type=manual_suggestion, status=dismissed）
    ms_total = 0
    if status and "dismissed" in status and "manual_trigger" in types_list:
        ms_where = {"$and": [{"type": "manual_suggestion"}, {"project_id": pid}, {"status": "dismissed"}]}
        ms_total = mem.store.count(where=ms_where)
        if ms_total > 0:
            ms_results = mem.store.get(where=ms_where, limit=limit, include=["documents", "metadatas"])
            for doc_id, doc, meta in zip(
                ms_results.get("ids", []), ms_results.get("documents", []), ms_results.get("metadatas", [])
            ):
                items.append(_meta_to_suggestion(doc_id, doc, meta))

    return {"items": items, "total": total + ms_total, "limit": limit, "offset": offset}


@router.get("/api/suggestions/count")
def count_pending_suggestions(request: Request):
    """轻量计数查询 — 管道一待处理建议总数。"""
    mem = request.app.state.mem
    pid = _get_project_id(mem)
    where = _build_suggestion_query(
        pid,
        status="pending",
        suggestion_types=["active_push"],
    )
    count = mem.store.count(where=where)
    return {"count": count}


@router.post("/api/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(request: Request, suggestion_id: str):
    """关闭单条建议。"""
    mem = request.app.state.mem

    results = mem.store.get(ids=[suggestion_id], include=["metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "建议不存在")

    old_meta = dict(results["metadatas"][0])
    old_meta["status"] = "dismissed"
    mem.store.update(ids=[suggestion_id], metadatas=[old_meta])
    return {"ok": True}


@router.post("/api/suggestions/{suggestion_id}/restore")
def restore_suggestion(request: Request, suggestion_id: str):
    """将已关闭建议恢复到待处理状态。

    FIX: 根据 suggestion_type 恢复至不同面板。
    manual_trigger（原人工建议）→ 恢复为 type=manual_suggestion 到「人工建议」；
    其他（active_push/system_alert）→ status=pending 到「待处理」。
    """
    mem = request.app.state.mem

    results = mem.store.get(ids=[suggestion_id], include=["metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "建议不存在")

    old_meta = dict(results["metadatas"][0])
    if old_meta.get("status") != "dismissed":
        raise HTTPException(400, "仅可恢复已关闭的建议")

    # 判断是否原手工建议
    if old_meta.get("suggestion_type") == "manual_trigger":
        old_meta["type"] = "manual_suggestion"
        old_meta["status"] = "active"
        old_meta["expires_at"] = 0  # 恢复为永不过期
        old_meta.pop("suggestion_type", None)
        old_meta.pop("_original_type", None)
        old_meta.pop("feedback", None)
        mem.store.update(ids=[suggestion_id], metadatas=[old_meta])
        return {"ok": True, "restore_type": "manual"}

    old_meta["status"] = "pending"
    old_meta.pop("feedback", None)
    mem.store.update(ids=[suggestion_id], metadatas=[old_meta])
    return {"ok": True, "restore_type": "pending"}


@router.delete("/api/suggestions/history")
def clear_suggestion_history(request: Request):
    """物理删除当前项目的所有历史建议（status=dismissed）。"""
    mem = request.app.state.mem
    pid = _get_project_id(mem)

    where = _build_suggestion_query(pid, status="dismissed", include_expired=True)
    results = mem.store.get(where=where, include=[])
    ids = results["ids"]
    if not ids:
        return {"ok": True, "deleted": 0}

    mem.store.delete(ids=ids)
    return {"ok": True, "deleted": len(ids)}


@router.delete("/api/suggestions/{suggestion_id}")
def hard_delete_suggestion(request: Request, suggestion_id: str):
    """硬删除建议记录（物理删除）。"""
    mem = request.app.state.mem
    results = mem.store.get(ids=[suggestion_id], include=["metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "建议不存在")
    mem.store.delete(ids=[suggestion_id])
    return {"ok": True}


@router.post("/api/suggestions/dismiss-all")
def dismiss_all_suggestions(request: Request):
    """批量关闭当前项目的所有待处理建议。"""
    mem = request.app.state.mem
    pid = _get_project_id(mem)

    where = _build_suggestion_query(pid, status="pending", include_expired=True, suggestion_types=["active_push"])
    results = mem.store.get(where=where, include=["metadatas"])
    ids = results["ids"]
    if not ids:
        return {"ok": True, "dismissed": 0}

    metadatas = []
    for meta in results["metadatas"]:
        m = dict(meta)
        m["status"] = "dismissed"
        metadatas.append(m)

    mem.store.update(ids=ids, metadatas=metadatas)
    return {"ok": True, "dismissed": len(ids)}


@router.post("/api/suggestions/{suggestion_id}/feedback")
def submit_suggestion_feedback(request: Request, suggestion_id: str, req: SuggestionFeedbackRequest):
    """提交反馈 — 更新 suggestion 状态 + 反哺源记忆 + 写入 feedback 记录。"""
    mem = request.app.state.mem
    pid = _get_project_id(mem)

    results = mem.store.get(ids=[suggestion_id], include=["documents", "metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "建议不存在")

    old_meta = dict(results["metadatas"][0])
    doc = results["documents"][0]
    source_memory_id = old_meta.get("source_memory_id", "")

    # 幂等性：已经是 reacted 说明已处理过反馈，不再反哺
    was_reacted = old_meta.get("status") == "reacted"

    # 更新 suggestion 元数据
    old_meta["status"] = "reacted"
    old_meta["feedback"] = req.feedback
    old_meta["feedback_time"] = time.time()
    mem.store.update(ids=[suggestion_id], metadatas=[old_meta])

    # ★ 反哺源记忆（幂等：已处理过的不再反哺；异常不阻断主流程）
    if not was_reacted:
        try:
            mem._apply_feedback_to_source(
                source_memory_id=source_memory_id,
                feedback=req.feedback,
            )
        except Exception as e:
            logger.error("反哺源记忆失败: %s", e)

    # 写入 feedback 类型记录（独立写入，失败不影响主流程）
    try:
        mem.remember(
            f"建议反馈 [{req.feedback}]: {doc[:200]}",
            metadata={
                "type": "feedback",
                "project_id": pid,
                "suggestion_id": suggestion_id,
                "feedback": req.feedback,
                "source_memory_id": source_memory_id,
                "timestamp": time.time(),
            },
        )
    except Exception as e:
        logger.error("写入反馈记录失败: %s", e)

    return {"ok": True}


@router.get("/api/suggestions/stats")
def get_suggestion_stats(request: Request, days: int | None = None):
    """建议统计，支持可选天数过滤（HIGH-002）。"""
    mem = request.app.state.mem
    pid = _get_project_id(mem)

    # 构建基础过滤条件（仅管道一 active_push）
    base_clauses: list[dict] = [{"type": "suggestion"}, {"project_id": pid}, {"suggestion_type": "active_push"}]
    if days is not None and days > 0:
        cutoff = time.time() - days * 86400
        base_clauses.append({"timestamp": {"$gte": cutoff}})
    base_where = {"$and": base_clauses}
    total = mem.store.count(where=base_where)

    useful_where = {"$and": list(base_clauses) + [{"feedback": "useful"}]}
    useful = mem.store.count(where=useful_where)

    not_useful_where = {"$and": list(base_clauses) + [{"feedback": "not_useful"}]}
    not_useful = mem.store.count(where=not_useful_where)

    dismissed_where = {"$and": list(base_clauses) + [{"status": "dismissed"}]}
    dismissed = mem.store.count(where=dismissed_where)

    denominator = useful + not_useful
    useful_rate = round(useful / denominator, 2) if denominator > 0 else None

    return {
        "total": total,
        "useful": useful,
        "not_useful": not_useful,
        "dismissed": dismissed,
        "useful_rate": useful_rate,
        "period_days": days,
    }


@router.get("/api/suggestions/injection-stats")
def get_injection_stats(
    request: Request,
    days: int = 7,
    window_hours: int = 24,
):
    """注入监控统计（S5）：三管道分布 + 采纳率趋势 + 源记忆排行 + 去重注入记录 + 活跃人工建议。"""
    mem = request.app.state.mem
    pid = _get_project_id(mem)

    pipeline_types = ["active_push", "manual_trigger", "system_alert"]
    now = time.time()

    # ── 1. 三管道分组统计 ──
    pipelines = {}
    for pt in pipeline_types:
        base_where = {
            "$and": [
                {"type": "suggestion"},
                {"project_id": pid},
                {"suggestion_type": pt},
            ]
        }
        total = mem.store.count(where=base_where)

        pending = mem.store.count(where={"$and": list(base_where["$and"]) + [{"status": "pending"}]})
        reacted = mem.store.count(where={"$and": list(base_where["$and"]) + [{"status": "reacted"}]})
        dismissed = mem.store.count(where={"$and": list(base_where["$and"]) + [{"status": "dismissed"}]})

        # unique 数：拉取所有 records 按去重键聚合去重
        all_results = mem.store.get(
            where=base_where,
            limit=500,
            include=["documents", "metadatas"],
        )
        dedup_keys: set = set()
        for meta, doc in zip(all_results.get("metadatas", []), all_results.get("documents", [])):
            key = _dedup_key(pt, doc, meta)
            if key:
                dedup_keys.add(key)
        unique = len(dedup_keys)

        pipelines[pt] = {
            "total": total,
            "unique": unique,
            "pending": pending,
            "reacted": reacted,
            "dismissed": dismissed,
        }

    # ── 2. 采纳率趋势（近 days 天） ──
    trend = []
    for i in range(days):
        day_start = now - (i + 1) * 86400
        day_end = now - i * 86400
        day_label = time.strftime("%Y-%m-%d", time.localtime(day_start))

        day_where = {
            "$and": [
                {"type": "suggestion"},
                {"project_id": pid},
                {"suggestion_type": {"$in": pipeline_types}},
                {"$and": [{"timestamp": {"$gte": day_start}}, {"timestamp": {"$lt": day_end}}]},
            ]
        }
        day_total = mem.store.count(where=day_where)
        useful_c = mem.store.count(where={"$and": list(day_where["$and"]) + [{"feedback": "useful"}]})
        not_useful_c = mem.store.count(where={"$and": list(day_where["$and"]) + [{"feedback": "not_useful"}]})

        denom = useful_c + not_useful_c
        trend.append(
            {
                "date": day_label,
                "total": day_total,
                "useful": useful_c,
                "not_useful": not_useful_c,
                "useful_rate": round(useful_c / denom, 2) if denom > 0 else None,
            }
        )
    trend.reverse()

    # ── 3. 整体质量 ──
    quality_where = {
        "$and": [
            {"type": "suggestion"},
            {"project_id": pid},
            {"suggestion_type": {"$in": pipeline_types}},
        ]
    }
    total_feedback = mem.store.count(
        where={"$and": list(quality_where["$and"]) + [{"feedback": {"$in": ["useful", "not_useful"]}}]}
    )
    total_useful = mem.store.count(where={"$and": list(quality_where["$and"]) + [{"feedback": "useful"}]})
    total_not_useful = mem.store.count(where={"$and": list(quality_where["$and"]) + [{"feedback": "not_useful"}]})
    q_denom = total_useful + total_not_useful

    quality = {
        "total_feedback": total_feedback,
        "useful": total_useful,
        "not_useful": total_not_useful,
        "useful_rate": round(total_useful / q_denom, 2) if q_denom > 0 else None,
        "trend": trend,
    }

    # ── 4. 源记忆排行 ──
    source_map: dict[str, dict] = {}
    src_results = mem.store.get(
        where={"$and": [{"type": "suggestion"}, {"project_id": pid}, {"suggestion_type": "active_push"}]},
        limit=500,
        include=["documents", "metadatas"],
    )
    for doc_id, doc, meta in zip(
        src_results.get("ids", []),
        src_results.get("documents", []),
        src_results.get("metadatas", []),
    ):
        sm_id = meta.get("source_memory_id", "")
        if not sm_id:
            continue
        if sm_id not in source_map:
            source_map[sm_id] = {
                "source_memory_id": sm_id,
                "content_preview": doc[:120],
                "suggestion_count": 0,
                "useful_count": 0,
                "not_useful_count": 0,
                "source_type": meta.get("source_type", ""),
            }
        entry = source_map[sm_id]
        entry["suggestion_count"] += 1
        fb = meta.get("feedback")
        if fb == "useful":
            entry["useful_count"] += 1
        elif fb == "not_useful":
            entry["not_useful_count"] += 1
        # 更新内容预览（取最新的）
        entry["content_preview"] = doc[:120]

    top_source_memories = sorted(
        source_map.values(),
        key=lambda x: x["suggestion_count"],
        reverse=True,
    )[:10]
    for entry in top_source_memories:
        uc = entry["useful_count"]
        nuc = entry.get("not_useful_count", 0)
        denom_src = uc + nuc
        entry["useful_rate"] = round(uc / denom_src, 2) if denom_src > 0 else None
        entry.pop("not_useful_count", None)

    # ── 5. manual_suggestion 聚合 ──
    manual_results = mem.store.get(
        where={"$and": [{"type": "manual_suggestion"}, {"project_id": pid}]},
        limit=200,
        include=["metadatas"],
    )
    manual_total = len(manual_results.get("ids", []))
    manual_active = 0
    manual_disabled = 0
    total_hits = 0
    top_triggered: list[dict] = []
    for meta in manual_results.get("metadatas", []):
        disabled = bool(meta.get("disabled", False))
        if disabled:
            manual_disabled += 1
        else:
            manual_active += 1
        hits = meta.get("hit_count", 0) or 0
        total_hits += hits
        if hits > 0:
            raw_kw = meta.get("trigger_keywords", "[]")
            if isinstance(raw_kw, str):
                try:
                    kw_list = json.loads(raw_kw)
                except (json.JSONDecodeError, TypeError):
                    kw_list = [raw_kw]
            else:
                kw_list = raw_kw
            top_triggered.append(
                {
                    "keyword_sample": kw_list[0] if kw_list else "",
                    "hit_count": hits,
                }
            )
    top_triggered.sort(key=lambda x: x["hit_count"], reverse=True)

    manual_suggestions = {
        "total": manual_total,
        "active": manual_active,
        "disabled": manual_disabled,
        "total_hits": total_hits,
        "top_triggered": top_triggered[:5],
    }

    # ── 6. 近期注入记录列表（去重合并，仅统计注入过上下文的记录） ──
    cutoff = now - window_hours * 3600
    recent_where = {
        "$and": [
            {"type": "suggestion"},
            {"project_id": pid},
            {"suggestion_type": {"$in": ["active_push", "manual_trigger"]}},
            {"timestamp": {"$gte": cutoff}},
        ]
    }
    recent_results = mem.store.get(
        where=recent_where,
        limit=100,
        include=["documents", "metadatas"],
    )
    # 窗口内无记录时降级到最近 50 条
    if not recent_results.get("ids"):
        recent_results = mem.store.get(
            where={
                "$and": [
                    {"type": "suggestion"},
                    {"project_id": pid},
                    {"suggestion_type": {"$in": ["active_push", "manual_trigger"]}},
                ]
            },
            limit=50,
            include=["documents", "metadatas"],
        )
    # 去重合并
    merged_map: dict = {}
    for doc_id, doc, meta in zip(
        recent_results.get("ids", []),
        recent_results.get("documents", []),
        recent_results.get("metadatas", []),
    ):
        pt = meta.get("suggestion_type", "active_push")
        key = _dedup_key(pt, doc, meta)
        if not key:
            key = doc_id  # fallback: 不合并

        if key not in merged_map:
            merged_map[key] = {
                "id": doc_id,
                "content": doc,
                "suggestion_type": pt,
                "inject_count": 0,
                "first_injected": meta.get("timestamp", 0),
                "latest_injected": meta.get("timestamp", 0),
                "statuses": [],
                "feedback": None,
                "best_similarity": meta.get("similarity", 0),
                "source_memory_id": meta.get("source_memory_id", ""),
                "source_type": meta.get("source_type", ""),
                "source_date": meta.get("source_date", ""),
                "trigger_keywords": meta.get("trigger_keywords", "[]"),
                "hit_count": meta.get("hit_count", 0),
            }
        entry = merged_map[key]
        entry["inject_count"] += 1
        ts = meta.get("timestamp", 0)
        if ts < entry["first_injected"]:
            entry["first_injected"] = ts
        if ts > entry["latest_injected"]:
            entry["latest_injected"] = ts
            entry["id"] = doc_id  # 保留最近一条的 id
            entry["content"] = doc  # 更新到最新内容
        st = meta.get("status", "pending")
        if st not in entry["statuses"]:
            entry["statuses"].append(st)
        # 反馈合并：任一 useful → useful
        fb = meta.get("feedback")
        if fb == "useful":
            entry["feedback"] = "useful"
        elif fb == "not_useful" and entry["feedback"] is None:
            entry["feedback"] = "not_useful"
        # 最高相似度
        sim = meta.get("similarity", 0)
        if sim > entry["best_similarity"]:
            entry["best_similarity"] = sim

    recent_injections = sorted(
        merged_map.values(),
        key=lambda x: x["latest_injected"],
        reverse=True,
    )[:50]

    # ── 7. 最近会话注入记录（基于进程级内存缓存 + Hook 持久化文件） ──
    session_injections = _get_injected_records(pid)
    session_injections.sort(key=lambda x: x["timestamp"], reverse=True)

    # ── 8. 活跃人工建议列表 ──
    active_manual_results = mem.store.get(
        where={
            "$and": [
                {"type": "manual_suggestion"},
                {"project_id": pid},
                {"disabled": False},
                {"status": {"$ne": "dismissed"}},
            ]
        },
        limit=100,
        include=["documents", "metadatas"],
    )
    active_manual_suggestions = []
    for doc_id, doc, meta in zip(
        active_manual_results.get("ids", []),
        active_manual_results.get("documents", []),
        active_manual_results.get("metadatas", []),
    ):
        raw_kw = meta.get("trigger_keywords", "[]")
        if isinstance(raw_kw, str):
            try:
                keywords = json.loads(raw_kw)
            except (json.JSONDecodeError, TypeError):
                keywords = [raw_kw]
        else:
            keywords = raw_kw
        active_manual_suggestions.append(
            {
                "id": doc_id,
                "content": doc,
                "trigger_keywords": keywords,
                "trigger_mode": meta.get("trigger_mode", "keyword"),
                "priority": meta.get("priority", "medium"),
                "hit_count": meta.get("hit_count", 0),
                "cooldown_minutes": meta.get("cooldown_minutes", 60),
                "validity_minutes": meta.get("validity_minutes", 0),
                "last_triggered": meta.get("last_triggered", 0),
            }
        )

    return {
        "pipelines": pipelines,
        "quality": quality,
        "top_source_memories": top_source_memories,
        "manual_suggestions": manual_suggestions,
        "session_injections": session_injections,
        "recent_injections": recent_injections,
        "active_manual_suggestions": active_manual_suggestions,
    }


def _dedup_key(suggestion_type: str, document: str, metadata: dict) -> str | None:
    """根据管道类型返回去重合并键。
    - active_push: source_memory_id
    - manual_trigger: content MD5（前 200 字符）
    - system_alert: event_type
    """
    import hashlib

    if suggestion_type == "active_push":
        return metadata.get("source_memory_id") or None
    elif suggestion_type == "manual_trigger":
        text = (document or "")[:200]
        return hashlib.md5(text.encode("utf-8")).hexdigest()
    elif suggestion_type == "system_alert":
        return metadata.get("event_type") or None
    return None


@router.post("/api/suggestions/toggle-pause")
def toggle_pause_suggestions():
    """切换暂停推送状态 — 创建/删除 .claude/no_suggestions 文件（HIGH-001）。"""
    no_suggestions_path = Path(".claude/no_suggestions")
    if no_suggestions_path.exists():
        no_suggestions_path.unlink()
        return {"enabled": False}
    else:
        no_suggestions_path.parent.mkdir(parents=True, exist_ok=True)
        no_suggestions_path.write_text(
            json.dumps({"created_at": time.time(), "reason": "user_toggle"}, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"enabled": True}


@router.get("/api/suggestions/no-suggestions-status")
def get_no_suggestions_status():
    """查询免打扰文件状态。"""
    no_suggestions_path = Path(".claude/no_suggestions")
    exists = no_suggestions_path.exists()
    content = None
    if exists:
        try:
            content = json.loads(no_suggestions_path.read_text(encoding="utf-8"))
        except Exception:
            content = {"_error": "JSON 解析失败"}
    return {"enabled": exists, "content": content}


# --- 手工建议管理 API (v0.4.4 增强版 Phase 3) ---


@router.post("/api/manual-suggestions")
def create_manual_suggestion(request: Request, req: ManualSuggestionCreateRequest):
    """创建手工建议（含 trigger_keywords json.dumps 序列化）。"""
    mem = request.app.state.mem
    pid = _get_project_id(mem)

    # 验证每个关键词长度
    for kw in req.trigger_keywords:
        if len(kw) > 50:
            raise HTTPException(422, f"关键词长度不能超过 50 字符: {kw}")

    expires_at = (time.time() + req.validity_minutes * 60) if req.validity_minutes > 0 else 0

    meta = {
        "type": "manual_suggestion",
        "project_id": pid,
        "source": "dashboard",
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
        raise HTTPException(500, "保存手工建议失败")

    return {
        "ok": True,
        "id": mid,
        "content": req.content,
        "trigger_keywords": req.trigger_keywords,
        "trigger_mode": req.trigger_mode,
    }


@router.get("/api/manual-suggestions")
def list_manual_suggestions(request: Request):
    """列出当前项目所有手工建议。"""
    mem = request.app.state.mem
    pid = _get_project_id(mem)

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
def delete_manual_suggestion(request: Request, suggestion_id: str):
    """删除手工建议 —— 软删除：转为 type=suggestion 硬关闭，在「历史建议」中可见。

    FIX: 原为硬删除（mem.store.delete），删除后记录消失无法查看。
    改为元数据更新：type → suggestion, suggestion_type → manual_trigger, status → dismissed。
    """
    mem = request.app.state.mem

    results = mem.store.get(ids=[suggestion_id], include=["metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "手工建议不存在")

    meta = dict(results["metadatas"][0])
    if meta.get("type") != "manual_suggestion":
        raise HTTPException(400, "该记录不是手工建议")

    meta["status"] = "dismissed"
    meta["suggestion_type"] = "manual_trigger"
    # expires_at=0（永久有效）会被过期过滤排除，设为一年后
    if not meta.get("expires_at") or meta["expires_at"] < time.time():
        meta["expires_at"] = time.time() + 365 * 86400
    mem.store.update(ids=[suggestion_id], metadatas=[meta])
    return {"ok": True}


@router.put("/api/manual-suggestions/{suggestion_id}")
def update_manual_suggestion(request: Request, suggestion_id: str, req: ManualSuggestionCreateRequest):
    """更新手工建议（用 remember 重建，保持 hit_count 不变）。"""
    mem = request.app.state.mem
    pid = _get_project_id(mem)

    results = mem.store.get(ids=[suggestion_id], include=["metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "手工建议不存在")

    old_meta = results["metadatas"][0]
    if old_meta.get("type") != "manual_suggestion":
        raise HTTPException(400, "该记录不是手工建议")

    for kw in req.trigger_keywords:
        if len(kw) > 50:
            raise HTTPException(422, f"关键词长度不能超过 50 字符: {kw}")

    # 删除旧记录
    mem.store.delete(ids=[suggestion_id])

    # 用 remember 新建（自动计算 embedding）
    expires_at = (time.time() + req.validity_minutes * 60) if req.validity_minutes > 0 else 0
    new_meta = {
        "type": "manual_suggestion",
        "project_id": old_meta.get("project_id", pid),
        "source": "dashboard",
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
        raise HTTPException(500, "更新手工建议失败")

    return {
        "ok": True,
        "id": new_id,
        "content": req.content,
        "trigger_keywords": req.trigger_keywords,
        "trigger_mode": req.trigger_mode,
    }


@router.put("/api/manual-suggestions/{suggestion_id}/toggle-disable")
def toggle_manual_suggestion_disable(request: Request, suggestion_id: str):
    """切换手工建议的临时失效/启用状态。"""
    mem = request.app.state.mem

    results = mem.store.get(ids=[suggestion_id], include=["metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "手工建议不存在")

    meta = dict(results["metadatas"][0])
    if meta.get("type") != "manual_suggestion":
        raise HTTPException(400, "该记录不是手工建议")

    current = bool(meta.get("disabled", False))
    meta["disabled"] = not current
    mem.store.update(ids=[suggestion_id], metadatas=[meta])

    return {"ok": True, "disabled": meta["disabled"]}


# --- 建议设置面板 API (v0.4.4 增强版 Phase 1) ---


def _get_suggestion_field_meta() -> dict:
    """获取建议设置字段的元信息（当前值 + 默认值 + 说明）。"""
    sc = config.suggestion
    ssc = config.system_suggestion

    suggestion_defaults = SuggestionConfig()
    system_defaults = SystemSuggestionConfig()

    return {
        # 管道一
        "active_suggestion_threshold": {
            "value": sc.active_suggestion_threshold,
            "default": suggestion_defaults.active_suggestion_threshold,
            "description": "Layer 2 推送相似度阈值，越高越精确",
            "section": "suggestion",
            "type": "slider",
            "min": 0.0,
            "max": 1.0,
            "step": 0.05,
        },
        "context_injection_threshold": {
            "value": sc.context_injection_threshold,
            "default": suggestion_defaults.context_injection_threshold,
            "description": "Layer 1 上下文注入阈值",
            "section": "suggestion",
            "type": "slider",
            "min": 0.0,
            "max": 1.0,
            "step": 0.05,
        },
        "suggestion_max_per_day": {
            "value": sc.suggestion_max_per_day,
            "default": suggestion_defaults.suggestion_max_per_day,
            "description": "管道一每日最大推送数（24h 滑动窗口）",
            "section": "suggestion",
            "type": "number",
            "min": 0,
            "max": 100,
        },
        "suggestion_max_pending": {
            "value": sc.suggestion_max_pending,
            "default": suggestion_defaults.suggestion_max_pending,
            "description": "最大待处理建议数，超出时 FIFO 自动清理",
            "section": "suggestion",
            "type": "number",
            "min": 10,
            "max": 200,
        },
        "suggestion_display_limit": {
            "value": sc.suggestion_display_limit,
            "default": suggestion_defaults.suggestion_display_limit,
            "description": "Dashboard 单次拉取建议数",
            "section": "suggestion",
            "type": "number",
            "min": 5,
            "max": 100,
        },
        "suggestion_manual_daily_limit": {
            "value": sc.suggestion_manual_daily_limit,
            "default": suggestion_defaults.suggestion_manual_daily_limit,
            "description": "[已废弃] 管道三（手工建议）每日推送上限，由 max_injection_per_round 替代",
            "section": "suggestion",
            "type": "number",
            "min": 0,
            "max": 20,
        },
        "max_injection_per_round": {
            "value": sc.max_injection_per_round,
            "default": suggestion_defaults.max_injection_per_round,
            "description": "每轮会话最多注入的记录数（人工建议 + 知识匹配），按优先级排序后截断",
            "section": "suggestion",
            "type": "number",
            "min": 1,
            "max": 20,
        },
        # 管道二
        "system_suggestion_enabled": {
            "value": ssc.enabled,
            "default": system_defaults.enabled,
            "description": "管道二（系统状态型）全局开关",
            "section": "system_suggestion",
            "type": "toggle",
        },
        "system_suggestion_daily_limit": {
            "value": ssc.daily_limit,
            "default": system_defaults.daily_limit,
            "description": "管道二每日最大推送数",
            "section": "system_suggestion",
            "type": "number",
            "min": 0,
            "max": 10,
        },
        "system_suggestion_cooldown_hours": {
            "value": ssc.cooldown_hours,
            "default": system_defaults.cooldown_hours,
            "description": "管道二同类事件冷却（小时）",
            "section": "system_suggestion",
            "type": "number",
            "min": 1,
            "max": 168,
        },
        "system_suggestion_triggers": {
            "value": ssc.triggers.model_dump(),
            "default": system_defaults.triggers.model_dump(),
            "description": "管道二各触发事件独立开关",
            "section": "system_suggestion",
            "type": "triggers",
        },
    }


@router.get("/api/settings/suggestions")
def get_suggestion_settings():
    """获取建议设置当前值 + 默认值 + 字段说明。"""
    return {"fields": _get_suggestion_field_meta()}


@router.put("/api/settings/suggestions")
def update_suggestion_settings(req: SuggestionSettingsRequest):
    """部分更新建议设置，含跨字段校验 + 即时写回 config.json。"""
    update_data = req.model_dump(exclude_none=True)
    mc = config.suggestion
    sc = config.system_suggestion

    # 管道一字段
    sug_fields = {
        "active_suggestion_threshold",
        "context_injection_threshold",
        "suggestion_max_per_day",
        "suggestion_max_pending",
        "suggestion_display_limit",
        "suggestion_manual_daily_limit",
        "max_injection_per_round",
    }
    for key, val in update_data.items():
        if key in sug_fields:
            setattr(mc, key, val)

    # 阈值交叉校验：active_suggestion >= context_injection
    if mc.active_suggestion_threshold < mc.context_injection_threshold:
        raise HTTPException(
            422,
            f"active_suggestion_threshold ({mc.active_suggestion_threshold}) "
            f"必须 >= context_injection_threshold ({mc.context_injection_threshold})",
        )

    # 跨字段校验：suggestion_max_pending >= suggestion_max_per_day * 2
    if mc.suggestion_max_pending < mc.suggestion_max_per_day * 2:
        raise HTTPException(
            422,
            f"suggestion_max_pending ({mc.suggestion_max_pending}) 必须 >= "
            f"suggestion_max_per_day × 2 ({mc.suggestion_max_per_day * 2})",
        )

    # 管道二字段
    if "system_suggestion_enabled" in update_data:
        sc.enabled = update_data["system_suggestion_enabled"]
    if "system_suggestion_daily_limit" in update_data:
        sc.daily_limit = update_data["system_suggestion_daily_limit"]
    if "system_suggestion_cooldown_hours" in update_data:
        sc.cooldown_hours = update_data["system_suggestion_cooldown_hours"]
    if "system_suggestion_triggers" in update_data:
        for trigger_key, trigger_val in update_data["system_suggestion_triggers"].items():
            if hasattr(sc.triggers, trigger_key) and isinstance(trigger_val, bool):
                setattr(sc.triggers, trigger_key, trigger_val)

    # 即时写回 config.json
    try:
        config.save()
    except Exception as e:
        logger.error("保存配置失败: %s", e)
        raise HTTPException(500, f"保存配置失败: {e}")

    return {"ok": True, "fields": _get_suggestion_field_meta()}


@router.post("/api/settings/suggestions/reset")
def reset_suggestion_settings():
    """一键恢复 Pydantic 默认值。"""
    suggestion_defaults = SuggestionConfig()
    system_defaults = SystemSuggestionConfig()

    mc = config.suggestion
    sc = config.system_suggestion

    # 恢复管道一字段
    mc.active_suggestion_threshold = suggestion_defaults.active_suggestion_threshold
    mc.context_injection_threshold = suggestion_defaults.context_injection_threshold
    mc.suggestion_max_per_day = suggestion_defaults.suggestion_max_per_day
    mc.suggestion_max_pending = suggestion_defaults.suggestion_max_pending
    mc.suggestion_display_limit = suggestion_defaults.suggestion_display_limit
    mc.suggestion_manual_daily_limit = suggestion_defaults.suggestion_manual_daily_limit
    mc.max_injection_per_round = suggestion_defaults.max_injection_per_round

    # 恢复管道二字段
    sc.enabled = system_defaults.enabled
    sc.daily_limit = system_defaults.daily_limit
    sc.cooldown_hours = system_defaults.cooldown_hours
    sc.triggers = system_defaults.triggers

    try:
        config.save()
    except Exception as e:
        logger.error("保存配置失败: %s", e)
        raise HTTPException(500, f"保存配置失败: {e}")

    return {"ok": True, "fields": _get_suggestion_field_meta()}


@router.get("/api/suggestions/preview")
def preview_suggestion_threshold(request: Request, threshold: float = 0.60):
    """预览阈值下预计匹配条数（最近 7 天记忆）。"""
    mem = request.app.state.mem
    pid = _get_project_id(mem)

    # 查询最近 7 天、type 为知识类型的记忆
    cutoff = time.time() - 7 * 86400
    where = {
        "$and": [
            {"project_id": pid},
            {"type": {"$in": ["fact", "decision", "preference", "todo"]}},
            {"timestamp": {"$gte": cutoff}},
        ]
    }
    total = mem.store.count(where=where)

    # 计算在阈值以上的比例（模拟：取 top_k 中高于阈值的比例）
    results = mem.store.get(
        where=where,
        limit=50,
        include=["metadatas"],
    )
    metadatas = results.get("metadatas", [])
    if not metadatas:
        return {"total_knowledge": total, "above_threshold": 0, "threshold": threshold, "ratio": 0}

    above = sum(1 for m in metadatas if m.get("similarity", 0) >= threshold)
    ratio = round(above / len(metadatas), 2) if metadatas else 0

    return {
        "total_knowledge": total,
        "above_threshold": above,
        "threshold": threshold,
        "ratio": ratio,
    }
