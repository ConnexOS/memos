from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from ...config import config

# 本模块特有导入
from ..app import _get_projects_from_db, _invalidate_projects_cache
from ..dependencies import get_project_id
from ..services.helpers import _calc_db_size, _get_llama_status
from ..utils import detect_project_id

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/conflicts")
def list_conflicts(request: Request, limit: int = 50, project_id: str = Depends(get_project_id)):
    """获取待处理冲突配对列表（合并同一冲突的两条记忆）"""
    import time as time_mod

    mem = request.app.state.context_memory
    results = mem.list_memories(
        where={"conflict_status": "pending"},
        project_id=project_id,
        limit=200,
    )
    if not results:
        return {"pairs": [], "total": 0}

    # 按 conflict_with 配对：同一冲突的两条记忆合并为一个 pair
    pair_map: dict[str, dict] = {}
    for m in results:
        meta = m.get("metadata", {})
        cw = meta.get("conflict_with", "")
        cid = m["id"]
        if not cw:
            continue
        # 以排序后的 ID 对作为 pair key
        key = tuple(sorted([cid, cw]))
        if key not in pair_map:
            pair_map[key] = {
                "id": "",  # 始终指向 trigger 记忆的 ID
                "new_memory": None,
                "existing_memory": None,
                "reason": meta.get("conflict_reason", ""),
                "similarity": 0.0,
                "detected_at": meta.get("conflict_detected_at", 0),
            }
        role = meta.get("conflict_role", "")
        if role == "trigger" or (role == "" and pair_map[key]["new_memory"] is None):
            pair_map[key]["id"] = cid  # 始终使用 trigger 记忆的 ID 作为 pair_id
            pair_map[key]["new_memory"] = {
                "id": cid,
                "content": m.get("document", ""),
                "type": meta.get("type", ""),
                "created_at": meta.get("timestamp", ""),
            }
        else:
            pair_map[key]["existing_memory"] = {
                "id": cid,
                "content": m.get("document", ""),
                "type": meta.get("type", ""),
                "created_at": meta.get("timestamp", ""),
            }

    # 补充缺失端（只有一条记录在 pending 中时，从 DB 拉取完整信息）
    _now = time_mod.time()
    for key, pair in list(pair_map.items()):
        if pair["new_memory"] is None or pair["existing_memory"] is None:
            # 从 DB 拉取缺失的那条：根据已存在的记忆确定哪条缺失
            if pair["new_memory"] is None and pair["existing_memory"] is not None:
                existing_id = pair["existing_memory"]["id"]
                missing_id = key[1] if existing_id == key[0] else key[0]
            elif pair["existing_memory"] is None and pair["new_memory"] is not None:
                new_id = pair["new_memory"]["id"]
                missing_id = key[1] if new_id == key[0] else key[0]
            else:
                continue
            try:
                got = mem.get_memory(missing_id)
                if got:
                    gmeta = got.get("metadata", {})
                    target = "new_memory" if pair["new_memory"] is None else "existing_memory"
                    pair[target] = {
                        "id": got["id"],
                        "content": got.get("document", ""),
                        "type": gmeta.get("type", ""),
                        "created_at": gmeta.get("timestamp", ""),
                    }
                    if target == "new_memory":
                        pair["id"] = got["id"]
                else:
                    # 对方记忆已删除 → 自清理孤立冲突
                    if pair.get("existing_memory"):
                        orphan_id = pair["existing_memory"]["id"]
                        try:
                            mem.update_memory(
                                orphan_id,
                                new_metadata={"conflict_status": "dismissed", "conflict_cleanup": "auto"},
                            )
                            logger.info("自清理孤立冲突记忆: %s (对方已删除)", orphan_id[:8])
                        except Exception:
                            pass
                    elif pair.get("new_memory"):
                        orphan_id = pair["new_memory"]["id"]
                        try:
                            mem.update_memory(
                                orphan_id,
                                new_metadata={"conflict_status": "dismissed", "conflict_cleanup": "auto"},
                            )
                            logger.info("自清理孤立冲突记忆: %s (对方已删除)", orphan_id[:8])
                        except Exception:
                            pass
            except Exception:
                logger.warning("无法获取冲突对方记忆: %s", missing_id[:8])

    # 过滤孤立冲突（对方记忆已删除，无法解决）
    pair_map = {k: v for k, v in pair_map.items() if v["id"]}

    # 按 detected_at 降序
    pairs = sorted(pair_map.values(), key=lambda p: p.get("detected_at", 0), reverse=True)
    pairs = pairs[:limit]

    return {"pairs": pairs, "total": len(pairs)}


