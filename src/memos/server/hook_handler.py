"""Hook HTTP Handler — POST /api/hooks/prompt 和 /api/hooks/stop

Unified 模式下，由 FastAPI 路由直接处理 Hook 事件。
替代原先的 stdin/stdout 管道模式。

Phase 2.3 (M7)：服务端 Hook 端点。
Phase 3.1 (M5) 实现前，creator_id 返回空字符串。
"""

from __future__ import annotations

import json
import logging
import re
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request

from ..server.mcp import _get_project_name, _project_id_ctx
from ..web.auth import _resolve_creator_id

logger = logging.getLogger(__name__)
router = APIRouter()

# Session 管理常量
SESSION_TTL = 1800  # 30 分钟

_TASK_EVAL_PATTERN = re.compile(
    r'\[TASK_EVAL\]\s*(\{.*?\})\s*\[/TASK_EVAL\]',
    re.DOTALL,
)


def _get_session_path(project_id: str) -> Path:
    """获取项目对应的 session 文件路径。"""
    from ..config.models import get_memos_home

    return get_memos_home() / "etc" / f".current_session_{project_id}"


def _get_cold_start_path(project_id: str) -> Path:
    """获取项目对应的冷启动标记文件路径。"""
    from ..config.models import get_memos_home

    return get_memos_home() / "etc" / f".cold_start_done_{project_id}"


def _get_or_create_session(project_id: str) -> dict:
    """读取或创建 session 状态文件。"""
    now = _time.time()
    session_file = _get_session_path(project_id)
    if session_file.exists():
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            if now - data.get("last_active_at", 0) < SESSION_TTL:
                data["last_active_at"] = now
                session_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
        except (json.JSONDecodeError, OSError):
            pass

    # 创建新 session
    try:
        from zoneinfo import ZoneInfo

        from ..config import get_local_timezone
        now_dt = datetime.now(ZoneInfo(get_local_timezone()))
    except Exception:
        from datetime import timezone as _tz
        now_dt = datetime.now(_tz.utc)
    session_id = now_dt.strftime("%Y%m%dT%H%M%S")
    data = {"session_id": session_id, "last_active_at": now}
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _build_task_eval_requirement(project_id: str) -> str:
    """构建 task 自评要求文本。根据冷启动状态选择变体。"""
    cold_start_file = _get_cold_start_path(project_id)
    is_cold_start = not cold_start_file.exists()

    if is_cold_start:
        return (
            "请在本轮回复的末尾附加任务描述，格式：\n"
            "[TASK_EVAL]\n"
            '{"project": "项目名", "goal": "目标描述", '
            '"done": [], "todo": ["首个待办项"], "blocked": []}\n'
            "[/TASK_EVAL]"
        )
    else:
        return (
            "请在本轮回复的末尾附加任务进度自评，格式：\n"
            "[TASK_EVAL]\n"
            '{"done": [...], "todo": [...], "blocked": [...]}\n'
            "[/TASK_EVAL]"
        )


def _extract_task_eval(text: str) -> dict | None:
    """从文本中提取 [TASK_EVAL] 块。"""
    match = _TASK_EVAL_PATTERN.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        logger.warning("TASK_EVAL 解析失败: %s", match.group(1)[:100])
        return None


def _build_behavior_guide() -> str:
    """构建 L3 行为引导文本（从 etc/behavior_guide.json 读取）。"""
    from ..config import load_behavior_guide as _load_bg

    return _load_bg()


