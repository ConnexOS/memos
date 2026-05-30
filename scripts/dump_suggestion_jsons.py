"""打印建议/注入管道的所有提示词 JSON 格式消息，供校验。"""

import json
import time
import sys
import os

# 直接写文件，避免编码问题
OUT = os.path.join(os.path.dirname(__file__), "..", "etc", "suggestion_jsons_dump.jsonl")
f = open(OUT, "w", encoding="utf-8")

def log(section: str, data):
    f.write(f"=== {section} ===\n")
    f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n\n")

_NOW = 1718000000.0

# ============================================================
# 1. Layer 1 —— additionalContext 输出 JSON
# ============================================================
additional_context = (
    "--- 相关记忆（自动检索，仅供参考） ---\n"
    "[历史参考] 2025-06-01 | [fact] | 相似度 85%\n"
    "用户偏好使用 Pydantic v2 的 model_validate 而不是 construct...\n"
    "---\n"
    "[历史参考] 2025-05-28 | [decision] | 相似度 72%\n"
    "决定采用 FastMCP 作为 MCP 框架...\n"
    "---"
)

output = {
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": additional_context,
    }
}
log("Layer 1 additionalContext 输出 JSON (stdout)", output)

# ============================================================
# 2. Layer 2 —— suggestion 写入 ChromaDB 的 metadata JSON
# ============================================================
log("Layer 2 - 管道一 active_push suggestion metadata", {
    "type": "suggestion",
    "project_id": "abc12345",
    "source_memory_id": "mem_xxxxxxxx",
    "similarity": 0.85,
    "query": "当前项目版本是多少？",
    "suggestion_type": "active_push",
    "status": "pending",
    "timestamp": _NOW,
    "source_date": "2025-06-01T12:00:00",
    "source_type": "fact",
    "expires_at": _NOW + 7 * 86400,
})

log("Layer 2 - 管道二 system_alert suggestion metadata", {
    "type": "suggestion",
    "project_id": "abc12345",
    "suggestion_type": "system_alert",
    "event_type": "unrefined_rounds",
    "priority": "medium",
    "status": "pending",
    "timestamp": _NOW,
    "expires_at": _NOW + 7 * 86400,
    "document": "【系统提醒】unrefined_rounds — 提炼率 15% (3/20) 低于 30%",
})

log("Layer 2 - 管道三 manual_trigger suggestion metadata", {
    "type": "suggestion",
    "project_id": "abc12345",
    "suggestion_type": "manual_trigger",
    "source_memory_id": "manual_001",
    "similarity": 0.95,
    "status": "pending",
    "timestamp": _NOW,
    "expires_at": _NOW + 7 * 86400,
    "trigger_keywords": '["版本", "版本号"]',
    "trigger_mode": "keyword",
    "hit_count": 3,
    "priority": "high",
    "created_by": "user",
    "document": "当前项目版本是 v0.4.4",
})

# ============================================================
# 3. LLM 提炼请求体（build_payload 结果）
# ============================================================
log("LLM 提炼 OpenAI 格式 payload", {
    "messages": [
        {
            "role": "system",
            "content": (
                "You are a senior technical analyst. Your task is to extract "
                "technical implementation knowledge from the conversation as "
                'structured "experience cards". For each significant change, fix, '
                "or decision, extract:\n\n"
                '- "problem": a concise description of the issue or context...\n'
                '- "solution": what exactly was done...\n'
                '- "type": one of fact/decision/preference/todo...'
            ),
        },
        {
            "role": "user",
            "content": (
                "Conversation:\n\n"
                "User: 当前项目版本是多少？\n"
                "Assistant: 当前项目版本是 v0.4.4"
            ),
        },
    ],
    "temperature": 0.1,
    "max_tokens": 1024,
    "stop": ["<|im_end|>"],
    "model": "deepseek-chat",
})

log("LLM 提炼 ChatML 格式 payload", {
    "messages": [
        {
            "role": "user",
            "content": (
                "<|im_start|>system\n"
                "You are a senior technical analyst...\n"
                "<|im_end|>\n"
                "<|im_start|>user\n"
                "Conversation:\n\n"
                "User: 当前项目版本是多少？\n"
                "Assistant: 当前项目版本是 v0.4.4\n"
                "<|im_end|>\n"
                "<|im_start|>assistant\n"
            ),
        }
    ],
    "stop": ["<|im_end|>"],
    "reasoning_format": "none",
    "reasoning_in_content": False,
    "temperature": 0.1,
    "model": "deepseek-chat",
})

# ============================================================
# 4. _format_context_item 纯文本格式
# ============================================================
f.write("=== _format_context_item 输出纯文本格式 ===\n")
f.write("--- 相关记忆（自动检索，仅供参考） ---\n")
f.write("[历史参考] 2025-06-01 | [fact] | 相似度 85%\n")
f.write("用户偏好使用 Pydantic v2 的 model_validate 而不是 construct…\n")
f.write("---\n")
f.write("[历史参考] 2025-05-28 | [decision] | 相似度 72%\n")
f.write("决定采用 FastMCP 作为 MCP 框架…\n")
f.write("---\n\n")

# ============================================================
# 5. 管道三上下文注入纯文本格式
# ============================================================
f.write("=== 管道三 手工建议注入上下文纯文本格式 ===\n")
f.write("--- 相关记忆（自动检索，仅供参考） ---\n")
f.write("[历史参考] 2025-06-01 | [fact] | 相似度 85%\n")
f.write("内容…\n")
f.write("---\n\n")
f.write("--- 以下是根据当前对话触发的手工建议 ---\n")
f.write("  [触发关键词: 版本, 版本号] 当前项目版本是 v0.4.4\n")
f.write("---\n\n")

f.close()
print(f"✅ 已写出到 {OUT}", file=sys.stderr)
