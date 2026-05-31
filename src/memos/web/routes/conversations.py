from __future__ import annotations

import logging
import os
import time

# 本模块特有导入
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from ...config import config
from ...engine.extractor import MemoryExtractor, _extract_llm_content, _strip_think_block, format_conversation
from ...engine.review import _query_conversations_by_date_range, generate_daily_report
from ...errors import ChromaDBError, LLMUnreachableError
from ..app import _invalidate_projects_cache
from ..models import (
    ConversationSearchRequest,
    DailyReviewRequest,
    ExtractConversationsRequest,
    ExtractConversationsV2Request,
    SaveDailyReviewRequest,
)
from ..services.helpers import _find_llm_endpoint, _parse_knowledge_cards

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/conversations")
def list_conversations(
    request: Request,
    project_id: str = None,
    limit: int = Query(default=50, ge=1),
    offset: int = Query(default=0, ge=0),
):
    mem = request.app.state.mem
    items = mem.list_memories(
        project_id=project_id,
        type_filter=["user_input", "assistant_output"],
        limit=limit,
        offset=offset,
    )
    total = mem.count_memories(
        project_id=project_id,
        type_filter=["user_input", "assistant_output"],
    )
    convs = []
    for item in items:
        meta = item["metadata"]
        convs.append(
            {
                "id": item["id"],
                "project_id": meta.get("project_id", ""),
                "project_name": meta.get("project_name", ""),
                "type": meta.get("type", ""),
                "content": item.get("document", ""),
                "round_id": meta.get("round_id", ""),
                "timestamp": meta.get("timestamp", 0),
            }
        )
    return {"conversations": convs, "total": total, "limit": limit}


@router.post("/api/conversations/search")
def search_conversations(request: Request, req: ConversationSearchRequest):
    """检索会话记录，按 round_id 合并为对话轮次。"""
    mem = request.app.state.mem

    where = {}
    and_clauses = []

    # 类型过滤：仅搜索 user_input 和 assistant_output
    and_clauses.append({"type": {"$in": ["user_input", "assistant_output"]}})

    # 日期范围下推到 ChromaDB where 子句（不做应用层过滤）
    if req.date_from is not None:
        and_clauses.append({"timestamp": {"$gte": req.date_from}})
    if req.date_to is not None:
        and_clauses.append({"timestamp": {"$lte": req.date_to}})

    if len(and_clauses) == 1:
        where = and_clauses[0]
    else:
        where = {"$and": and_clauses}

    try:
        results = mem.recall(
            query=req.query,
            top_k=req.top_k,
            where=where,
            project_id=req.project_id,
            hybrid=True,
            return_scores=True,
        )
    except ChromaDBError as e:
        logger.warning("对话搜索 ChromaDB 异常: %s，降级为关键词匹配", e)
        # v0.4.7: 向量搜索失败时降级为关键词匹配（like 搜索）
        try:
            fallback_results = mem.store.get(
                where=where,
                limit=req.top_k,
                include=["documents", "metadatas"],
            )
            fallback_items = []
            for doc_id, doc, meta in zip(
                fallback_results.get("ids", []),
                fallback_results.get("documents", []),
                fallback_results.get("metadatas", []),
            ):
                if req.query.lower() in (doc or "").lower():
                    fallback_items.append(
                        {
                            "id": doc_id,
                            "document": doc,
                            "metadata": meta,
                            "similarity": 0,
                        }
                    )
            if fallback_items:
                results = fallback_items
            else:
                return {"results": [], "total": 0, "error": str(e), "fallback": True}
        except Exception as e2:
            logger.error("对话搜索降级也失败: %s", e2)
            return {"results": [], "total": 0, "error": str(e)}
    if not results:
        return {"results": [], "total": 0}

    # 结果组装：按 round_id 分组，同轮合并
    round_groups: dict[str, dict] = {}
    unpaired: list[dict] = []

    for r in results:
        meta = r.get("metadata", {}) or {}
        round_id = meta.get("round_id", "")
        item = {
            "id": r.get("id", ""),
            "content": r.get("document", ""),
            "similarity": r.get("similarity", 0),
            "type": meta.get("type", ""),
            "round_id": round_id,
            "timestamp": meta.get("timestamp", 0),
            "project_id": meta.get("project_id", ""),
        }

        if not round_id:
            unpaired.append(item)
            continue

        if round_id not in round_groups:
            round_groups[round_id] = {"round_id": round_id, "user_input": None, "assistant_output": None, "items": []}

        rg = round_groups[round_id]
        t = item["type"]
        # 同一 round_id 多条同类型时取相似度最高者
        if t == "user_input":
            if rg["user_input"] is None or item["similarity"] > rg["user_input"]["similarity"]:
                rg["user_input"] = item
        elif t == "assistant_output":
            if rg["assistant_output"] is None or item["similarity"] > rg["assistant_output"]["similarity"]:
                rg["assistant_output"] = item
        rg["items"].append(item)

    # 格式化输出
    paired_results = []
    for rg in round_groups.values():
        if rg["user_input"] and rg["assistant_output"]:
            paired_results.append(
                {
                    "type": "paired",
                    "round_id": rg["round_id"],
                    "user_input": rg["user_input"],
                    "assistant_output": rg["assistant_output"],
                    "timestamp": rg["user_input"]["timestamp"],
                    "similarity": max(rg["user_input"]["similarity"], rg["assistant_output"]["similarity"]),
                }
            )
        elif rg["user_input"]:
            unpaired.append(rg["user_input"])
        elif rg["assistant_output"]:
            unpaired.append(rg["assistant_output"])

    # 未配对按 similarity 排序
    unpaired.sort(key=lambda x: -x["similarity"])

    return {"results": paired_results + unpaired, "total": len(paired_results) + len(unpaired)}