def _inject_briefing(mem, pid: str) -> str:
    """检索最近5天内 delivered=false 的项目简报并构造注入文本。

    v0.7.1 改进：
      - 范围扩至最近5天，不再严格 match 今天
      - 取最新一条 delivered=false 的简报注入
      - 去除兜底生成路径（quality=simple 不注入）
    """
    if mem is None:
        return ""

    from zoneinfo import ZoneInfo

    from ..config import get_local_timezone
    now = datetime.now(ZoneInfo(get_local_timezone()))
    today = now.strftime("%Y-%m-%d")
    five_days_ago = (now - timedelta(days=5)).strftime("%Y-%m-%d")

    try:
        results = mem.list_memories(type_filter="briefing", limit=10)

        # 筛选最近5天内的简报，按日期降序排列
        candidates = []
        for item in results:
            meta = item.get("metadata", {})
            bd = meta.get("briefing_date", "")
            if five_days_ago <= bd <= today:
                candidates.append(item)

        if not candidates:
            logger.debug("最近5天无简报记录，跳过注入")
            return ""

        candidates.sort(key=lambda x: x["metadata"].get("briefing_date", ""), reverse=True)

        # 找最新一条 delivered=false 的简报
        target = None
        for item in candidates:
            if not item["metadata"].get("delivered", False):
                target = item
                break

        if target is None:
            logger.debug("最近5天简报均已投递，跳过注入")
            return ""

        meta = target["metadata"]
        briefing_id = target.get("id", "")
        briefing_date = meta.get("briefing_date", "")

        # 注入并标记 delivered=true
        try:
            mem.update_memory(briefing_id, new_metadata={"delivered": True})
        except Exception:
            logger.warning("简报 delivered 更新失败: id=%s", briefing_id[:8])

        if briefing_date == today:
            label = "[今日简报]"
        else:
            label = f"[简报 ({briefing_date})]"

        return f"{label}\n{target.get('document', '')[:500]}"

    except Exception as e:
        logger.debug("briefing 注入查询异常: %s", e)
        return ""


def _inject_active_task(mem, pid: str) -> str:
    """检索最近的 active task 并构造注入文本。

    选择策略：latest 1 条未归档 + 未暂停（archived=false, paused=false）。
    注入优先级：task > briefing（task 信息在 additionalContext 中最先出现）。
    """
    if mem is None:
        return ""

    try:
        results = mem.list_memories(
            project_id=pid,
            type_filter="task",
            where={"status": "active"},
            limit=1,
        )
        if not results:
            return ""

        task = results[0]
        meta = task.get("metadata", {})
        # F5: 使用 status 判断是否活跃
        if meta.get("status") != "active":
            logger.debug("task 注入跳过: 记录 status=%s (非 active)", meta.get("status"))
            return ""
        if meta.get("paused", False):
            return ""

        content_text = task.get("document", "")
        try:
            content = json.loads(content_text) if content_text else {}
        except (json.JSONDecodeError, TypeError):
            content = {}

        project_name = content.get("project", meta.get("project", ""))
        goal = content.get("goal", "")
        progress = content.get("progress", {})
        done = progress.get("done", [])
        todo = progress.get("todo", [])
        blocked = progress.get("blocked", [])

        lines = ["[当前任务]"]
        if project_name:
            lines.append(f"项目: {project_name}")
        if goal:
            lines.append(f"目标: {goal}")
        if done:
            lines.append(f"已完成: {'; '.join(done)}")
        if todo:
            lines.append(f"待完成: {'; '.join(todo)}")
        if blocked:
            lines.append(f"阻塞项: {'; '.join(blocked)}")

        return "\n".join(lines)

    except Exception as e:
        logger.warning("task 注入失败: %s", e)
        return ""


def _format_additional_context(suggestions: list, project_id: str = "", mem=None) -> str:
    """将所有建议格式化为注入上下文文本

    各管道的 key 不同：
      - 管道一（知识检索）→ "document"
      - 管道三（用户建议）→ "content"

    拼接顺序：F5 task 注入 → 管道结果 → F2 自评要求 → F1 行为引导。
    """
    parts = []

    # F5: 注入当前 active task（最高优先级）
    if mem and project_id:
        task_text = _inject_active_task(mem, project_id)
        if task_text:
            parts.append(task_text)
            parts.append("")

    # F6: 注入今日简报
    if mem and project_id:
        briefing_text = _inject_briefing(mem, project_id)
        if briefing_text:
            parts.append(briefing_text)
            parts.append("")

    # L2+P3: 分区输出 — 知识召回 + 用户建议
    knowledge_items = []
    suggestion_items = []
    for s in suggestions:
        meta = s.get("metadata", {}) or {}
        meta_type = meta.get("type", "unknown")
        content = s.get("content") or s.get("document") or ""
        if not content:
            continue
        if meta_type in ("solution", "decision", "lesson", "process"):
            knowledge_items.append((meta_type, content))
        else:
            suggestion_items.append(content)

    if knowledge_items:
        # 按类型优先级排序：solution > decision > process > lesson
        type_priority = {"solution": 0, "decision": 1, "process": 2, "lesson": 3}
        type_prefix = {
            "solution": "[解决方案]",
            "decision": "[决策]",
            "lesson": "[经验]",
            "process": "[流程]",
        }
        knowledge_items.sort(key=lambda x: type_priority.get(x[0], 99))
        parts.append("[相关知识]")
        for t, c in knowledge_items:
            parts.append(f"{type_prefix.get(t, '[知识]')} {c}")

    if suggestion_items:
        parts.append("[用户建议]")
        for c in suggestion_items:
            parts.append(c)

    # F2: 注入 task 自评要求
    if project_id:
        _get_or_create_session(project_id)
        task_eval_req = _build_task_eval_requirement(project_id)
        if task_eval_req:
            parts.append("")
            parts.append(task_eval_req)

    # F1: 追加行为引导（最低优先级）
    behavior_text = _build_behavior_guide()
    if behavior_text:
        parts.append("")
        parts.append(behavior_text)

    return "\n".join(parts)


