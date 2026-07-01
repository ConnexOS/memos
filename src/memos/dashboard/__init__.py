"""向后兼容 stub —— v0.4.3 Phase 10：dashboard 模块已移至 memos.web。

v0.6.0: 同时负责启动 SchedulerThread 等后台服务。
"""

import json
import logging
import time

from memos.engine.memory import ContextMemory  # noqa: F401  # 测试 mock 引用
from memos.web.app import app, main  # noqa: F401

logger = logging.getLogger(__name__)

_scheduler = None
_memory_instance = None


def init_scheduler(memory_instance=None):
    """初始化并启动 SchedulerThread（在 Dashboard 进程启动时调用）。"""
    global _scheduler, _memory_instance
    _memory_instance = memory_instance

    if _scheduler is not None:
        return

    from ..features.scheduler import SchedulerThread

    _scheduler = SchedulerThread(
        briefing_generator=_generate_full_briefing,
        memory_instance=memory_instance,
    )
    _scheduler.start()
    logger.info("Dashboard 调度器已启动")

    # F11: 记录性能基线（仅 Dashboard 进程启动时写入）
    try:
        from ..features.performance import record_baseline

        record_baseline(
            {
                "event": "dashboard_startup",
                "version": __import__("memos").__version__,
            }
        )
    except Exception as e:
        logger.warning("性能基线记录失败: %s", e)


def _has_substance(records: list, sessions: list) -> bool:
    """检测对话是否包含实质性内容（双维度质量门禁的第二维度）。

    判定标准（任一满足即认为有实质内容）：
    1. 会话数 >= 2（进行了多轮深入讨论）
    2. 至少一个会话的轮次 >= 5（有实质性对话）
    3. 对话内容包含代码变更或决策类关键词

    注意：关键词匹配是全文中英文混合的简单信号检测。无法
    覆盖非标准表述（如"帮我改一下那个文件"）。已知局限，
    未来可升级为 LLM 判断。
    """
    if len(sessions) >= 2:
        return True
    for s in sessions:
        if s.get("rounds", 0) >= 5:
            return True

    keywords = [
        "提交",
        "修复",
        "修改",
        "新增",
        "实现",
        "重构",
        "删除",
        "配置",
        "commit",
        "fix",
        "add",
        "implement",
        "refactor",
        "merge",
        "test",
    ]
    for r in records:
        doc = r.get("document", "")
        for kw in keywords:
            if kw in doc:
                return True
    return False


