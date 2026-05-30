"""
今日回顾 — 基于对话记录生成开发日报。

提供 CLI (memos today) 和 Dashboard API 共用的日报生成引擎。
"""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from ..config import config
from ..config.models import _compute_default_project_id
from .extractor import MemoryExtractor, _extract_llm_content, _strip_think_block, format_conversation

logger = logging.getLogger(__name__)

# --- 三级递进策略 ---

_CMD_PATTERNS = re.compile(
    r"^\s*(ls|dir|cd|pwd|git\s+status|git\s+log|git\s+diff|python\s+-m\s+pytest|"
    r"npm\s+test|cargo\s+test|go\s+test|pip\s+list|pip\s+freeze|"
    r"cat|head|tail|echo|whoami|date|time)\b",
    re.IGNORECASE,
)
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.DOTALL)


class DailyReviewStrategy(Enum):
    DIRECT = "direct"  # 原文直出，1次LLM调用
    PRE_SUMMARIZE = "pre_summarize"  # 轮次预摘要，2次LLM调用
    BATCH = "batch"  # 分批预摘要，2~5次LLM调用
    HIERARCHICAL = "hierarchical"  # 分层摘要，多层压缩
    PLAIN_LIST = "plain_list"  # 纯文本清单，0次LLM调用


@dataclass
class PrecleanStats:
    total: int = 0
    filtered_out: int = 0
    merged: int = 0
    details: list[str] = field(default_factory=list)


def _group_by_round(records: list[dict]) -> list[dict]:
    """将单条消息记录按 round_id 分组为对话轮次。

    每条输入记录含 type (user_input/assistant_output)、content、round_id 等字段。
    输出每条代表一轮完整对话，含 user_content / assistant_content / records 字段。
    """
    rounds_map: dict[str, dict] = {}
    orphans = []

    for r in records:
        rid = r.get("round_id", "")
        if not rid:
            orphans.append(r)
            continue
        if rid not in rounds_map:
            rounds_map[rid] = {
                "round_id": rid,
                "user_content": "",
                "assistant_content": "",
                "records": [],
                "timestamp": r.get("timestamp", 0),
            }
        entry = rounds_map[rid]
        entry["timestamp"] = min(entry["timestamp"], r.get("timestamp", 0) or 0)
        if r.get("type") == "user_input":
            entry["user_content"] += r.get("content", "") or ""
        elif r.get("type") == "assistant_output":
            entry["assistant_content"] += r.get("content", "") or ""
        entry["records"].append(r)

    # 无 round_id 的孤儿记录：每条自成一轮
    result = list(rounds_map.values())
    for r in orphans:
        content = r.get("content", "") or ""
        result.append(
            {
                "round_id": "",
                "user_content": content if r.get("type") == "user_input" else "",
                "assistant_content": content if r.get("type") == "assistant_output" else "",
                "records": [r],
                "timestamp": r.get("timestamp", 0),
            }
        )

    result.sort(key=lambda x: x.get("timestamp", 0))
    return result