# --- API: 提炼记忆 ---


@router.post("/api/conversations/extract")
def extract_conversations(request: Request, req: ExtractConversationsRequest):
    mem = request.app.state.mem
    try:
        raw = mem.store.get(ids=req.ids, include=["documents", "metadatas"])
    except Exception as e:
        logger.warning("获取对话记录失败 ids=%s error=%s", req.ids, e)
        raise HTTPException(400, f"获取对话记录失败: {e}")

    ids_list = raw.get("ids") or []
    docs_list = raw.get("documents") or []
    metas_list = raw.get("metadatas") or []

    if not ids_list:
        return {"extracted": [], "message": "未找到指定的对话记录"}

    conversations = []
    for i in range(len(ids_list)):
        doc = docs_list[i] if i < len(docs_list) else ""
        meta = metas_list[i] if i < len(metas_list) else {}
        conversations.append(
            {
                "content": doc,
                "timestamp": meta.get("timestamp", 0) or 0,
            }
        )

    conversations.sort(key=lambda x: x["timestamp"])
    conv_text = "\n".join([c["content"] for c in conversations])

    if not conv_text.strip():
        return {"extracted": [], "message": "对话内容为空"}

    llm_api_url = f"{config.llm.api_base.rstrip('/')}/chat/completions"
    extractor = MemoryExtractor(llm_url=llm_api_url, api_key=config.llm.api_key)
    try:
        extracted = extractor.extract(conv_text)
    except Exception as e:
        logger.warning("记忆提炼服务调用失败 error=%s", e)
        raise LLMUnreachableError(f"记忆提炼服务调用失败: {e}", detail=str(e))
    logger.info("提炼 %d 条对话 -> %d 条记忆", len(req.ids), len(extracted))

    if not extracted:
        return {"extracted": [], "message": "未提取到结构化记忆，请检查 LLM 服务是否正常运行"}

    # 清理提取结果：过滤无效内容，修正类型
    valid_types = {"decision", "preference", "todo", "fact"}
    cleaned = []
    for m in extracted:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        t = m.get("type", "fact")
        if t not in valid_types:
            t = "fact"
        cleaned.append({"content": content, "type": t})

    if not cleaned:
        return {"extracted": [], "message": "提取结果为空，请检查 LLM 返回格式"}

    logger.info("提炼结果已清理: %d -> %d 条", len(extracted), len(cleaned))
    return {"extracted": cleaned, "message": f"已提取 {len(cleaned)} 条记忆"}


# --- API: 批量创建记忆 ---