@router.get("/api/conflicts/count")
def count_conflicts(request: Request, project_id: str = Depends(get_project_id)):
    """待处理冲突数量（供首页徽标）"""
    mem = request.app.state.context_memory
    try:
        all_pending = mem.list_memories(where={"conflict_status": "pending"}, project_id=project_id, limit=500)
        return {"count": len(all_pending)}
    except Exception:
        return {"count": 0}


def _write_conflict_log(
    mem, pair_id: str, new_content: str, conflicting_content: str, reason: str, similarity: float, decision: str
):
    """将冲突决策写入 ChromaDB type=conflict_log"""
    import time as time_mod

    try:
        mem.remember(
            f"冲突决策: {decision} — {reason}",
            metadata={
                "type": "conflict_log",
                "conflict_pair_id": pair_id,
                "new_content": new_content[:200],
                "conflicting_content": conflicting_content[:200],
                "reason": reason,
                "similarity": similarity,
                "decision": decision,
                "decided_at": time_mod.time(),
                "decided_by": "user",
            },
        )
    except Exception as e:
        logger.warning("冲突日志写入失败: %s", e)


def _get_conflict_pair(request: Request, pair_id: str) -> tuple[dict, dict]:
    """根据 new_memory_id 查找冲突配对的两条记忆。返回 (new_memory, existing_memory)。"""
    mem = request.app.state.context_memory
    new_mem = mem.get_memory(pair_id)
    if new_mem is None:
        raise HTTPException(404, f"冲突记录未找到: {pair_id[:8]}")
    meta = new_mem.get("metadata", {})
    if meta.get("conflict_status") != "pending":
        raise HTTPException(404, f"冲突记录不存在或已处理: {pair_id[:8]}")
    existing_id = meta.get("conflict_with", "")
    if not existing_id:
        raise HTTPException(400, "该记忆无关联冲突对象")
    existing_mem = mem.get_memory(existing_id)
    if existing_mem is None:
        raise HTTPException(404, f"冲突对象未找到: {existing_id[:8]}")
    return new_mem, existing_mem


@router.post("/api/conflicts/{pair_id}/resolve")
async def resolve_conflict(request: Request, pair_id: str, action: str = ""):
    """统一冲突解决入口。
    action=overwrite — 删除旧记忆（conflict_with），保留新记忆
    action=keep_both — 清除双方 conflict_status
    action=edit      — 更新新记忆内容并清除冲突标记（content 在请求体中）
    """
    import time as time_mod

    valid_actions = {"overwrite", "keep_both", "edit"}
    if action not in valid_actions:
        raise HTTPException(400, f"无效 action: {action}，可选: {', '.join(sorted(valid_actions))}")

    # edit 操作需要从请求体读取 content
    content = ""
    if action == "edit":
        try:
            body = await request.json()
            content = (body.get("content") or "").strip()
        except Exception:
            raise HTTPException(400, "edit 操作需要提供 JSON body {content: ...}")
        if not content:
            raise HTTPException(400, "edit 操作需要提供 content")

    mem = request.app.state.context_memory
    new_mem, existing_mem = _get_conflict_pair(request, pair_id)
    new_meta = new_mem.get("metadata", {})

    if action == "overwrite":
        # 删除旧记忆
        mem.delete_memory(existing_mem["id"])
        # 清除新记忆的冲突标记
        mem.update_memory(
            pair_id,
            new_metadata={
                "conflict_status": "resolved",
                "conflict_role": "",
                "conflict_with": "",
                "conflict_reason": "",
                "conflict_resolved_at": time_mod.time(),
                "conflict_resolution": "overwrite",
            },
        )
    elif action == "keep_both":
        # 清除双方冲突标记
        clear_meta = {
            "conflict_status": "resolved",
            "conflict_role": "",
            "conflict_with": "",
            "conflict_reason": "",
            "conflict_resolved_at": time_mod.time(),
            "conflict_resolution": "keep_both",
        }
        mem.update_memory(pair_id, new_metadata=clear_meta)
        mem.update_memory(existing_mem["id"], new_metadata=clear_meta)
    elif action == "edit":
        # 更新新记忆内容 + 清除冲突标记
        mem.update_memory(
            pair_id,
            new_content=content.strip(),
            new_metadata={
                "conflict_status": "resolved",
                "conflict_role": "",
                "conflict_with": "",
                "conflict_reason": "",
                "conflict_resolved_at": time_mod.time(),
                "conflict_resolution": "edit",
            },
        )

    # 写入冲突日志
    _write_conflict_log(
        mem,
        pair_id,
        new_mem.get("document", ""),
        existing_mem.get("document", ""),
        new_meta.get("conflict_reason", ""),
        new_meta.get("conflict_similarity", 0),
        action,
    )

    logger.info("冲突已解决 id=%s action=%s", pair_id[:8], action)
    return {"message": f"冲突已通过「{action}」解决"}