def _preclean_rounds(records: list[dict]) -> tuple[list[dict], PrecleanStats]:
    """Level 1 预清洗（纯 Python 规则引擎，零 LLM 成本）。

    先按 round_id 组装轮次，再在轮次级别应用规则:
    1. 长度过滤: (user_input + assistant_output) < 100 字符 → 丢弃整轮
    2. 重复检测: 滑动窗口3轮，字符集重叠率 > 80% → 合并
    3. 代码块压缩: assistant_output 中的 ```...``` → [代码块 N行]
    4. 纯命令丢弃: user_input 仅为状态查看命令 → 丢弃整轮

    返回: (过滤后的单条消息列表, 统计信息)
    """
    # 先按 round_id 组装轮次
    rounds = _group_by_round(records)
    stats = PrecleanStats(total=len(rounds))
    filtered_rounds = []

    for rnd in rounds:
        user_c = rnd.get("user_content", "").strip()
        asst_c = rnd.get("assistant_content", "").strip()

        # 规则1：长度过滤（user_input + assistant_output < 36 → 丢弃）
        total_len = len(user_c) + len(asst_c)
        if total_len < 36:
            stats.filtered_out += 1
            preview = (user_c or asst_c)[:50]
            stats.details.append(f"短内容丢弃({total_len}字符): {preview}")
            continue

        # 规则4：纯命令丢弃（仅检查 user_input）
        if user_c and _CMD_PATTERNS.match(user_c):
            stats.filtered_out += 1
            stats.details.append(f"纯命令丢弃: {user_c[:50]}")
            continue

        # 规则3：代码块压缩（对 assistant_output 中的代码块）
        code_blocks = _CODE_BLOCK_RE.findall(asst_c)
        if code_blocks:
            for cb in code_blocks:
                lines = cb.strip("`").strip().split("\n")
                n = len([ln for ln in lines if ln.strip()])
                asst_c = _CODE_BLOCK_RE.sub(f"[代码块 {n}行]", asst_c, count=1)
            rnd["assistant_content"] = asst_c
            # 同步更新 records 中的内容
            for rec in rnd.get("records", []):
                if rec.get("type") == "assistant_output":
                    rec["content"] = asst_c

        filtered_rounds.append(rnd)

    # 规则2：重复检测（滑动窗口3轮，基于组合文本的字符集重叠率）
    if len(filtered_rounds) >= 3:
        merged = []
        i = 0
        while i < len(filtered_rounds):
            if i + 2 < len(filtered_rounds):
                a = filtered_rounds[i].get("user_content", "") + filtered_rounds[i].get("assistant_content", "")
                b = filtered_rounds[i + 1].get("user_content", "") + filtered_rounds[i + 1].get("assistant_content", "")
                c = filtered_rounds[i + 2].get("user_content", "") + filtered_rounds[i + 2].get("assistant_content", "")
                if _edit_distance_ratio(a, b) < 0.2 and _edit_distance_ratio(b, c) < 0.2:
                    # 合并3轮：保留第一轮的 records，标记合并
                    merged.append(filtered_rounds[i])
                    stats.merged += 1
                    stats.details.append("合并3轮相似对话")
                    i += 3
                    continue
            merged.append(filtered_rounds[i])
            i += 1
        filtered_rounds = merged

    # 展平为单条消息列表（供下游 format_conversation 使用）
    flattened = []
    for rnd in filtered_rounds:
        flattened.extend(rnd.get("records", []))

    return flattened, stats


def _edit_distance_ratio(a: str, b: str) -> float:
    """简化编辑距离比率（0=完全相同, 1=完全不同）"""
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    # 基于字符集重叠的快速估算
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    overlap = len(sa & sb)
    total = len(sa | sb)
    return 1.0 - (overlap / total) if total > 0 else 0.0


def _merge_rounds(rounds: list[dict]) -> dict:
    """合并多轮相似对话为1条摘要"""
    first = rounds[0]
    content = f"[{len(rounds)}轮调试] {first.get('content', '')[:200]}"
    return {"content": content, "type": "merged", "timestamp": first.get("timestamp", 0)}


def _select_strategy(filtered_count: int, llm_available: bool) -> DailyReviewStrategy:
    """根据过滤后消息条数和 LLM 可用性选择策略。

    filtered_count 是单条消息数（非轮数），每轮约 2 条（user_input + assistant_output）。

    策略选择依据：
    - DIRECT（≤300 条 ≈ 150 轮）：全量发送，由 LLM 直接生成完整日报
    - BATCH（≤500 条）：分批预摘要再合成，用于中型对话
    - HIERARCHICAL（>500 条）：分层摘要，用于超长对话
    """
    if not llm_available:
        return DailyReviewStrategy.PLAIN_LIST
    if filtered_count <= 300:
        return DailyReviewStrategy.DIRECT
    if filtered_count <= 500:
        return DailyReviewStrategy.BATCH
    return DailyReviewStrategy.HIERARCHICAL


def _query_conversations_by_date_range(
    mem, start_ts: float, end_ts: float, project_id: str | None = None
) -> list[dict]:
    """按时间戳范围查询对话记录（user_input + assistant_output），按时间升序排列"""
    where_clause: dict = {
        "$and": [
            {"type": {"$in": ["user_input", "assistant_output"]}},
            {"timestamp": {"$gte": start_ts}},
            {"timestamp": {"$lte": end_ts}},
        ]
    }
    if project_id:
        where_clause["$and"].append({"project_id": project_id})
    where_clause["$and"].append({"active": {"$ne": False}})

    raw = mem.store.get(where=where_clause, include=["documents", "metadatas"])
    ids_list = raw.get("ids") or []
    docs_list = raw.get("documents") or []
    metas_list = raw.get("metadatas") or []

    records = []
    for i in range(len(ids_list)):
        meta = metas_list[i] if i < len(metas_list) else {}
        records.append(
            {
                "id": ids_list[i],
                "content": docs_list[i] if i < len(docs_list) else "",
                "type": meta.get("type", ""),
                "timestamp": meta.get("timestamp", 0),
                "project_id": meta.get("project_id", ""),
                "round_id": meta.get("round_id", ""),
            }
        )

    records.sort(key=lambda r: r.get("timestamp", 0))
    return records