@router.post("/api/conversations/extract-v2")
def extract_conversations_v2(request: Request, req: ExtractConversationsV2Request):
    """使用提示词模板从对话记录中提炼知识卡片（v2）"""
    mem = request.app.state.mem

    # 确定使用的 LLM 端点
    endpoint_name = req.llm_endpoint or config.llm.active
    target_ep = _find_llm_endpoint(endpoint_name)
    if not target_ep:
        raise LLMUnreachableError(f"LLM 端点 '{endpoint_name}' 不存在", detail="请检查端点配置或切换活跃端点")
    llm_api_url = f"{target_ep.api_base.rstrip('/')}/chat/completions"
    api_key = target_ep.api_key

    # 获取提示词模板（若未指定或为 default，尝试自动匹配端点专属模板）
    prompt_tpl = config.prompt.get(req.prompt_id)
    if not prompt_tpl or req.prompt_id == "default":
        endpoint_tpl = config.prompt.get_for_endpoint(endpoint_name, template_type="extract")
        if endpoint_tpl:
            prompt_tpl = endpoint_tpl
    if not prompt_tpl:
        raise HTTPException(404, f"提示词模板 {req.prompt_id} 不存在")
    prompt_tpl._sync_from_legacy()

    logger.info(
        "===== 手工提炼记忆 v2 开始 =====\n"
        "提示词模板: id=%s name=%s active_version=%s\n"
        "对话记录数量: %d\n"
        "对话记录 IDs: %s",
        prompt_tpl.id,
        prompt_tpl.name,
        prompt_tpl.active_version,
        len(req.ids),
        req.ids,
    )

    # 获取对话记录
    try:
        raw = mem.store.get(ids=req.ids, include=["documents", "metadatas"])
    except Exception as e:
        logger.warning("获取对话记录失败 ids=%s error=%s", req.ids, e)
        raise HTTPException(400, f"获取对话记录失败: {e}")

    ids_list = raw.get("ids") or []
    docs_list = raw.get("documents") or []
    metas_list = raw.get("metadatas") or []

    if not ids_list:
        return {"cards": [], "message": "未找到指定的对话记录"}

    # 构建 records 列表
    records = []
    for i in range(len(ids_list)):
        doc = docs_list[i] if i < len(docs_list) else ""
        meta = metas_list[i] if i < len(metas_list) else {}
        records.append(
            {
                "type": meta.get("type", ""),
                "content": doc,
                "timestamp": meta.get("timestamp", 0) or 0,
            }
        )

    # 格式化对话文本
    conv_text = format_conversation(records)
    if not conv_text.strip():
        return {"cards": [], "message": "对话内容为空"}

    logger.info(
        "----- 格式化的对话文本 -----\n%s\n----- 对话文本结束 -----",
        conv_text,
    )

    # 构建 LLM 请求：若指定版本则用该版本 system_prompt，公共属性取自模板级
    payload = prompt_tpl.build_payload(conv_text, version_override=req.prompt_version or None)
    if "stop" not in payload:
        payload["stop"] = ["<|im_end|>"]
    # 如果端点指定了 model 名，注入请求体
    if target_ep.model:
        payload["model"] = target_ep.model

    for msg in payload.get("messages") or []:
        logger.info(
            "----- messages[%s] -----\n%s\n----- message 结束 -----",
            msg.get("role"),
            msg.get("content", ""),
        )
    logger.info(
        "LLM 请求参数: %s",
        {k: v for k, v in payload.items() if k != "messages"},
    )

    # 调用 LLM
    _extract_start = time.time()
    extractor = MemoryExtractor(llm_url=llm_api_url, api_key=api_key)
    resp = extractor._request_with_retry(payload)
    _extract_duration_ms = int((time.time() - _extract_start) * 1000)
    _input_tokens = len(conv_text)  # 估算
    if resp is None:
        logger.error("LLM 无响应 URL=%s", llm_api_url)
        MemoryExtractor._log_usage("extract_manual_failed", endpoint_name, 0, _input_tokens, 0, _extract_duration_ms)
        return {"cards": [], "message": "记忆提炼服务调用失败: LLM 无响应"}

    try:
        resp_json = resp.json()
    except Exception:
        logger.warning("LLM 响应非 JSON（HTTP %d）前500字: %s", resp.status_code, resp.text[:500])
        MemoryExtractor._log_usage("extract_manual_failed", endpoint_name, 0, _input_tokens, 0, _extract_duration_ms)
        return {"cards": [], "message": f"LLM 响应格式异常（HTTP {resp.status_code}），请检查 LLM 服务状态"}

    raw_text = _extract_llm_content(resp_json)
    _output_tokens = len(raw_text)  # 估算

    logger.info(
        "----- LLM 原始响应 -----\n%s\n----- LLM 响应结束 -----",
        raw_text[:2000] + ("...(截断)" if len(raw_text) > 2000 else ""),
    )

    # 剥离推理模型的 <think> 块（兼容闭合/未闭合标签）
    cleaned = _strip_think_block(raw_text)
    if not cleaned:
        logger.info("LLM 仅输出了 <think> 思考块: %s", raw_text[:200])
        MemoryExtractor._log_usage(
            "extract_manual_failed", endpoint_name, 0, _input_tokens, _output_tokens, _extract_duration_ms
        )
        return {"cards": [], "message": "LLM 仅输出思考过程，未提取到知识卡片"}
    raw_text = cleaned

    # 解析 LLM 输出的 JSON
    cards = _parse_knowledge_cards(raw_text)
    if not cards:
        logger.warning(
            "解析知识卡片失败, LLM 响应(前1000字):\n%s",
            raw_text[:1000],
        )
        MemoryExtractor._log_usage(
            "extract_manual_failed", endpoint_name, 0, _input_tokens, _output_tokens, _extract_duration_ms
        )
        return {"cards": [], "message": "未提取到结构化知识卡片，请检查 LLM 服务是否正常运行"}

    MemoryExtractor._log_usage(
        "extract_manual_success", endpoint_name, len(cards), _input_tokens, _output_tokens, _extract_duration_ms
    )

    logger.info("v2 提炼 %d 条对话 -> %d 条卡片", len(req.ids), len(cards))
    for i, card in enumerate(cards):
        logger.info(
            "  卡片 %d: type=%s problem=%.60s solution=%.60s insight=%.60s",
            i + 1,
            card.get("type"),
            card.get("problem", ""),
            card.get("solution", ""),
            card.get("insight", ""),
        )
    logger.info("===== 手工提炼记忆 v2 完成 =====")
    return {
        "cards": cards,
        "conversation_count": len(req.ids),
        "prompt_id": prompt_tpl.id,
        "prompt_version": req.prompt_version or prompt_tpl.active_version,
        "llm_endpoint": endpoint_name,
        "message": f"已提取 {len(cards)} 条知识卡片",
    }


