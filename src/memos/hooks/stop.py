"""Stop Hook —— 采集助手响应到 ChromaDB，与 Prompt Hook 联动。

由 Claude Code settings.json 配置调用：
  python -m memos.hooks.stop
"""

import hashlib
import json
import logging
import os
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

_mem = None
_pid_cache = None


def _get_project_id() -> str:
    global _pid_cache
    if _pid_cache is None:
        _pid_cache = hashlib.md5(str(PROJECT_DIR).encode()).hexdigest()[:8]
    return _pid_cache


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

    # 4. 读取状态文件
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

    # 7. 更新状态标记已处理
    state["pending_assistant"] = False
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    logger.debug("状态文件已更新 pending_assistant=false round=%s", round_id)


if __name__ == "__main__":
    main()