@router.post("/api/conflicts/{pair_id}/discard")
def discard_conflict(request: Request, pair_id: str):
    """放弃新记忆：删除触发冲突的新记忆，保留旧记忆"""
    import time as time_mod

    mem = request.app.state.context_memory
    new_mem, existing_mem = _get_conflict_pair(request, pair_id)
    new_meta = new_mem.get("metadata", {})

    # 删除新记忆
    mem.delete_memory(pair_id)
    # 更新旧记忆：清除冲突标记
    try:
        mem.update_memory(
            existing_mem["id"],
            new_metadata={
                "conflict_status": "resolved",
                "conflict_role": "",
                "conflict_with": "",
                "conflict_reason": "",
                "conflict_resolved_at": time_mod.time(),
                "conflict_resolution": "discard",
            },
        )
    except Exception as e:
        logger.warning("清除冲突标记失败 id=%s: %s", existing_mem["id"][:8], e)

    # 写入冲突日志
    _write_conflict_log(
        mem,
        pair_id,
        new_mem.get("document", ""),
        existing_mem.get("document", ""),
        new_meta.get("conflict_reason", ""),
        new_meta.get("conflict_similarity", 0),
        "discard",
    )

    logger.info("已放弃新记忆 id=%s", pair_id[:8])
    return {"message": "已放弃新记忆"}


@router.get("/api/conflicts/stats")
def conflict_stats(request: Request, project_id: str = Depends(get_project_id)):
    """冲突决策统计（聚合 decision 分布）"""
    mem = request.app.state.context_memory
    try:
        logs = mem.list_memories(where={"type": "conflict_log"}, project_id=project_id, limit=500)
    except Exception:
        return {"total": 0, "decisions": {}}
    decisions: dict[str, int] = {}
    for log in logs:
        meta = log.get("metadata", {})
        d = meta.get("decision", "unknown")
        decisions[d] = decisions.get(d, 0) + 1
    return {"total": len(logs), "decisions": decisions}


# --- v0.4.5 R2: 从日报提取待办 ---