def _resolve_date(target_date: str | None = None) -> tuple[str, float, float]:
    """解析目标日期，返回 (日期字符串, 起始时间戳, 结束时间戳)"""
    date_str = target_date or datetime.now().strftime("%Y-%m-%d")
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start_of_day = datetime(dt.year, dt.month, dt.day, 0, 0, 0).timestamp()
    end_of_day = datetime(dt.year, dt.month, dt.day, 23, 59, 59).timestamp()
    return date_str, start_of_day, end_of_day


def generate_daily_report(
    mem,
    target_date: str | None = None,
    project_id: str | None = None,
    llm_endpoint: str | None = None,
    prompt_id: str | None = None,
    prompt_version: str | None = None,
    save_as_memory: bool = False,
) -> dict:
    """日报生成核心引擎——CLI 和 Dashboard API 共用。

    返回:
        {
            "report": str | None,       # Markdown 日报文本
            "date": str,                 # 日期
            "conversation_count": int,   # 对话轮数
            "llm_endpoint": str | None,  # 使用的 LLM 端点
            "prompt_id": str | None,     # 使用的提示词模板 ID
            "prompt_version": str | None,
            "saved_id": str | None,      # 保存为记忆的 ID
            "file_path": str | None,     # 写入文件路径（CLI 模式）
            "message": str,              # 状态消息
        }
    """
    # 1. 解析日期
    try:
        date_str, start_of_day, end_of_day = _resolve_date(target_date)
    except ValueError:
        return {
            "report": None,
            "date": target_date or "",
            "conversation_count": 0,
            "message": f"无效日期格式: {target_date}，请使用 YYYY-MM-DD",
        }

    # 2. 查询对话记录
    records = _query_conversations_by_date_range(mem, start_of_day, end_of_day, project_id=project_id)
    raw_count = len(records)
    if not records:
        return {
            "report": None,
            "date": date_str,
            "conversation_count": 0,
            "raw_rounds": 0,
            "filtered_rounds": 0,
            "message": f"{date_str} 暂无对话记录，日报未生成",
        }

    # 3. Level 1 预清洗 + 策略选择
    filtered, preclean_stats = _preclean_rounds(records)
    filtered_count = len(filtered)
    llm_available = bool(
        config.llm.active and any(ep.name == (llm_endpoint or config.llm.active) for ep in config.llm.endpoints)
    )
    strategy = _select_strategy(filtered_count, llm_available)

    # 4. 格式化对话文本（使用过滤后的轮次）
    conv_text = format_conversation(filtered)
    conv_text = f"Today's date: {date_str}\n\n{conv_text}"
    if not conv_text.strip():
        return {
            "report": None,
            "date": date_str,
            "conversation_count": raw_count,
            "raw_rounds": raw_count,
            "filtered_rounds": filtered_count,
            "message": "对话内容为空",
        }

    # 5. 降级路径：PLAIN_LIST
    if strategy == DailyReviewStrategy.PLAIN_LIST:
        timeline = _build_conversation_timeline(filtered)
        stats_text = _format_preclean_stats(preclean_stats)
        report = timeline + "\n\n" + stats_text if stats_text else timeline
        return {
            "report": report,
            "date": date_str,
            "conversation_count": raw_count,
            "raw_rounds": raw_count,
            "filtered_rounds": filtered_count,
            "strategy": strategy.value,
            "llm_calls": 0,
            "message": "LLM 未配置，已降级为对话清单",
            "fallback": True,
        }

    # 6. 解析 LLM 端点
    endpoint_name = llm_endpoint or config.llm.active
    target_ep = None
    for ep in config.llm.endpoints:
        if ep.name == endpoint_name:
            target_ep = ep
            break
    llm_api_url = f"{target_ep.api_base.rstrip('/')}/chat/completions"
    api_key = target_ep.api_key

    # 7. 获取 daily_review 提示词模板
    prompt_tpl = None
    if prompt_id:
        prompt_tpl = config.prompt.get(prompt_id)
    if not prompt_tpl:
        prompt_tpl = config.prompt.get_for_endpoint(endpoint_name, template_type="daily-review")
    if not prompt_tpl:
        timeline = _build_conversation_timeline(filtered)
        return {
            "report": timeline,
            "date": date_str,
            "conversation_count": raw_count,
            "raw_rounds": raw_count,
            "filtered_rounds": filtered_count,
            "strategy": strategy.value,
            "llm_calls": 0,
            "message": "未找到每日回顾提示词模板，已降级为对话清单",
        }

    prompt_tpl._sync_from_legacy()

    # 8. 按策略准备对话文本并构建 LLM 请求
    model_name = target_ep.model if target_ep else ""
    if strategy == DailyReviewStrategy.DIRECT:
        final_text = f"请根据以下对话记录生成今日开发日报。\n\n{conv_text}"
        llm_calls = 1
    elif strategy == DailyReviewStrategy.PRE_SUMMARIZE:
        final_text = _batch_summarize(filtered, llm_api_url, api_key, prompt_tpl, date_str, model_name)
        llm_calls = 2
    elif strategy == DailyReviewStrategy.HIERARCHICAL:
        final_text = _hierarchical_summarize(
            filtered,
            llm_api_url,
            api_key,
            prompt_tpl,
            date_str,
            model_name,
        )
        groups = max(1, (filtered_count + 19) // 20)
        l2_groups = max(1, (groups + 9) // 10)
        llm_calls = groups + (l2_groups if groups > 10 else 0) + 1
    else:  # BATCH
        chunks = _token_aware_chunk(filtered, max_tokens=config.memory.daily_review_chunk_tokens)
        final_text = _batch_partition_summarize(filtered, llm_api_url, api_key, prompt_tpl, date_str, model_name)
        llm_calls = max(2, len(chunks))

    payload = prompt_tpl.build_payload(
        final_text,
        version_override=prompt_version or None,
        model_name=target_ep.model if target_ep else None,
    )
    if "stop" not in payload:
        payload["stop"] = ["<|im_end|>"]

    logger.info(
        "今日回顾: date=%s endpoint=%s prompt=%s strategy=%s raw=%d filtered=%d",
        date_str,
        endpoint_name,
        prompt_tpl.id,
        strategy.value,
        raw_count,
        filtered_count,
    )

    # 9. 调用 LLM
    _dr_start = time.time()
    extractor = MemoryExtractor(llm_url=llm_api_url, api_key=api_key)
    resp = extractor._request_with_retry(payload)
    _dr_duration_ms = int((time.time() - _dr_start) * 1000)
    _input_chars = len(final_text)
    if resp is None:
        MemoryExtractor._log_usage("daily_review_failed", endpoint_name, 0, _input_chars, 0, _dr_duration_ms)
        return {
            "report": None,
            "date": date_str,
            "conversation_count": raw_count,
            "raw_rounds": raw_count,
            "filtered_rounds": filtered_count,
            "strategy": strategy.value,
            "llm_calls": llm_calls,
            "message": "LLM 无响应",
        }

    try:
        resp_json = resp.json()
    except Exception:
        MemoryExtractor._log_usage("daily_review_failed", endpoint_name, 0, _input_chars, 0, _dr_duration_ms)
        return {
            "report": None,
            "date": date_str,
            "conversation_count": raw_count,
            "raw_rounds": raw_count,
            "filtered_rounds": filtered_count,
            "strategy": strategy.value,
            "llm_calls": llm_calls,
            "message": f"LLM 响应格式异常（HTTP {resp.status_code}）",
        }

    raw_text = _extract_llm_content(resp_json)
    _output_chars = len(raw_text) if raw_text else 0
    if not raw_text:
        MemoryExtractor._log_usage("daily_review_failed", endpoint_name, 0, _input_chars, 0, _dr_duration_ms)
        return {
            "report": None,
            "date": date_str,
            "conversation_count": raw_count,
            "raw_rounds": raw_count,
            "filtered_rounds": filtered_count,
            "strategy": strategy.value,
            "llm_calls": llm_calls,
            "message": "LLM 未返回有效内容",
        }

    MemoryExtractor._log_usage("daily_review_success", endpoint_name, 1, _input_chars, _output_chars, _dr_duration_ms)

    cleaned = _strip_think_block(raw_text)
    report_text = cleaned or raw_text
    # 安全网：移除首个 H1 之前的所有内容（LLM 有时在日报正文前添加额外分析）
    report_text = _strip_before_first_heading(report_text)
    logger.info("今日回顾 生成成功 date=%s length=%d", date_str, len(report_text))

    # 10. 追加预清洗元信息
    stats_text = _format_preclean_stats(preclean_stats)
    if stats_text:
        report_text += stats_text

    # 11. 可选保存为记忆
    saved_id = None
    if save_as_memory:
        save_pid = project_id or _compute_default_project_id()
        meta = {
            "type": "daily_report",
            "project_id": save_pid,
            "project_name": Path.cwd().name,
            "source": "daily_review",
            "report_date": date_str,
        }
        saved_id = mem.remember(report_text, metadata=meta)
        logger.info("今日回顾 日报已保存为记忆 id=%s", saved_id)

    return {
        "report": report_text,
        "date": date_str,
        "conversation_count": raw_count,
        "raw_rounds": raw_count,
        "filtered_rounds": filtered_count,
        "strategy": strategy.value,
        "llm_calls": llm_calls,
        "llm_endpoint": endpoint_name,
        "prompt_id": prompt_tpl.id,
        "prompt_version": prompt_version or prompt_tpl.active_version,
        "saved_id": saved_id,
        "message": "日报生成成功",
    }


def _build_conversation_timeline(records: list[dict]) -> str:
    """降级模式：构建纯文本对话时间线"""
    lines = ["## 对话记录时间线", f"共 {len(records)} 条对话记录", ""]
    for r in records:
        ts = r.get("timestamp", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "--:--"
        content = r.get("content", "")[:200]
        rtype = "用户" if r.get("type") == "user_input" else "助手"
        lines.append(f"- [{time_str}] {rtype}: {content}")
    return "\n".join(lines)


def _format_preclean_stats(stats: PrecleanStats) -> str:
    """格式化预清洗统计信息，追加到日报末尾"""
    if stats.filtered_out == 0 and stats.merged == 0:
        return ""
    parts = ["\n\n---\n*预清洗统计*"]
    if stats.filtered_out:
        parts.append(f"- 过滤: {stats.filtered_out} 条")
    if stats.merged:
        parts.append(f"- 合并: {stats.merged} 组")
    for d in stats.details[:5]:  # 最多5条详情
        parts.append(f"  - {d}")
    return "\n".join(parts)


def _direct_format_conversation(rounds: list[dict], date_str: str | None = None) -> str:
    """DIRECT 模式的纯文本格式，不加包装指令。"""
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    conversation_block = format_conversation(rounds)
    return f"Today's date: {date_str}\n\n{conversation_block}"


def _strip_before_first_heading(text: str) -> str:
    """安全网：移除首个 H1（如 # YYYY-MM-DD 开发日报）之前的所有内容。"""
    for i, line in enumerate(text.split("\n")):
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2 and stripped[2] != "#":
            if i > 0:
                return "\n".join(text.split("\n")[i:])
            break
    return text


def _token_aware_chunk(rounds: list[dict], max_tokens: int = 12000) -> list[list[dict]]:
    """按 token 估算值将轮次分批，确保每批不超过 max_tokens。"""
    if not rounds:
        return []

    round_tokens = []
    for rnd in rounds:
        text = rnd.get("user_content", "") + rnd.get("assistant_content", "")
        tokens = int(len(text) / config.buffer.token_ratio) if text else 0
        round_tokens.append(max(tokens, 10))

    chunks = []
    current_chunk = []
    current_tokens = 0

    for rnd, tokens in zip(rounds, round_tokens):
        if tokens > max_tokens:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_tokens = 0
            chunks.append([rnd])
            continue
        if current_tokens + tokens > max_tokens and current_chunk:
            chunks.append(current_chunk)
            current_chunk = [rnd]
            current_tokens = tokens
        else:
            current_chunk.append(rnd)
            current_tokens += tokens

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _batch_summarize(
    rounds: list[dict],
    llm_api_url: str,
    api_key: str,
    prompt_tpl,
    date_str: str | None = None,
    model_name: str = "",
) -> str:
    """Level B: 批量预摘要 — 一次 LLM 调用将多轮对话压缩为结构化摘要。

    返回用于日报生成的摘要文本。
    """
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    conversation_block = format_conversation(rounds)
    conversation_block = f"Today's date: {date_str}\n\n{conversation_block}"
    summary_prompt = (
        "请对以下每轮对话，用1句话概括做了什么（动作+对象+结果），"
        "忽略寒暄和过程细节。按时间顺序输出摘要列表。\n\n"
        f"{conversation_block}\n\n"
        "请按轮次输出摘要（格式: [HH:MM] 动作描述）："
    )
    # 使用中性摘要系统提示词，避免 LLM 在预摘要阶段就生成完整日报
    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个开发记录分析助手。请根据对话记录提炼关键信息，按轮次输出简洁的摘要列表，不要生成完整日报。"
                ),
            },
            {"role": "user", "content": summary_prompt},
        ]
    }
    if model_name:
        payload["model"] = model_name
    payload["max_tokens"] = 2000
    extractor = MemoryExtractor(llm_url=llm_api_url, api_key=api_key)
    logger.info("批量预摘要: %d 条记录, model=%s", len(rounds), model_name or "default")
    resp = extractor._request_with_retry(payload)
    if resp is None:
        logger.warning("批量预摘要失败，降级为原文直出")
        return _direct_format_conversation(rounds, date_str)

    try:
        resp_json = resp.json()
        raw = _extract_llm_content(resp_json)
        logger.info("批量预摘要响应: %d 字符", len(raw or ""))
        if not raw:
            logger.warning("批量预摘要 LLM 返回空内容，降级为原文直出")
            return _direct_format_conversation(rounds, date_str)
        return _strip_think_block(raw) or raw
    except Exception:
        logger.warning("批量预摘要解析失败，降级为原文直出")
        return _direct_format_conversation(rounds, date_str)


