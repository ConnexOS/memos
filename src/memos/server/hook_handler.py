"""Hook HTTP Handler — POST /api/hooks/prompt 和 /api/hooks/stop

Unified 模式下，由 FastAPI 路由直接处理 Hook 事件。
替代原先的 stdin/stdout 管道模式。

Phase 2.3 (M7)：服务端 Hook 端点。
Phase 3.1 (M5) 实现前，creator_id 返回空字符串。
"""

from __future__ import annotations

import logging
import time as _time

from fastapi import APIRouter, Request

from ..server.mcp import _get_project_name, _project_id_ctx
from ..web.auth import _resolve_creator_id

logger = logging.getLogger(__name__)
router = APIRouter()


def _format_additional_context(suggestions: list) -> str:
    """将所有建议格式化为注入上下文文本

    各管道的 key 不同：
      - 管道一（知识检索）→ "document"
      - 管道三（手工建议）→ "content"
    """
    parts = []
    for s in suggestions:
        content = s.get("content") or s.get("document") or ""
        if content:
            parts.append(content)
    return "\n".join(parts)


@router.post("/prompt")
async def handle_hook_prompt(request: Request) -> dict:
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
        return {"additional_context": "", "suggestions": []}

    # 映射 prompt → legacy 键（下游管线兼容）
    body["prompt"] = user_input

    # 获取 ContextMemory 单例
    context_memory = request.app.state.context_memory

    # === C 管线：写入对话记录到 ChromaDB ===
    now = _time.time()
    project_id = _project_id_ctx.get()
    creator_id = _resolve_creator_id(from_ctx=True)

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

    # === 执行三管道 ===
    from ..hooks.prompt import _save_injected_records, run_pipeline_1, run_pipeline_2, run_pipeline_3

    suggestions_p1, injected_items = run_pipeline_1(body, context_memory, project_id=project_id)
    suggestions_p2 = run_pipeline_2(context_memory, project_id=project_id)
    suggestions_p3 = run_pipeline_3(body, context_memory, project_id=project_id)

    all_suggestions = suggestions_p1 + suggestions_p2 + suggestions_p3
    additional_context = _format_additional_context(all_suggestions)

    # 持久化注入记录供 Dashboard 展示（合并 pipe1 Layer1 + pipe3 手工建议）
    all_injected = list(injected_items) + list(suggestions_p3)
    if project_id:
        _save_injected_records(project_id, all_injected)

    logger.info(
        "hook_prompt: 完成 (p1=%d, p2=%d, p3=%d, injected=%d)",
        len(suggestions_p1),
        len(suggestions_p2),
        len(suggestions_p3),
        len(all_injected),
    )

    return {"additional_context": additional_context, "suggestions": all_suggestions}


@router.post("/stop")
async def handle_hook_stop(request: Request) -> dict:
    """处理 Stop Hook 事件（HTTP 模式）

    接收体格式：
      { "last_assistant_message": "...", "conversation_id": "...", "stop_hook_active": false }

    将助手响应写入 ChromaDB type=assistant_output，与 Prompt Hook 配对联。
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

    logger.info("hook_stop: 已保存助手输出 (%d 字符)", len(assistant_msg))
    return {"additional_context": ""}