@router.post("/api/conversations/extract-todos")
def extract_todos_from_review(request: Request, body: dict, project_id: str = Depends(get_project_id)):
    """从今日回顾日报中提取待办事项（LLM 识别可执行项 + 去重 + 批量写入）。"""
    import json as _json
    import time as _time
    from pathlib import Path as _Path

    from ...engine.extractor import _extract_llm_content, _strip_think_block, get_llm_api_key, get_llm_url

    date = (body.get("date") or "").strip()
    project_id = body.get("project_id") or project_id

    if not date:
        from datetime import date as _date

        date = _date.today().isoformat()

    # 优先使用前端传来的 report_text（生成但未保存时也能工作）
    report_text = (body.get("report_text") or "").strip()
    if not report_text:
        # 兜底：从磁盘文件读取
        import os as _os

        project_dir = body.get("project_dir") or _os.environ.get("CLAUDE_PROJECT_DIR")
        base = _Path(project_dir) if project_dir else _Path.cwd()
        reports_dir = base / "document" / "日报"
        candidates = [
            reports_dir / f"{date}-开发日报.md",
            reports_dir / f"{date}-日报.md",
        ]
        report_path = None
        for c in candidates:
            if c.exists():
                report_path = c
                break

        if report_path is None:
            raise HTTPException(404, f"未找到 {date} 的日报文件")

        report_text = report_path.read_text(encoding="utf-8")

    if not report_text.strip():
        raise HTTPException(400, "日报内容为空")

    # 调用 LLM 提取待办
    endpoint_name = config.llm.active_endpoint.name if config.llm.active_endpoint else "default"
    tpl = config.prompt.get_for_endpoint(endpoint_name, template_type="todo-extract")
    if tpl is None:
        tpl = config.prompt.get_for_endpoint("default", template_type="todo-extract")
    if tpl is None:
        raise HTTPException(500, "todo-extract 提示词模板不存在")

    payload = tpl.build_payload(report_text)
    payload.setdefault("temperature", config.llm.temperature)
    payload.setdefault("max_tokens", 4096)
    model_name = config.llm.active_endpoint.model
    if model_name:
        payload["model"] = model_name

    headers = {"Content-Type": "application/json"}
    api_key = get_llm_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        import requests

        llm_url = get_llm_url()
        resp = requests.post(llm_url, json=payload, headers=headers, timeout=config.llm.request_timeout)
        if resp.status_code != 200:
            raise HTTPException(502, f"LLM 返回 {resp.status_code}")
        raw_text = _extract_llm_content(resp.json())
        cleaned = _strip_think_block(raw_text)
        # 解析 JSON 数组
        extracted = []
        try:
            extracted = _json.loads(cleaned)
        except _json.JSONDecodeError:
            import re

            arr_match = re.search(r"\[[\s\S]*?\]", cleaned)
            if arr_match:
                try:
                    extracted = _json.loads(arr_match.group(0))
                except _json.JSONDecodeError:
                    pass
        if not isinstance(extracted, list):
            extracted = []
    except requests.RequestException as e:
        raise HTTPException(502, f"LLM 请求失败: {e}")

    if not extracted:
        return {"todos": [], "total": 0, "skipped": 0, "message": "未提取到待办事项"}

    # 去重 + 批量写入
    mem = request.app.state.context_memory
    pname = _Path.cwd().name
    todos = []
    skipped = 0
    for item in extracted:
        content = (item.get("content") or "").strip()
        if not content:
            skipped += 1
            continue
        priority = item.get("priority", "medium")
        if priority not in ("high", "medium", "low"):
            priority = "medium"

        context = (item.get("context") or "").strip()[:300]  # 截断避免过长

        # 去重检查
        try:
            similar = mem.recall_with_scores(content, project_id=project_id, where={"type": "todo"})
        except Exception:
            similar = []
        if similar:
            dist = similar[0].get("distance", 1.0)
            if dist < config.memory.similarity_threshold:
                skipped += 1
                continue

        # 写入（含 context 背景说明 + source_date 来源日期）
        mid = mem.remember(
            content,
            metadata={
                "type": "todo",
                "todo_status": "pending",
                "priority": priority,
                "context": context,
                "source_date": date,
                "source": "review_extracted",
                "project_id": project_id,
                "project_name": pname,
                "status_history": _json.dumps([]),
                "timestamp": _time.time(),
            },
        )
        if mid:
            todos.append({"id": mid, "content": content, "context": context, "priority": priority})
        else:
            skipped += 1

    return {
        "todos": todos,
        "total": len(todos),
        "skipped": skipped,
        "message": f"已提取 {len(todos)} 条待办，跳过 {skipped} 条",
    }


# --- v0.4.1 API: 用量统计 ---


@router.get("/api/stats/usage")
def usage_stats(request: Request, period: str = "today", endpoint: str = "all", project_id: str = None):
    """获取用量统计数据。卡片数按 source 真实统计（保存数，非提炼调用数）。"""
    from ...features.usage import usage_logger

    mem = request.app.state.context_memory
    stats = usage_logger.get_stats(period=period, endpoint=endpoint, memory=mem, project_id=project_id)
    return stats


@router.get("/api/stats/trend")
def usage_trend(request: Request, days: int = 7):
    """获取近 N 天用量趋势"""
    from ...features.usage import usage_logger

    trend = usage_logger.get_trend(days=days)
    return {"trend": trend}