def _batch_partition_summarize(
    rounds: list[dict],
    llm_api_url: str,
    api_key: str,
    prompt_tpl,
    date_str: str | None = None,
    model_name: str = "",
) -> str:
    """Level C: 分批预摘要 — 动态分片，子日报 → 合并为最终日报。

    返回合并后的文本用于最终日报生成。
    若所有批均降级为原文，退化为 DIRECT 策略，避免文本膨胀。
    """
    max_tokens_per_chunk = config.memory.daily_review_chunk_tokens
    chunks = _token_aware_chunk(rounds, max_tokens=max_tokens_per_chunk)
    sub_reports = []
    fallback_count = 0

    for idx, chunk in enumerate(chunks):
        logger.info("分批摘要: 第 %d/%d 批 (%d 轮)", idx + 1, len(chunks), len(chunk))
        sub_report = _batch_summarize(chunk, llm_api_url, api_key, prompt_tpl, date_str, model_name)
        # 检测是否降级: 以 "Today's date:" 开头表示原文直出
        if sub_report.startswith("Today's date:"):
            fallback_count += 1
        sub_reports.append(f"## 第 {idx + 1} 部分\n\n{sub_report}")

    # 所有批均降级 → 退化为 DIRECT，避免包装指令造成文本膨胀
    if fallback_count == len(chunks):
        logger.warning("所有 %d 批预摘要均降级，退回 DIRECT 策略", len(chunks))
        return _direct_format_conversation(rounds, date_str)

    merged = "\n\n".join(sub_reports)
    return f"以下为对话摘要（共 {len(chunks)} 批），请直接生成完整的开发日报：\n\n{merged}"