def _generate_full_briefing(date: str = None, llm_endpoint: str = None, prompt_id: str = None, memory_instance=None):
    """完整简报生成函数（供 SchedulerThread + 手动触发调用）。

    date: YYYY-MM-DD，指定生成哪天的简报。不传或传今日则用 [今日00:00, now] 范围。
    传指定日期则用 [date 00:00, date+1 00:00] 范围。

    v0.7.0 F8: 数据源从活动日志切换为 ChromaDB 对话记录。
    v0.7.1: 增加 llm_endpoint/prompt_id 参数 + 全流程调试日志。
    v0.7.1 bugfix: 增加 memory_instance 参数，不依赖模块全局 _memory_instance。
    v0.7.1 F10: Git 数据采集 + active task + 双维度质量门禁。
    """
    from datetime import datetime

    from ..config import config
    from ..features.briefing import (
        _get_active_task,
        _get_conversation_records,
        _get_today_knowledge,
        _group_sessions,
        build_fallback_briefing,
        build_full_briefing,
    )

    global _memory_instance
    mem = memory_instance or _memory_instance
    if mem is None:
        logger.warning("_generate_full_briefing: memory 实例不可用（所有来源均空）")
        return

    from zoneinfo import ZoneInfo

    from ..config import get_local_timezone

    tz_str = get_local_timezone()
    now = datetime.now(ZoneInfo(tz_str))
    today_str = now.strftime("%Y-%m-%d")

    # 计算数据时间范围
    if date and date != today_str:
        # 指定日期 → [date 00:00, date+1 00:00]
        dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=ZoneInfo(tz_str))
        data_start = dt.timestamp()
        data_end = data_start + 86400
        briefing_date = date
        data_date_label = date
        logger.info("[简报] 按指定日期生成: %s (范围 00:00 ~ 24:00)", date)
    else:
        # 默认/今日 → [今日 00:00, now]
        today_dt = datetime(now.year, now.month, now.day, tzinfo=ZoneInfo(tz_str))
        data_start = today_dt.timestamp()
        data_end = now.timestamp()
        briefing_date = today_str
        data_date_label = today_str

    # 获取当前 project_id（简报上下文所属项目）
    _pid = None
    try:
        from ..server.mcp import _project_id_ctx as _pid_ctx

        _pid = _pid_ctx.get()
        logger.info("[简报] 当前 project_id: %s", _pid)
    except Exception:
        pass

    # ── 数据采集（v0.7.1 F10 增强；支持按日期/时间段过滤）──

    # 1. Task 数据（active 优先）
    task_data = _get_active_task(mem)

    # 2. 对话记录（按项目+时间范围过滤）
    records = []
    sessions = []
    try:
        records = _get_conversation_records(mem, tz_str, project_id=_pid, start_ts=data_start, end_ts=data_end)
        sessions = _group_sessions(records)
        logger.info("[简报] 会话数据: %d 条记录, %d 个会话", len(records), len(sessions))
    except Exception as e:
        logger.warning("获取会话数据失败: %s", e)

    # 3. 知识（仅 lesson+process，按项目+时间范围过滤）
    new_knowledge = []
    try:
        new_knowledge = _get_today_knowledge(mem, tz_str, project_id=_pid, start_ts=data_start, end_ts=data_end)
        logger.info("[简报] 新增知识: %d 条 (lesson+process)", len(new_knowledge) if new_knowledge else 0)
    except Exception as e:
        logger.warning("获取知识写入失败: %s", e)

    # 4. Git 数据（新增 v0.7.1 F10）
    git_log_text = ""
    git_diff_text = ""
    try:
        from memos.features.git_collector import get_git_diff, get_git_log

        git_log_text = get_git_log(briefing_date)
        git_diff_text = get_git_diff()
        logger.info("[简报] Git 数据: log=%d chars, diff=%d chars", len(git_log_text), len(git_diff_text))
    except Exception as e:
        logger.warning("获取 Git 数据失败: %s", e)

    def _llm_call(system_prompt: str, user_prompt: str) -> str | None:
        import requests

        llm_url = f"{config.llm.api_base.rstrip('/')}/chat/completions"
        payload = {
            "model": config.llm.active_endpoint.model or "default",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        try:
            headers = {}
            if config.llm.api_key:
                headers["Authorization"] = f"Bearer {config.llm.api_key}"
            resp = requests.post(llm_url, json=payload, headers=headers, timeout=300)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error("LLM 调用失败: %s", e)
            return None

    logger.info(
        "[简报] === 开始生成简报 (date=%s, llm_endpoint=%s, prompt_id=%s) ===", briefing_date, llm_endpoint, prompt_id
    )
    logger.info("[简报] 任务数据: %s", json.dumps(task_data, ensure_ascii=False)[:500])

    # 质量门禁：双维度判定
    total_rounds = len(records)
    has_content = _has_substance(records, sessions)

    if total_rounds < 5 or not has_content:
        logger.info("[简报] 质量门禁触发: rounds=%d, has_substance=%s", total_rounds, has_content)
        briefing = build_fallback_briefing(
            memory_instance=mem, tz_str=tz_str, project_id=_pid, start_ts=data_start, end_ts=data_end
        )
        briefing["source"] = "auto_extracted"
    else:
        logger.info("[简报] 调用 LLM 生成完整简报 (llm_endpoint=%s, prompt_id=%s)...", llm_endpoint, prompt_id)
        briefing = build_full_briefing(
            task_data,
            sessions,
            new_knowledge,
            git_log_text,
            git_diff_text,  # 新增参数
            _llm_call,
            llm_endpoint=llm_endpoint,
            prompt_id=prompt_id,
            date_str=data_date_label,
        )
        if briefing is None:
            logger.warning("[简报] LLM 简报生成失败，降级为兜底模板")
            briefing = build_fallback_briefing(
                memory_instance=mem, tz_str=tz_str, project_id=_pid, start_ts=data_start, end_ts=data_end
            )
            briefing["source"] = "auto_extracted"
        else:
            briefing["source"] = "auto_extracted"

    # F9: 计数注入 — task_done/task_todo/new_knowledge/session
    task_progress = task_data.get("progress", {}) if task_data else {}
    briefing["task_done_count"] = len(task_progress.get("done", []))
    briefing["task_todo_count"] = len(task_progress.get("todo", []))
    briefing["new_knowledge_count"] = len(new_knowledge) if new_knowledge else 0
    briefing["session_count"] = len(sessions) if sessions else 0

    logger.info(
        "[简报] 生成结果: quality=%s, source=%s, summary=%s",
        briefing.get("quality", "?"),
        briefing.get("source", "?"),
        json.dumps(briefing.get("summary", ""), ensure_ascii=False)[:200],
    )

    briefing_text = json.dumps(briefing, ensure_ascii=False)
    briefing_meta = {
        "type": "briefing",
        "briefing_date": briefing_date,
        "source": briefing.get("source", "auto_extracted"),
        "quality": briefing.get("quality", "full"),
        "generated_at": time.time(),
        "delivered": False,
        "task_done_count": briefing["task_done_count"],
        "task_todo_count": briefing["task_todo_count"],
        "new_knowledge_count": briefing["new_knowledge_count"],
        "session_count": briefing["session_count"],
    }
    if _pid:
        briefing_meta["project_id"] = _pid
    try:
        mem.remember(briefing_text, metadata=briefing_meta)
        logger.info("简报已写入 (%s, quality=%s)", briefing_date, briefing.get("quality", "unknown"))
    except Exception as e:
        logger.error("简报写入失败: %s", e)