# --- API: 高级检索 ---


@router.get("/api/projects")
def list_projects(request: Request):
    projects = _get_projects_from_db(request.app.state.context_memory)
    return {"projects": projects, "current_project": detect_project_id(), "current_project_name": Path.cwd().name}


# --- 项目级数据管理（v0.4.8+）---


@router.get("/api/projects/{project_id}/stats")
def get_project_stats(project_id: str, request: Request):
    """获取指定项目的数据统计概览（按类型分布）"""
    from collections import Counter

    mem = request.app.state.context_memory
    try:
        result = mem.store.get(where={"project_id": project_id}, include=["metadatas"])
    except Exception:
        logger.warning("获取项目 %s 统计失败", project_id)
        return {"total": 0, "by_type": {}}
    if not result or not result.get("metadatas"):
        return {"total": 0, "by_type": {}}
    stats = Counter(m.get("type", "unknown") for m in result["metadatas"])
    return {"total": len(result["metadatas"]), "by_type": dict(stats)}


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str, request: Request):
    """删除指定项目的全部数据。幂等——项目已空或不存在也返回成功。"""
    mem = request.app.state.context_memory
    try:
        # 先查出该项目全部 ID
        existing = mem.store.get(where={"project_id": project_id}, include=["metadatas"])
        ids = existing.get("ids", []) if existing else []
        if len(ids) > 20000:
            logger.warning("项目 %s 包含 %d 条记录，数量较大", project_id, len(ids))
        if ids:
            # 分批删除
            batch_size = 500
            for i in range(0, len(ids), batch_size):
                batch = ids[i : i + batch_size]
                mem.store.delete(batch)
        _invalidate_projects_cache()
        logger.info("项目 %s 已删除，清除 %d 条记录", project_id, len(ids))
        return {"deleted": True, "count": len(ids)}
    except Exception as e:
        logger.error("删除项目 %s 失败: %s", project_id, e)
        raise HTTPException(500, f"删除失败: {e}")


# --- API: 系统状态 ---


@router.get("/api/status")
async def system_status(request: Request):
    mem = request.app.state.context_memory
    llama_ok = await _get_llama_status()
    try:
        total = mem.store.count()
    except Exception:
        total = 0
    db_size_mb = _calc_db_size()
    # v0.4.0 HIGH-3: 增加活跃/已删除统计 + 更正 model_name 数据源
    try:
        stats = mem._get_deleted_stats()
        active_count = stats["active"]
        deleted_count = stats["deleted"]
    except Exception:
        active_count = total
        deleted_count = 0
    return {
        "llama_server_ok": llama_ok,
        "total_memories": total,
        "active_count": active_count,
        "deleted_count": deleted_count,
        "db_size_mb": db_size_mb,
        "vector_dim": config.model.vector_dim,
        "model_name": config.model.name,
        "active_endpoint": config.llm.active,
    }


# --- API: Vacuum（v0.4.0 HIGH-3 修复）---


@router.post("/api/vacuum")
def trigger_vacuum(request: Request):
    """手动触发数据库 VACUUM，回收已删除文档的磁盘空间。返回执行前后文件大小。"""
    mem = request.app.state.context_memory
    db_path = Path(config.chroma.path) / "chroma.sqlite3"
    if not db_path.exists():
        raise HTTPException(400, "数据库文件不存在")
    before = db_path.stat().st_size
    # v0.4.0 HIGH-2: 加锁防止 VACUUM 期间并发写入
    with mem._vacuum_lock:
        ok = mem.store.vacuum()
    if ok:
        after = db_path.stat().st_size
        reclaimed = before - after
        logger.info(
            "Dashboard 手动 VACUUM: %.1fMB → %.1fMB (回收 %.1fMB)",
            before / 1024 / 1024,
            after / 1024 / 1024,
            reclaimed / 1024 / 1024,
        )
        return {
            "message": f"VACUUM 完成，回收 {reclaimed / 1024 / 1024:.1f}MB",
            "before_bytes": before,
            "after_bytes": after,
            "reclaimed_bytes": reclaimed,
        }
    raise HTTPException(500, "VACUUM 执行失败，请查看服务端日志")