@router.post("/api/conversations/extract-preview")
def extract_conversations_preview(request: Request, req: ExtractConversationsV2Request):
    """预览发送给 LLM 的请求消息（不实际调用 LLM）"""
    mem = request.app.state.mem

    endpoint_name = req.llm_endpoint or config.llm.active
    target_ep = _find_llm_endpoint(endpoint_name)
    if not target_ep:
        raise LLMUnreachableError(f"LLM 端点 '{endpoint_name}' 不存在", detail="请检查端点配置或切换活跃端点")

    prompt_tpl = config.prompt.get(req.prompt_id)
    if not prompt_tpl or req.prompt_id == "default":
        endpoint_tpl = config.prompt.get_for_endpoint(endpoint_name, template_type="extract")
        if endpoint_tpl:
            prompt_tpl = endpoint_tpl
    if not prompt_tpl:
        raise HTTPException(404, f"提示词模板 {req.prompt_id} 不存在")
    prompt_tpl._sync_from_legacy()

    raw = mem.store.get(ids=req.ids, include=["documents", "metadatas"])
    ids_list = raw.get("ids") or []
    docs_list = raw.get("documents") or []
    metas_list = raw.get("metadatas") or []

    if not ids_list:
        return {"messages": [], "payload": {}}

    records = []
    for i in range(len(ids_list)):
        doc = docs_list[i] if i < len(docs_list) else ""
        meta = metas_list[i] if i < len(metas_list) else {}
        records.append(
            {
                "type": meta.get("type", ""),
                "content": doc,
                "timestamp": meta.get("timestamp", 0) or 0,
            }
        )

    conv_text = format_conversation(records)

    payload = prompt_tpl.build_payload(conv_text, version_override=req.prompt_version or None)

    if "stop" not in payload:
        payload["stop"] = ["<|im_end|>"]
    if target_ep.model:
        payload["model"] = target_ep.model

    return {
        "llm_url": f"{target_ep.api_base.rstrip('/')}/chat/completions",
        "llm_endpoint": endpoint_name,
        "messages": payload.get("messages", []),
        "payload": {k: v for k, v in payload.items() if k != "messages"},
    }


# --- API: 今日回顾 ---