_HOOK_START: float | None = None


@router.post("/prompt")
async def handle_hook_prompt(request: Request) -> dict:
    global _HOOK_START
    _HOOK_START = _time.time()
    """处理 UserPromptSubmit Hook 事件（HTTP 模式）

    接收体格式（与 Claude Code Hook JSON 一致）：
      { "conversation_id": "...", "user_input": "...", "assistant_output": "..." }

    返回体：
      { "additional_context": "...", "suggestions": [...] }
    """
    body = await request.json()

    # 兼容 Claude Code 原生格式（prompt）和旧版格式（user_input）
    user_input = body.get("user_input") or body.get("prompt") or ""
    assistant_output = body.get("assistant_output") or ""

    if not user_input:
        logger.warning("hook_prompt: 缺少 user_input/prompt")
        return {"additional_context": ""}

    # 映射 prompt → legacy 键（下游管线兼容）
    body["prompt"] = user_input

    # 获取 ContextMemory 单例
    context_memory = request.app.state.context_memory

    # === C 管线：写入对话记录到 ChromaDB ===
    now = _time.time()
    project_id = _project_id_ctx.get()
    creator_id = _resolve_creator_id(from_ctx=True)
    if creator_id == "unknown":
        creator_id = project_id or "unknown"

    project_name = _get_project_name(project_id) if project_id else ""

    context_memory.remember(
        user_input,
        metadata={
            "type": "user_input",
            "project_id": project_id or "",
            "project_name": project_name,
            "creator_id": creator_id,
            "scope": "personal",
            "conversation_id": body.get("conversation_id", ""),
            "timestamp": now,
        },
    )

    if assistant_output:
        context_memory.remember(
            assistant_output,
            metadata={
                "type": "assistant_output",
                "project_id": project_id or "",
                "project_name": project_name,
                "creator_id": creator_id,
                "scope": "personal",
                "conversation_id": body.get("conversation_id", ""),
                "timestamp": now,
            },
        )

    # === 执行知识注入 + 用户建议匹配 ===
    from ..hooks.prompt import _build_layered_context, _save_injected_records, run_manual_suggestion_matching

    injected_items = _build_layered_context(context_memory, user_input, pid=project_id or "")
    suggestions_p3 = run_manual_suggestion_matching(body, context_memory, project_id=project_id)

    all_suggestions = injected_items + suggestions_p3
    additional_context = _format_additional_context(all_suggestions, project_id=project_id or "", mem=context_memory)

    # 持久化注入记录供 Dashboard 展示
    all_injected = list(injected_items) + list(suggestions_p3)
    if project_id:
        _save_injected_records(project_id, all_injected)

    # F7 活动日志埋点（非阻塞）
    try:
        from ..features.activity_log import log_context_injection as _log_ci

        if injected_items:
            injected_types = list(set(
                r.get("metadata", {}).get("type", "") or "unknown" for r in injected_items
            ))
            meaningful_types = [t for t in injected_types if t not in ("", "unknown")]
            if meaningful_types:
                _log_ci(
                    memory_ids=[r.get("id", "") for r in injected_items[:10]],
                    types=meaningful_types,
                    injection_type="knowledge",
                    project_id=project_id,
                )

        if suggestions_p3:
            _log_ci(
                memory_ids=[s.get("source_memory_id", "") for s in suggestions_p3[:10]],
                types=["manual_suggestion"],
                injection_type="manual",
                project_id=project_id,
            )
    except Exception:
        logger.debug("活动日志埋点(injection)失败", exc_info=True)

    logger.info(
        "hook_prompt: 完成 (injected=%d, p3=%d, total=%d)",
        len(injected_items),
        len(suggestions_p3),
        len(all_injected),
    )

    # F11: 记录 Hook 延迟到活动日志
    if _HOOK_START is not None:
        try:
            from ..features.activity_log import _append_event as _ae

            latency_ms = round((_time.time() - _HOOK_START) * 1000, 2)
            _ae({
                "event": "hook_latency",
                "hook_type": "prompt",
                "latency_ms": latency_ms,
                "summary": f"Prompt Hook 处理耗时 {latency_ms:.0f}ms",
                "timestamp": _time.time(),
            })
        except Exception:
            logger.debug("Prompt Hook 延迟记录失败", exc_info=True)

    return {"additional_context": additional_context}