def _summarize_text_with_llm(
    text: str,
    llm_api_url: str,
    api_key: str,
    prompt_tpl,
    date_str: str | None = None,
    model_name: str = "",
) -> str:
    """通用 LLM 摘要调用 — 向 LLM 发送一段文本并返回摘要结果。"""
    # 使用中性摘要提示词，避免 L1/L2 阶段生成完整日报结构
    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个开发记录分析助手。请根据对话记录提炼关键信息，按轮次输出简洁的摘要列表，不要生成完整日报。"
                ),
            },
            {"role": "user", "content": text},
        ]
    }
    if model_name:
        payload["model"] = model_name
    payload.setdefault("max_tokens", 2048)
    extractor = MemoryExtractor(llm_url=llm_api_url, api_key=api_key)
    logger.info("通用摘要: %d 字符, model=%s", len(text), model_name or "default")
    resp = extractor._request_with_retry(payload)
    if resp is None:
        return ""
    try:
        resp_json = resp.json()
        raw = _extract_llm_content(resp_json)
        logger.info("通用摘要响应: %d 字符", len(raw or ""))
        if not raw:
            return ""
        return _strip_think_block(raw) or raw
    except Exception:
        return ""


def _hierarchical_summarize(
    rounds: list[dict],
    llm_api_url: str,
    api_key: str,
    prompt_tpl,
    date_str: str | None = None,
    model_name: str = "",
    layer_size: int = 20,
    summary_per_group: int = 5,
) -> str:
    """分层摘要 — 多轮压缩，适用于极长对话（200+ 轮）。

    L1: rounds → groups of layer_size → summarize each group to summary_per_group items
    L2 (if L1 groups > 10): L1 summaries → groups of 10 → summarize each to 3 items
    Final: all summaries → single merged text for final daily report generation
    """
    if not rounds:
        return ""
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")

    # L1: 分组预摘要
    groups = [rounds[i : i + layer_size] for i in range(0, len(rounds), layer_size)]
    l1_summaries: list[str] = []

    for idx, group in enumerate(groups):
        conv_block = format_conversation(group)
        prompt = (
            f"Today's date: {date_str}\n\n"
            "以下是一组开发对话记录，请提取每轮的核心内容（动作+对象+结果），"
            f"用 1-2 句话概括，输出 {summary_per_group} 条以内摘要。"
            "如无实质性开发内容（仅寒暄/命令），可跳过。\n\n"
            f"{conv_block}\n\n"
            f"请输出不超过 {summary_per_group} 条摘要（格式: - [HH:MM] 动作描述）："
        )
        logger.info("分层摘要 L1: 第 %d/%d 组 (%d 轮)", idx + 1, len(groups), len(group))
        result = _summarize_text_with_llm(prompt, llm_api_url, api_key, prompt_tpl, date_str, model_name)
        l1_summaries.append(result or f"[第 {idx + 1} 组摘要为空]")

    # L2: L1 组数 > 10 时二次压缩
    if len(l1_summaries) > 10:
        l2_groups = [l1_summaries[i : i + 10] for i in range(0, len(l1_summaries), 10)]
        l2_summaries: list[str] = []

        for idx, group in enumerate(l2_groups):
            combined = "\n".join(f"- {s}" if not s.startswith("- ") else s for s in group if s)
            prompt = (
                f"Today's date: {date_str}\n\n"
                "以下是对多组开发对话的摘要，请进一步提炼，保留所有关键技术决策和变更，"
                "输出 3 条以内综合摘要。\n\n"
                f"{combined}\n\n"
                "输出 3 条以内摘要（格式: - 动作描述）："
            )
            logger.info("分层摘要 L2: 第 %d/%d 组 (%d 组摘要)", idx + 1, len(l2_groups), len(group))
            result = _summarize_text_with_llm(prompt, llm_api_url, api_key, prompt_tpl, date_str, model_name)
            l2_summaries.append(result or f"[第 {idx + 1} 组综合摘要为空]")

        l1_summaries = l2_summaries

    merged = "\n".join(l1_summaries)
    return f"以下为分层摘要结果（共 {len(groups)} 组），请直接生成完整的开发日报：\n\n{merged}"


def write_daily_report(report_text: str, target_date: str, output_dir: Path | None = None) -> tuple[Path, bool]:
    """将日报写入文件。已存在时追加「补充」章节。

    返回: (文件路径, 是否追加)
    """
    if output_dir is None:
        output_dir = Path("document/日报")
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{target_date}-开发日报.md"

    is_append = False
    if file_path.exists():
        existing = file_path.read_text(encoding="utf-8")
        now_str = datetime.now().strftime("%H:%M")
        supplement = f"\n\n## 补充（{now_str} 更新）\n\n{report_text}"
        file_path.write_text(existing + supplement, encoding="utf-8")
        is_append = True
        logger.info("日报追加写入: %s", file_path)
    else:
        file_path.write_text(report_text, encoding="utf-8")
        logger.info("日报写入: %s", file_path)

    return file_path, is_append
