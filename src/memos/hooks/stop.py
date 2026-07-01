"""Stop Hook —— 采集助手响应到 ChromaDB，与 Prompt Hook 联动。

由 Claude Code settings.json 配置调用：
  python -m memos.hooks.stop
"""

import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# 配置日志
LOG_FILE = Path.home() / ".memos" / "hook_stop.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("memos.hooks.stop")
logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8", mode="a")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] stop: %(message)s"))
logger.addHandler(_fh)
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
_stderr_handler.setFormatter(logging.Formatter("[memos.stop] %(levelname)s: %(message)s"))
logger.addHandler(_stderr_handler)

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
STATE_FILE = PROJECT_DIR / ".claude" / "conv_state.json"

# F2: session 文件（在 stop.py 中独立声明，与 prompt.py 各自进程独立）
SESSION_FILE = PROJECT_DIR / "etc" / ".current_session"

_TASK_EVAL_PATTERN = re.compile(
    r"\[TASK_EVAL\]\s*(\{.*?\})\s*\[/TASK_EVAL\]",
    re.DOTALL,
)

_mem = None
_pid_cache = None


def _get_project_id() -> str:
    global _pid_cache
    if _pid_cache is None:
        _pid_cache = hashlib.md5(str(PROJECT_DIR).encode()).hexdigest()[:8]
    return _pid_cache


def _extract_task_eval(text: str) -> dict | None:
    """从文本中提取 [TASK_EVAL] 块。

    返回解析后的 dict，或 None（未找到或解析失败）。
    """
    match = _TASK_EVAL_PATTERN.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        logger.warning("TASK_EVAL 解析失败: %s", match.group(1)[:100])
        return None


def _get_memory():
    global _mem
    if _mem is None:
        try:
            from memos.engine.memory import ContextMemory

            _mem = ContextMemory()
            logger.debug("ContextMemory 初始化成功")
        except Exception as e:
            logger.error("初始化 ContextMemory 失败: %s", e)
            import traceback

            logger.error(traceback.format_exc())
    return _mem


def _check_ai_reference(assistant_msg: str, pid: str):
    """F1: AI 引用回检 — 检测助手回复是否引用了注入内容。

    读取 .injected_records_{pid}.json，对每条记录的前 100 字符做子串匹配。
    匹配成功时记录 ai_reference 事件，无匹配时静默跳过。
    整个回检过程 < 5ms，不阻塞 Stop Hook 主流程。
    """
    if not assistant_msg or not pid:
        return
    try:
        from ..config.models import get_memos_home

        path = get_memos_home() / "etc" / f".injected_records_{pid}.json"
        if not path.exists():
            return

        data = json.loads(path.read_text(encoding="utf-8"))
        records = data.get("records", [])
        if not records:
            return

        from ..features.activity_log import log_ai_reference

        msg_lower = assistant_msg.lower()
        for r in records:
            snippet = (r.get("content") or "")[:100]
            if not snippet:
                continue
            matched = snippet.lower() in msg_lower
            if matched:
                log_ai_reference(
                    memory_id=r.get("id", ""),
                    content_snippet=snippet,
                    injected_at=r.get("timestamp", 0),
                    matched=True,
                    project_id=pid,
                )
                logger.info(
                    "AI 引用检测: id=%s, snippet=%s...",
                    r.get("id", "")[:8],
                    snippet[:40],
                )
    except Exception:
        # 静默降级：回检失败不影响 Stop Hook 主流程
        logger.warning("AI 引用回检异常", exc_info=True)


def main():
    # 1. 解析 stdin（强制 UTF-8 解码避免 Windows GBK 编码问题）
    try:
        raw_bytes = sys.stdin.buffer.read()
        input_data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as e:
        logger.warning("读取 stdin 失败: %s", e)
        return

    # 2. 安全防护：stop_hook_active 检查
    if input_data.get("stop_hook_active"):
        logger.debug("stop_hook_active=true，跳过防止无限循环")
        return

    # 3. 获取助手响应文本
    assistant_msg = (input_data.get("last_assistant_message") or "").strip()
    if not assistant_msg:
        logger.debug("last_assistant_message 为空，保存占位文本")
        assistant_msg = "[助手无文本输出]"
    else:
        logger.info("收到助手响应 (%d 字符): %s", len(assistant_msg), assistant_msg[:500])

    # F2: 提取 TASK_EVAL 并转发至 server（非阻塞，失败不影响 L1 采集）
    task_eval = _extract_task_eval(assistant_msg)
    if task_eval:
        session_id = ""
        if SESSION_FILE.exists():
            try:
                session_data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                session_id = session_data.get("session_id", "")
            except (json.JSONDecodeError, OSError):
                pass

        import urllib.error
        import urllib.request

        server_url = input_data.get("memos_server_url", "http://127.0.0.1:8000")
        payload = json.dumps(
            {
                "task_eval": task_eval,
                "session_id": session_id,
                "project_id": _get_project_id(),
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{server_url}/api/task/eval",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            logger.info("TASK_EVAL 已转发至 server，session=%s", session_id)
        except (urllib.error.URLError, OSError) as e:
            logger.warning("转发 TASK_EVAL 失败（server 可能未启动）: %s", e)
    else:
        logger.debug("未找到 TASK_EVAL 块，静默跳过")

    # F1: AI 引用回检（<5ms，非阻塞）
    pid = _get_project_id()
    _check_ai_reference(assistant_msg, pid)
    if not STATE_FILE.exists():
        logger.debug("conv_state.json 不存在，跳过（可能 Prompt Hook 未执行）")
        return

    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("读取 conv_state.json 失败: %s", e)
        return

    # 5. 幂等检查：pending_assistant 为 false 说明已处理
    if not state.get("pending_assistant"):
        logger.debug("pending_assistant=false，跳过（已处理）")
        return

    # 6. 写入 ChromaDB
    round_id = state.get("round_id", "")
    user_record_id = state.get("user_record_id", "")
    pid = _get_project_id()
    mem = _get_memory()

    if mem:
        try:
            mem.remember(
                assistant_msg,
                metadata={
                    "type": "assistant_output",
                    "project_id": pid,
                    "project_name": PROJECT_DIR.name,
                    "round_id": round_id,
                    "user_record_id": user_record_id,
                    "timestamp": time.time(),
                },
            )
            logger.info("已保存助手输出 round=%s", round_id)
        except Exception as e:
            logger.error("保存助手输出失败: %s", e)
            import traceback

            logger.error(traceback.format_exc())
    else:
        logger.error("ContextMemory 不可用，无法保存")

    # 7. 更新状态标记已处理（portalocker 排他锁防并发）
    import portalocker

    state["pending_assistant"] = False
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        portalocker.lock(f, portalocker.LOCK_EX)
        f.write(json.dumps(state, ensure_ascii=False))
        portalocker.unlock(f)
    logger.debug("状态文件已更新 pending_assistant=false round=%s", round_id)


if __name__ == "__main__":
    main()