@router.post("/stop")
async def handle_hook_stop(request: Request) -> dict:
    global _HOOK_START
    _HOOK_START = _time.time()
    """处理 Stop Hook 事件（HTTP 模式）

    接收体格式：
      { "last_assistant_message": "...", "conversation_id": "...", "stop_hook_active": false }

    将助手响应写入 ChromaDB type=assistant_output，与 Prompt Hook 配对联。
    同时从回复中提取 [TASK_EVAL] 块并转发至 task 异步处理队列。
    """
    body = await request.json()

    # stop_hook_active 防无限循环
    if body.get("stop_hook_active"):
        logger.debug("hook_stop: stop_hook_active=true，跳过")
        return {"additional_context": ""}

    assistant_msg = (body.get("last_assistant_message") or "").strip()
    if not assistant_msg:
        logger.debug("hook_stop: last_assistant_message 为空，跳过")
        return {"additional_context": ""}

    context_memory = request.app.state.context_memory
    now = _time.time()
    project_id = _project_id_ctx.get()
    creator_id = _resolve_creator_id(from_ctx=True)
    if creator_id == "unknown":
        creator_id = project_id or "unknown"

    project_name = _get_project_name(project_id) if project_id else ""

    context_memory.remember(
        assistant_msg,
        metadata={
            "type": "assistant_output",
            "project_id": project_id or "",
            "project_name": project_name,
            "creator_id": creator_id,
            "scope": "personal",
            "conversation_id": body.get("conversation_id", ""),
            "timestamp": now,
        },
    )

    # F2: 提取 TASK_EVAL 并转发至 task 异步处理队列
    task_eval = _extract_task_eval(assistant_msg)
    if task_eval:
        session_data = {}
        if project_id:
            try:
                session_file = _get_session_path(project_id)
                if session_file.exists():
                    session_data = json.loads(session_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        session_id = session_data.get("session_id", "")

        # 通过 app.state 访问 task 队列（在 lifespan 阶段注入）
        task_queue = getattr(request.app.state, "task_queue", None)
        if task_queue is not None:
            try:
                task_queue.enqueue(task_eval, session_id, project_id or "")
                logger.info("TASK_EVAL 已入队: session=%s", session_id)
            except Exception as e:
                logger.warning("TASK_EVAL 入队失败: %s", e)
        else:
            logger.warning("task_queue 不可用，TASK_EVAL 已丢弃")
    else:
        logger.debug("未找到 TASK_EVAL 块，静默跳过")

    logger.info("hook_stop: 已保存助手输出 (%d 字符)", len(assistant_msg))

    # F1: AI 引用回检（<5ms，非阻塞）
    try:
        from ..hooks.stop import _check_ai_reference
        _check_ai_reference(assistant_msg, project_id or "")
    except Exception:
        logger.debug("Stop Hook 引用回检异常", exc_info=True)

    # F11: 记录 Stop Hook 延迟到活动日志
    if _HOOK_START is not None:
        try:
            from ..features.activity_log import _append_event as _ae

            latency_ms = round((_time.time() - _HOOK_START) * 1000, 2)
            _ae({
                "event": "hook_latency",
                "hook_type": "stop",
                "latency_ms": latency_ms,
                "summary": f"Stop Hook 处理耗时 {latency_ms:.0f}ms",
                "timestamp": _time.time(),
            })
        except Exception:
            logger.debug("Stop Hook 延迟记录失败", exc_info=True)

    return {"additional_context": ""}
