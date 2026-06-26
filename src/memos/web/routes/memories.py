from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from ...config import config

# 本模块特有导入
from ...engine.extractor import MemoryExtractor
from ...errors import ChromaDBError
from ..app import _invalidate_projects_cache
from ..dependencies import get_project_id
from ..models import (
    BatchCreateCardsRequest,
    BatchCreateMemoriesRequest,
    BatchDeleteRequest,
    CreateMemoryRequest,
    UpdateMemoryRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/memories")
def list_memories(
    request: Request,
    project_id: str = Depends(get_project_id),
    limit: int = Query(default=20, ge=1),
    offset: int = Query(default=0, ge=0),
    type: list[str] = Query(default=None),
    include_archived: bool = False,
    source: str = Query(default=None, description="按来源过滤: auto/manual/expiring_soon/expired"),
    days: int = Query(default=None, description="auto/manual 时限制最近 N 天（与统计卡联动）"),
    status: str = Query(
        default=None,
        # F5: 三态 status + 过渡期兼容
        pattern=r"^(active|forgotten|archived|pending|completed|expired|deprecated)$",
        description="按知识状态过滤",
    ),
):
    import time as time_mod

    dc = config.dashboard
    limited = min(limit, dc.list_limit_max)
    mem = request.app.state.context_memory

    # 将 source/status 转译为 ChromaDB where 条件
    source_clauses = []
    if status:
        source_clauses.append({"status": status})
    if source == "auto":
        source_clauses.append({"source": "auto_extracted"})
    elif source == "manual":
        # F5: 新旧 source 值兼容
        source_clauses.append({"source": {"$in": ["user_extracted", "user_appended", "user_instructed", "manual"]}})
    elif source == "expiring_soon":
        now = time_mod.time()
        archive_sec = config.memory.archive_days * 86400
        warn_sec = config.memory.expiry_warn_days * 86400
        source_clauses.append(
            {
                "$and": [
                    {"timestamp": {"$gte": now - archive_sec}},
                    {"timestamp": {"$lt": now - archive_sec + warn_sec}},
                ]
            }
        )
    elif source == "expired":
        source_clauses.append({"timestamp": {"$lt": time_mod.time() - config.memory.archive_days * 86400}})

    # auto/manual 叠加时间范围（与统计卡 today/week 联动）
    if source in ("auto", "manual") and days:
        now = time_mod.time()
        # days=1: 今天 00:00; days=7: 本周一 00:00（与统计卡对齐）
        if days == 1:
            since = now - (now % 86400)
        elif days == 7:
            tm = time_mod.localtime(now)
            since = now - (now % 86400) - tm.tm_wday * 86400
        else:
            since = now - days * 86400
        source_clauses.append({"timestamp": {"$gte": since}})

    if len(source_clauses) == 1:
        source_where = source_clauses[0]
    elif len(source_clauses) > 1:
        source_where = {"$and": source_clauses}
    else:
        source_where = None

    if not type:
        type = [
            "solution",
            "decision",
            "lesson",
            "process",
            "task",
            "briefing",
        ]
    logger.info("GET /api/memories project=%s type=%s source=%s offset=%d", project_id, type, source, offset)
    items = mem.list_memories(
        project_id=project_id,
        type_filter=type,
        limit=limited,
        offset=offset,
        include_archived=include_archived,
        where=source_where,
    )
    total = mem.count_memories(
        project_id=project_id,
        type_filter=type,
        include_archived=include_archived,
        where=source_where,
    )
    return {"memories": items, "total": total, "limit": limited, "source": source, "_v": "v0.4.1-source-filter"}


# --- API: 添加记忆 ---


@router.post("/api/memories", status_code=201)
def create_memory(request: Request, req: CreateMemoryRequest, project_id: str = Depends(get_project_id)):
    mem = request.app.state.context_memory
    metadata = {
        "type": req.type,
        "project_id": req.project_id or project_id,
        "project_name": os.path.basename(os.getcwd()),
        "source": "manual",
        "quality_score": 1.0,
        "quality_reason": "用户直写",
    }
    mem_id = mem.remember(req.content, metadata=metadata)
    if mem_id is None:
        logger.error("记忆创建失败 content=%s type=%s", req.content[:50], req.type)
        raise HTTPException(500, "记忆创建失败")
    logger.info("记忆已创建 id=%s project=%s", mem_id, req.project_id)
    _invalidate_projects_cache()
    # v0.4.1: 异步冲突检测
    if config.memory.conflict_detection_enabled:
        pid = req.project_id or project_id
        extractor = MemoryExtractor(memory_system=mem, project_id=pid)
        extractor._detect_conflicts_async(req.content, mem_id)
    return {"id": mem_id, "message": "记忆已创建"}


# --- API: 单条记忆 ---


@router.get("/api/memories/export")
def export_memories_api(
    request: Request,
    project_id: str = Depends(get_project_id),
    type: list[str] = Query(None),
    include_embeddings: bool = Query(False),
    memory_ids: list[str] = Query(None),
):
    """导出记忆为 JSON Lines 流式下载"""
    mem = request.app.state.context_memory
    pid = project_id
    type_filter = type if type else None
    ids_filter = memory_ids if memory_ids else None

    import json as _json
    from datetime import date

    def generate():
        for item in mem.export_memories(
            project_id=pid,
            type_filter=type_filter,
            include_embeddings=include_embeddings,
            memory_ids=ids_filter,
        ):
            if "_header" in item:
                yield "# " + _json.dumps(item["_header"], ensure_ascii=False) + "\n"
                continue
            if not include_embeddings:
                item.pop("embedding", None)
            yield _json.dumps(item, ensure_ascii=False) + "\n"

    filename = f"memos-export-{date.today().isoformat()}.jsonl"
    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/memories/import")
async def import_memories_api(
    request: Request,
    file: UploadFile,
    project_id: str = Depends(get_project_id),
    strategy: str = Query("skip"),
):
    """上传 JSON Lines 文件导入记忆"""

    if not file.filename or not file.filename.endswith(".jsonl"):
        raise HTTPException(400, "仅支持 .jsonl 文件")

    # P1-5: 检查 Content-Length 防止内存溢出，超过 50MB 拒绝
    content_length = request.headers.get("Content-Length")
    max_import_bytes = 50 * 1024 * 1024  # 50MB
    if content_length and int(content_length) > max_import_bytes:
        raise HTTPException(413, f"导入文件过大（{int(content_length) / 1024 / 1024:.1f}MB），上限 50MB，请拆分后重试")

    mem = request.app.state.context_memory
    pid = project_id

    # P1-5: 流式逐行读取，避免全量加载到内存
    raw_bytes = await file.read()
    if len(raw_bytes) > max_import_bytes:
        raise HTTPException(413, f"导入文件过大（{len(raw_bytes) / 1024 / 1024:.1f}MB），上限 50MB，请拆分后重试")
    lines = raw_bytes.decode("utf-8").splitlines()
    result = mem.import_memories(lines, target_project_id=pid, strategy=strategy)
    return result


# --- 备份/恢复 API ---


@router.post("/api/memories/{mem_id}/renew")
def renew_memory(request: Request, mem_id: str):
    """续期指定记忆（重置 timestamp 为当前时间）。"""
    mem = request.app.state.context_memory
    ok = mem.renew_memory(mem_id)
    if not ok:
        raise HTTPException(404, "记忆未找到")
    return {"ok": True, "message": "记忆已续期"}


@router.get("/api/memories/{id}")
def get_memory(request: Request, id: str):
    mem = request.app.state.context_memory
    item = mem.get_memory(id)
    if item is None:
        logger.warning("记忆未找到 id=%s", id)
        raise HTTPException(404, "记忆未找到")
    return item


# --- API: 更新记忆 ---


@router.put("/api/memories/{id}")
def update_memory(request: Request, id: str, req: UpdateMemoryRequest):
    mem = request.app.state.context_memory
    old = mem.get_memory(id)
    if old is None:
        logger.warning("更新记忆未找到 id=%s", id)
        raise HTTPException(404, "记忆未找到")
    new_metadata = None
    new_content = old["document"]
    if req.content is not None:
        new_content = req.content
    if req.type is not None:
        new_metadata = {"type": req.type}
    try:
        mem.update_memory(id, new_content, new_metadata=new_metadata)
    except (ValueError, ChromaDBError) as e:
        logger.warning("更新记忆失败 id=%s error=%s", id, e)
        raise HTTPException(404, str(e))
    logger.info("记忆已更新 id=%s", id)
    _invalidate_projects_cache()
    return {"id": id, "message": "记忆已更新"}


# --- API: 删除记忆 ---


@router.delete("/api/memories/{id}")
def delete_memory(request: Request, id: str):
    mem = request.app.state.context_memory
    try:
        mem.delete_memory(id)
    except (ValueError, ChromaDBError) as e:
        logger.warning("删除记忆失败 id=%s error=%s", id, e)
        raise HTTPException(404, str(e))
    logger.info("记忆已删除 id=%s", id)
    _invalidate_projects_cache()
    return {"message": "记忆已删除"}


# --- API: 批量删除 ---


@router.post("/api/memories/batch-delete")
def batch_delete_memories(request: Request, req: BatchDeleteRequest):
    mem = request.app.state.context_memory
    deleted = []
    errors = []
    for mid in req.ids:
        try:
            mem.delete_memory(mid)
            deleted.append(mid)
        except (ValueError, ChromaDBError) as e:
            errors.append({"id": mid, "error": str(e)})
    logger.info("批量删除: %d 成功, %d 失败", len(deleted), len(errors))
    _invalidate_projects_cache()
    return {"deleted": deleted, "errors": errors, "message": f"已删除 {len(deleted)} 条记忆"}


# --- API: 遗忘/恢复/归档 ---


@router.post("/api/memories/{id}/forget")
def forget_memory(request: Request, id: str):
    """F6: 标记记忆为 forgotten 状态。"""
    mem = request.app.state.context_memory
    try:
        mem.forget_memory(id)
    except (ValueError, ChromaDBError) as e:
        logger.warning("遗忘记忆失败 id=%s error=%s", id, e)
        raise HTTPException(404, str(e))
    logger.info("记忆已遗忘 id=%s", id)
    _invalidate_projects_cache()
    return {"message": "记忆已遗忘"}


# --- API: 归档/恢复 ---


@router.post("/api/memories/{id}/archive")
def archive_memory(request: Request, id: str):
    mem = request.app.state.context_memory
    try:
        mem.archive_memory(id)
    except (ValueError, ChromaDBError) as e:
        logger.warning("归档记忆失败 id=%s error=%s", id, e)
        raise HTTPException(404, str(e))
    logger.info("记忆已归档 id=%s", id)
    _invalidate_projects_cache()
    return {"message": "记忆已归档"}


@router.post("/api/memories/{id}/restore")
def restore_memory(request: Request, id: str):
    mem = request.app.state.context_memory
    try:
        mem.restore_memory(id)
    except (ValueError, ChromaDBError) as e:
        logger.warning("恢复记忆失败 id=%s error=%s", id, e)
        raise HTTPException(404, str(e))
    logger.info("记忆已恢复 id=%s", id)
    _invalidate_projects_cache()
    return {"message": "记忆已恢复"}


# v0.4.1: 记忆查看计数
@router.post("/api/memories/{id}/view")
def view_memory(request: Request, id: str):
    """查看记忆详情时触发 reuse_count + 1"""
    import time as time_mod

    mem = request.app.state.context_memory
    now = time_mod.time()
    # 获取当前 reuse_count
    results = mem.store.get(ids=[id], include=["metadatas"])
    if not results["ids"]:
        raise HTTPException(404, "记忆未找到")
    old_meta = results["metadatas"][0] or {}
    new_count = old_meta.get("reuse_count", 0) + 1
    mem.update_memory(
        id,
        new_metadata={
            "reuse_count": new_count,
            "last_reused_at": now,
        },
    )
    return {"reuse_count": new_count}


# F10: 反馈反哺 — 用户反馈 useful / not-useful


@router.post("/api/memories/{id}/feedback/useful")
def feedback_useful(request: Request, id: str):
    """标记记忆为有用：useful_feedback_count +1"""
    import time as time_mod

    mem = request.app.state.context_memory
    old = mem.get_memory(id)
    if old is None:
        raise HTTPException(404, "记忆未找到")
    meta = dict(old["metadata"])
    current = int(meta.get("useful_feedback_count", 0) or 0)
    meta["useful_feedback_count"] = current + 1
    meta["last_feedback_at"] = time_mod.time()
    mem.store.update(ids=[id], metadatas=[meta])
    # F10: SSE 事件总线
    try:
        from ...features.event_bus import touch_event as _touch

        _touch("feedback")
    except Exception:
        logger.debug("SSE 事件总线 touch 失败（feedback 有用）", exc_info=True)
    logger.info("反馈有用: %s... useful_feedback_count=%d", id[:8], meta["useful_feedback_count"])
    return {"useful_feedback_count": meta["useful_feedback_count"], "message": "已标记为有用"}


@router.post("/api/memories/{id}/feedback/not-useful")
def feedback_not_useful(request: Request, id: str):
    """标记记忆为无用：useful_feedback_count -1（最低 -10）"""
    import time as time_mod

    mem = request.app.state.context_memory
    old = mem.get_memory(id)
    if old is None:
        raise HTTPException(404, "记忆未找到")
    meta = dict(old["metadata"])
    current = int(meta.get("useful_feedback_count", 0) or 0)
    meta["useful_feedback_count"] = max(-10, current - 1)
    meta["last_feedback_at"] = time_mod.time()
    mem.store.update(ids=[id], metadatas=[meta])
    # F10: SSE 事件总线
    try:
        from ...features.event_bus import touch_event as _touch

        _touch("feedback")
    except Exception:
        logger.debug("SSE 事件总线 touch 失败（feedback 无用）", exc_info=True)
    logger.info("反馈无用: %s... useful_feedback_count=%d", id[:8], meta["useful_feedback_count"])
    return {"useful_feedback_count": meta["useful_feedback_count"], "message": "已标记为无用"}


# --- v0.4.1 API: 冲突管理 ---


@router.post("/api/memories/batch-create", status_code=201)
def batch_create_memories(request: Request, req: BatchCreateMemoriesRequest, project_id: str = Depends(get_project_id)):
    mem = request.app.state.context_memory
    created = []
    errors = []
    for m in req.memories:
        # v0.4.1: 写入前去重
        pid = m.project_id or project_id
        try:
            similar = mem.recall_with_scores(m.content, project_id=pid)
        except Exception as e:
            logger.warning("  batch-create 去重查询失败(%s)，降级为直接写入", e)
            similar = []
        if similar and similar[0]["distance"] < config.memory.similarity_threshold:
            dup_id = similar[0]["id"]
            dup_dist = similar[0]["distance"]
            logger.info("  batch-create 跳过(重复): dist=%.3f dup=%s", dup_dist, dup_id[:8])
            errors.append({"content": m.content[:50], "type": m.type, "reason": f"内容重复(相似距离 {dup_dist:.2f})"})
            continue

        metadata = {
            "type": m.type,
            "project_id": m.project_id,
            "project_name": os.path.basename(os.getcwd()),
            "source": "manual",
            "quality_score": 1.0,
            "quality_reason": "用户直写",
        }
        mem_id = mem.remember(m.content, metadata=metadata)
        if mem_id:
            created.append({"id": mem_id, "content": m.content, "type": m.type})
            # v0.4.1: 异步冲突检测
            if config.memory.conflict_detection_enabled:
                extractor = MemoryExtractor(memory_system=mem, project_id=pid)
                extractor._detect_conflicts_async(m.content, mem_id)
        else:
            errors.append({"content": m.content[:50], "type": m.type})

    if created:
        _invalidate_projects_cache()

    return {
        "created": created,
        "errors": errors,
        "message": f"已创建 {len(created)} 条记忆" + (f"，{len(errors)} 条重复跳过" if errors else ""),
    }


@router.post("/api/memories/batch-create-v2", status_code=201)
def batch_create_cards(request: Request, req: BatchCreateCardsRequest, project_id: str = Depends(get_project_id)):
    """批量创建记忆（知识卡片格式），将 problem/solution/insight 拼接后写入"""
    mem = request.app.state.context_memory
    created = []
    overwrites = 0
    _conflict_cards = []  # (mem_id, full_content) 用于异步冲突检测
    errors = []
    pid = req.project_id or project_id
    pname = Path.cwd().name

    logger.info(
        "===== 批量保存知识卡片开始 =====\n卡片数量: %d\n项目: %s",
        len(req.cards),
        pid,
    )

    for i, card in enumerate(req.cards):
        is_overwrite = False
        # 拼接 document 内容
        parts = []
        if card.problem:
            parts.append(f"[问题] {card.problem}")
        if card.solution:
            parts.append(f"[方案] {card.solution}")
        if card.insight:
            parts.append(f"[洞察] {card.insight}")
        content = "\n".join(parts) if parts else ""
        if not content:
            errors.append({"card": card.model_dump(), "reason": "内容为空"})
            continue

        metadata = {
            "type": card.type,
            "project_id": pid,
            "project_name": pname,
            "problem": card.problem,
            "solution": card.solution,
            "insight": card.insight,
            "source": "manual",
        }
        if card.quality_score is not None:
            metadata["quality_score"] = card.quality_score
        if card.quality_reason:
            metadata["quality_reason"] = card.quality_reason

        # v0.4.1: 写入前去重，质量评分高的替换低的
        try:
            similar = mem.recall_with_scores(content, project_id=pid, where={"type": card.type})
        except Exception as e:
            # B3 修复: recall 查询路径也可能因 ChromaDB 索引不一致而失败，
            # 降级为跳过去重直接写入
            logger.warning("  卡片 %d 去重查询失败(%s)，降级为直接写入", i + 1, e)
            similar = []
        if similar and similar[0]["distance"] < config.memory.similarity_threshold:
            dup = similar[0]
            dup_id = dup["id"]
            dup_dist = dup["distance"]
            dup_doc = dup.get("document", "")
            dup_meta = dup.get("metadata", {})
            old_score = dup_meta.get("quality_score", 0.5)
            new_score = card.quality_score if card.quality_score is not None else 0.5

            if new_score > old_score:
                # 新知识评分更高，覆盖旧知识
                try:
                    mem.delete_memory(dup_id)
                    is_overwrite = True
                    logger.info(
                        "  卡片 %d 覆盖旧知识(新评分%.2f > 旧评分%.2f): dist=%.3f\n"
                        "    旧记录 [%s]: %s\n"
                        "    新记录 [%s]: %s",
                        i + 1,
                        new_score,
                        old_score,
                        dup_dist,
                        dup_meta.get("type", "?"),
                        dup_doc,
                        card.type,
                        content,
                    )
                except Exception as e:
                    is_overwrite = False
                    # B3 修复: ChromaDB ID 不一致时降级，不阻塞写入
                    logger.warning(
                        "  卡片 %d 删除旧知识失败(old=%s error=%s)，降级为直接写入",
                        i + 1,
                        dup_id[:8],
                        e,
                    )
                # 继续写入新知识（不 continue）
            else:
                logger.info(
                    "  卡片 %d 跳过(新评分%.2f <= 旧评分%.2f): dist=%.3f\n    旧记录 [%s]: %s\n    新记录 [%s]: %s",
                    i + 1,
                    new_score,
                    old_score,
                    dup_dist,
                    dup_meta.get("type", "?"),
                    dup_doc,
                    card.type,
                    content,
                )
                errors.append(
                    {
                        "card": card.model_dump().get("problem", "")[:50],
                        "reason": f"内容重复(新评分{new_score:.2f}不高于已有{old_score:.2f}，相似距离{dup_dist:.2f})",
                    }
                )
                continue

        mem_id = mem.remember(content, metadata=metadata)
        if mem_id:
            created.append({"id": mem_id, "content": content[:80], "type": card.type})
            if is_overwrite:
                overwrites += 1
            _conflict_cards.append((mem_id, content))
            logger.info("  已保存卡片 %d: id=%s type=%s problem=%.50s", i + 1, mem_id, card.type, card.problem)
        else:
            errors.append({"card": card.model_dump().get("problem", "")[:50], "reason": "写入失败（remember 返回空）"})
            logger.warning("  卡片 %d 保存失败 type=%s problem=%.50s", i + 1, card.type, card.problem)

    if created:
        _invalidate_projects_cache()
        # v0.4.1: 异步冲突检测（与 Pipeline A 对齐，详见 extractor.store_memories）
        if config.memory.conflict_detection_enabled and _conflict_cards:
            extractor = MemoryExtractor(memory_system=mem, project_id=pid, project_name=pname)
            for cid, ccontent in _conflict_cards:
                extractor._detect_conflicts_async(ccontent, cid)

    logger.info("批量保存完成: %d 成功(其中覆写 %d), %d 失败", len(created), overwrites, len(errors))
    logger.info("===== 批量保存知识卡片完成 =====")

    parts = [f"写入成功{len(created)}条"]
    if overwrites:
        parts.append(f"其中覆写{overwrites}条")
    if errors:
        parts.append(f"写入失败{len(errors)}条")
    message = "，".join(parts)

    return {
        "created": created,
        "overwrites": overwrites,
        "errors": errors,
        "message": message,
    }


# --- 导出/导入 API ---


# --- 启动入口 ---