@router.post("/api/conversations/daily-review")
def generate_daily_review(request: Request, req: DailyReviewRequest):
    """根据当天对话记录生成开发日报（Markdown 格式）"""
    mem = request.app.state.mem

    # 日期格式校验
    if req.date:
        try:
            datetime.strptime(req.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, f"无效日期格式: {req.date}，请使用 YYYY-MM-DD")

    # 端点存在性校验
    if req.llm_endpoint:
        target_ep = None
        for ep in config.llm.endpoints:
            if ep.name == req.llm_endpoint:
                target_ep = ep
                break
        if not target_ep:
            raise LLMUnreachableError(f"LLM 端点 '{req.llm_endpoint}' 不存在", detail="请检查端点配置或切换活跃端点")

    result = generate_daily_report(
        mem=mem,
        target_date=req.date,
        project_id=req.project_id,
        llm_endpoint=req.llm_endpoint,
        prompt_id=req.prompt_id,
        prompt_version=req.prompt_version,
        save_as_memory=req.save_as_memory,
    )

    if result.get("saved_id") and not result["report"]:
        _invalidate_projects_cache()

    if result.get("message"):
        logger.info("今日回顾: %s", result["message"])

    return {
        "report": result["report"],
        "date": result["date"],
        "conversation_count": result["conversation_count"],
        "raw_rounds": result.get("raw_rounds", result["conversation_count"]),
        "filtered_rounds": result.get("filtered_rounds", result["conversation_count"]),
        "strategy": result.get("strategy", "direct"),
        "llm_calls": result.get("llm_calls", 1),
        "llm_endpoint": result.get("llm_endpoint"),
        "prompt_id": result.get("prompt_id"),
        "prompt_version": result.get("prompt_version"),
        "saved_id": result.get("saved_id"),
        "message": result["message"],
        "fallback": result.get("fallback", False),
    }


@router.post("/api/conversations/daily-review/preview")
def preview_daily_review(request: Request, req: DailyReviewRequest):
    """预览今日回顾的 LLM 请求内容（不实际调用 LLM）"""
    mem = request.app.state.mem

    target_date = req.date or datetime.now().strftime("%Y-%m-%d")
    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, f"无效日期格式: {target_date}")

    start_of_day = datetime(dt.year, dt.month, dt.day, 0, 0, 0).timestamp()
    end_of_day = datetime(dt.year, dt.month, dt.day, 23, 59, 59).timestamp()

    records = _query_conversations_by_date_range(mem, start_of_day, end_of_day, project_id=req.project_id)
    if not records:
        return {"messages": [], "payload": {}, "date": target_date, "conversation_count": 0}

    conv_text = format_conversation(records)
    conv_text = f"Today's date: {target_date}\n\n{conv_text}"

    endpoint_name = req.llm_endpoint or config.llm.active
    target_ep = _find_llm_endpoint(endpoint_name)
    if not target_ep:
        raise LLMUnreachableError(f"LLM 端点 '{endpoint_name}' 不存在", detail="请检查端点配置或切换活跃端点")

    prompt_tpl = None
    if req.prompt_id:
        prompt_tpl = config.prompt.get(req.prompt_id)
    if not prompt_tpl:
        prompt_tpl = config.prompt.get_for_endpoint(endpoint_name, template_type="daily-review")
    if not prompt_tpl:
        raise HTTPException(404, "未找到每日回顾提示词模板")
    prompt_tpl._sync_from_legacy()

    payload = prompt_tpl.build_payload(conv_text, version_override=req.prompt_version or None)
    if target_ep.model:
        payload["model"] = target_ep.model

    return {
        "llm_url": f"{target_ep.api_base.rstrip('/')}/chat/completions",
        "llm_endpoint": endpoint_name,
        "date": target_date,
        "conversation_count": len(records),
        "messages": payload.get("messages", []),
        "payload": {k: v for k, v in payload.items() if k != "messages"},
    }


@router.post("/api/conversations/daily-review/save")
def save_daily_review(request: Request, req: SaveDailyReviewRequest):
    """保存日报到项目目录 document/日报/ 下"""
    # 确定保存目录：优先使用前端传入的项目目录，其次 CLAUDE_PROJECT_DIR，最后 CWD
    if req.project_dir:
        base = Path(req.project_dir)
    elif os.environ.get("CLAUDE_PROJECT_DIR"):
        base = Path(os.environ["CLAUDE_PROJECT_DIR"])
    else:
        base = Path.cwd()
    daily_dir = base / "document" / "日报"
    daily_dir.mkdir(parents=True, exist_ok=True)

    # 文件名：YYYY-MM-DD-开发日报.md
    filename = f"{req.date}-开发日报.md"
    filepath = daily_dir / filename

    try:
        filepath.write_text(req.report, encoding="utf-8")
    except Exception as e:
        logger.error("保存日报失败 path=%s error=%s", filepath, e)
        raise HTTPException(500, f"保存日报失败: {e}")

    logger.info("日报已保存 path=%s size=%d", filepath, len(req.report))
    return {
        "message": f"日报已保存到 document/日报/{filename}",
        "path": str(filepath.relative_to(base)),
    }
