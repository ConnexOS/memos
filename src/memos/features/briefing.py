"""简报生成 —— 基于 ChromaDB 语义对话数据的主路径（MEMOS LLM）+ 兜底路径（纯模板拼接）。

v0.7.0 F8: 数据源从活动日志切换为 ChromaDB 对话记录，增加质量门禁、会话分组。
"""

import json
import logging
import time
from datetime import datetime
from typing import Callable

logger = logging.getLogger(__name__)

# 会话分组间隔（秒）
_SESSION_INTERVAL = 1800  # 30 分钟


# ─── 辅助函数：从 ChromaDB 获取今日数据 ───


def _get_tz(tz_str: str = "Asia/Shanghai"):
    """获取 zoneinfo 时区对象。"""
    from zoneinfo import ZoneInfo

    return ZoneInfo(tz_str)


def _today_timestamp_range(tz_str: str = "Asia/Shanghai") -> tuple[float, float]:
    """获取今日 0 点和明日 0 点的时间戳（配置时区）。"""
    tz = _get_tz(tz_str)
    now = datetime.now(tz)
    today_start = datetime(now.year, now.month, now.day, tzinfo=tz).timestamp()
    today_end = today_start + 86400
    return today_start, today_end


def _get_conversation_records(
    memory_instance, tz_str: str = "Asia/Shanghai", project_id: str = None, start_ts: float = None, end_ts: float = None
) -> list[dict]:
    """从 ChromaDB 查询 user_input 和 assistant_output 记录。

    返回按时间戳升序排列的记录列表，每条含 id/document/metadata。

    start_ts/end_ts 可选：指定时间范围。不传则使用今日范围。
    """
    if start_ts is None or end_ts is None:
        ts_start, ts_end = _today_timestamp_range(tz_str)
    else:
        ts_start, ts_end = start_ts, end_ts
    try:
        results = memory_instance.list_memories(
            type_filter=["user_input", "assistant_output"],
            project_id=project_id,
            where={"timestamp": {"$gte": ts_start}},
            limit=500,
        )
    except Exception as e:
        logger.warning("查询对话记录失败: %s", e)
        return []

    # 过滤时间范围（list_memories 的 where 只保证 $gte）
    records = []
    for r in results:
        ts = r.get("metadata", {}).get("timestamp", 0)
        if isinstance(ts, str):
            try:
                ts = float(ts)
            except (ValueError, TypeError):
                ts = 0
        if ts_start <= ts < ts_end:
            records.append(r)

    # 按时间戳升序排列
    records.sort(key=lambda x: float(x.get("metadata", {}).get("timestamp", 0)))
    return records


def _group_sessions(records: list[dict], interval_seconds: int = _SESSION_INTERVAL) -> list[dict]:
    """将对话记录按时间间隔分组为会话。

    连续记录时间差 < interval_seconds 则归为同一会话。
    返回会话列表，按开始时间升序排列。
    """
    if not records:
        return []

    sessions = []
    current = {"records": [records[0]]}

    for i in range(1, len(records)):
        prev_ts = float(records[i - 1].get("metadata", {}).get("timestamp", 0))
        curr_ts = float(records[i].get("metadata", {}).get("timestamp", 0))
        if curr_ts - prev_ts < interval_seconds:
            current["records"].append(records[i])
        else:
            sessions.append(current)
            current = {"records": [records[i]]}
    sessions.append(current)

    # 格式化为结构化会话
    result = []
    for session in sessions:
        s_records = session["records"]
        start_ts = float(s_records[0].get("metadata", {}).get("timestamp", 0))
        end_ts = float(s_records[-1].get("metadata", {}).get("timestamp", 0))
        user_msgs = [r.get("document", "") for r in s_records if r.get("metadata", {}).get("type") == "user_input"]
        assistant_msgs = [
            r.get("document", "") for r in s_records if r.get("metadata", {}).get("type") == "assistant_output"
        ]
        result.append(
            {
                "start_time": start_ts,
                "end_time": end_ts,
                "rounds": max(len(user_msgs), len(assistant_msgs)),
                "user_messages": user_msgs,
                "assistant_messages": assistant_msgs,
            }
        )

    return result


def _get_active_task(memory_instance) -> dict:
    """获取当前活跃 task，按 status=active 查询。

    查询优先级：
    1. status=active → 当前活跃任务，标注「进行中」
    2. 无活跃 → 取最近一条 task，按 status 标注

    ChromaDB get() 必须显式 include documents 和 metadatas
    （默认不返回 documents，缺少会导致静默 None 而非报错）。
    """
    if memory_instance is None:
        return {}

    # 获取当前 project_id（简报上下文中的活跃项目）
    _pid = None
    try:
        from ..server.mcp import _project_id_ctx as _pid_ctx

        _pid = _pid_ctx.get()
    except Exception:
        pass

    try:
        # 1. 优先查 status=active（使用 list_memories 确保 project_id 过滤）
        active_results = memory_instance.list_memories(
            type_filter="task",
            project_id=_pid,
            where={"status": "active"},
            limit=1,
        )
        if active_results and active_results[0].get("document"):
            doc_raw = active_results[0]["document"]
            task_data = json.loads(doc_raw) if isinstance(doc_raw, str) else doc_raw
            task_data["_status"] = "active"
            task_data["_status_label"] = "进行中"
            return task_data

        # 2. 无活跃 → 取最近一条
        recent = memory_instance.list_memories(type_filter="task", project_id=_pid, limit=1)
        if recent:
            doc = recent[0].get("document", "{}")
            meta_status = recent[0].get("metadata", {}).get("status", "unknown")
            task_data = json.loads(doc) if isinstance(doc, str) else (doc or {})
            if not isinstance(task_data, dict):
                task_data = {}
            if meta_status == "completed":
                task_data["_status"] = "completed"
                task_data["_status_label"] = "已全部完成"
            elif meta_status == "pending":
                task_data["_status"] = "pending"
                task_data["_status_label"] = "待定（用户未确认）"
            else:
                task_data["_status"] = meta_status
                task_data["_status_label"] = "已归档"
            return task_data
    except Exception as e:
        logger.warning("获取活跃 task 失败: %s", e)

    return {}


def _get_today_task(memory_instance, tz_str: str = "Asia/Shanghai") -> dict:
    """从 ChromaDB 查询最近的任务记录（active 优先）。"""
    return _get_active_task(memory_instance)


def _get_today_knowledge(
    memory_instance, tz_str: str = "Asia/Shanghai", project_id: str = None, start_ts: float = None, end_ts: float = None
) -> list[dict]:
    """从 ChromaDB 查询知识写入记录（lesson/process）。

    start_ts/end_ts 可选：指定时间范围。不传则使用今日范围。
    """
    if start_ts is None or end_ts is None:
        ts_start, ts_end = _today_timestamp_range(tz_str)
    else:
        ts_start, ts_end = start_ts, end_ts
    try:
        results = memory_instance.list_memories(
            type_filter=["lesson", "process"],
            project_id=project_id,
            where={"timestamp": {"$gte": ts_start}},
            limit=50,
        )
        # 过滤时间范围
        knowledge = []
        for r in results:
            ts = r.get("metadata", {}).get("timestamp", 0)
            if isinstance(ts, str):
                try:
                    ts = float(ts)
                except (ValueError, TypeError):
                    ts = 0
            if ts_start <= ts < ts_end:
                knowledge.append(r)
        return knowledge
    except Exception as e:
        logger.warning("获取今日知识写入失败: %s", e)
        return []


# ─── 主函数 ───


def build_fallback_briefing(
    today_task: dict = None,
    today_events: list = None,
    event_count: int = 0,
    memory_instance=None,
    tz_str: str = "Asia/Shanghai",
    project_id: str = None,
    start_ts: float = None,
    end_ts: float = None,
) -> dict:
    """构建兜底简报（quality=simple，不调 LLM）。

    使用 ChromaDB 数据源（memory_instance 必传）。不传时返回最小默认简报。
    旧版 today_events 路径已移除（v0.7.1）。
    """
    # ── 新路径：使用 ChromaDB 数据 ──
    if memory_instance is not None:
        return _build_fallback_from_chromadb(
            memory_instance, today_task, tz_str, project_id=project_id, start_ts=start_ts, end_ts=end_ts
        )

    # ── 无 memory_instance 时的最小兜底（旧路径已移除）──
    task = today_task or {}
    done = task.get("progress", {}).get("done", [])
    todo = task.get("progress", {}).get("todo", [])
    summary = f"今日共 {event_count} 轮对话" if event_count else "今日无活跃任务记录"
    return {
        "summary": summary,
        "task_status": f"已完成: {len(done)}/{len(done) + len(todo)}" if done or todo else "无",
        "key_events": [],
        "new_knowledge": [],
        "plan_tomorrow": "无",
        "quality": "simple",
        "source": "lazy_hook",
        "session_count": 0,
    }


def _build_fallback_from_chromadb(
    memory_instance,
    task_data: dict = None,
    tz_str: str = "Asia/Shanghai",
    project_id: str = None,
    start_ts: float = None,
    end_ts: float = None,
) -> dict:
    """使用 ChromaDB 数据构建兜底简报（F8 新路径）。"""
    # 1. 获取对话记录
    records = _get_conversation_records(
        memory_instance, tz_str, project_id=project_id, start_ts=start_ts, end_ts=end_ts
    )
    total_rounds = len(records)

    # 2. 获取任务
    task = task_data or _get_today_task(memory_instance)

    # 3. 获取知识写入
    knowledge_list = _get_today_knowledge(memory_instance, project_id=project_id, start_ts=start_ts, end_ts=end_ts)

    # 4. 会话分组
    sessions = _group_sessions(records)
    session_count = len(sessions)

    # 5. 质量门禁：对话轮次 < 5 → 最小化简报
    if total_rounds < 5:
        return _build_minimal_briefing(task, total_rounds, session_count, len(knowledge_list))

    # 6. 构建详细兜底模板
    done = task.get("progress", {}).get("done", [])
    todo = task.get("progress", {}).get("todo", [])
    blocked = task.get("progress", {}).get("blocked", [])
    next_steps = task.get("next_steps", [])

    # 摘要
    task_progress = f"{len(done)}/{len(done) + len(todo)}" if task and (done or todo) else "无任务进度"
    summary = (
        f"今日共 {total_rounds} 轮对话（{session_count} 个会话），"
        f"当前任务进度：{task_progress}，"
        f"新增知识 {len(knowledge_list)} 条"
    )

    # 任务状态
    task_status = (
        (
            f"项目: {task.get('project', '')}\n"
            f"目标: {task.get('goal', '')}\n"
            f"已完成: {done}\n"
            f"待完成: {todo}\n"
            f"阻塞: {blocked}"
        )
        if task and task.get("project")
        else "无活跃任务"
    )

    # 会话摘要
    key_events = []
    for i, session in enumerate(sessions):
        start_str = time.strftime("%H:%M", time.localtime(session["start_time"]))
        end_str = time.strftime("%H:%M", time.localtime(session["end_time"]))
        user_preview = session["user_messages"][0][:80] if session["user_messages"] else ""
        key_events.append(f"[会话{i + 1}] {start_str}-{end_str}: {session['rounds']} 轮 | {user_preview}")

    # 知识摘要
    new_knowledge = []
    for k in knowledge_list:
        doc = k.get("document", "")[:100]
        meta = k.get("metadata", {})
        ktype = meta.get("type", "unknown")
        new_knowledge.append(f"[{ktype}] {doc}")

    # 明日计划
    plan_tomorrow = "根据当前进度，下一步计划：" + "；".join(next_steps) if task and next_steps else "无"

    return {
        "summary": summary,
        "task_status": task_status,
        "key_events": key_events,
        "new_knowledge": new_knowledge,
        "plan_tomorrow": plan_tomorrow,
        "quality": "simple",
        "source": "lazy_hook",
        "session_count": session_count,
    }


def _build_minimal_briefing(
    task: dict,
    total_rounds: int,
    session_count: int,
    knowledge_count: int,
) -> dict:
    """对话极少时的最小化简报。"""
    done = task.get("progress", {}).get("done", [])
    todo = task.get("progress", {}).get("todo", [])
    blocked = task.get("progress", {}).get("blocked", [])
    next_steps = task.get("next_steps", [])

    task_progress = f"{len(done)}/{len(done) + len(todo)}" if task and (done or todo) else "无任务进度"
    summary = f"今日对话极少（共 {total_rounds} 轮），当前任务进度：{task_progress}，新增知识 {knowledge_count} 条"

    task_status = (
        (f"项目: {task.get('project', '')}\n已完成: {done}\n待完成: {todo}\n阻塞: {blocked}")
        if task and task.get("project")
        else "无活跃任务"
    )

    plan_tomorrow = "根据当前进度，下一步计划：" + "；".join(next_steps) if task and next_steps else "无"

    return {
        "summary": summary,
        "task_status": task_status,
        "key_events": [f"今日仅有 {total_rounds} 轮对话（{session_count} 个会话），内容较少"],
        "new_knowledge": [],
        "plan_tomorrow": plan_tomorrow,
        "quality": "simple",
        "source": "lazy_hook",
        "session_count": session_count,
    }


def build_full_briefing(
    task_data: dict,
    sessions: list,
    new_knowledge: list,
    git_log_text: str,  # 新增 v0.7.1 F10
    git_diff_text: str,  # 新增 v0.7.1 F10
    llm_caller: Callable,
    llm_endpoint: str = None,
    prompt_id: str = None,
    date_str: str = None,  # 简报日期 YYYY-MM-DD，用于提示文案
) -> dict | None:
    """调用 MEMOS LLM 生成完整简报（quality=full）。

    v0.7.1 F10: 接收 Git 数据 + lesson/process 知识，使用结构化 Markdown 模板
    作为 User Prompt（遵循设计文档 4.2 节）。

    注：v0.7.1 F10 移除了旧版 _build_session_summaries 的 JSON 格式，
    User Prompt 改为 Markdown 模板。
    """
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")

    task_json = json.dumps(task_data, ensure_ascii=False) if task_data else "无活跃任务"

    sessions_text = ""
    for i, session in enumerate(sessions):
        if i >= 20:  # 限制会话数
            break
        start_str = time.strftime("%H:%M", time.localtime(session.get("start_time", 0)))
        end_str = time.strftime("%H:%M", time.localtime(session.get("end_time", 0)))
        user_msgs = session.get("user_messages", [])
        assistant_msgs = session.get("assistant_messages", [])
        rounds = max(len(user_msgs), len(assistant_msgs))
        topic = (user_msgs[0] if user_msgs else "")[:200]
        sessions_text += f"\n### 会话 {i + 1} ({start_str}-{end_str}, {rounds} 轮)\n"
        sessions_text += f"主题: {topic}\n"
        for j, msg in enumerate(user_msgs[:3]):
            sessions_text += f"用户: {msg[:200]}\n"
        for j, msg in enumerate(assistant_msgs[:2]):
            sessions_text += f"助手: {msg[:200]}\n"

    knowledge_text = (
        json.dumps([k.get("document", "") for k in new_knowledge], ensure_ascii=False) if new_knowledge else "无"
    )

    git_log_display = git_log_text[:2000] if git_log_text else "当日无提交"
    git_diff_display = git_diff_text[:500] if git_diff_text else "工作区无未提交变更"

    user_prompt = (
        f"请根据以下今日（{date_str}）的项目数据生成简报：\n\n"
        f"## 任务状态\n{task_json}\n\n"
        f"## 对话记录（已按会话分组）\n{sessions_text}\n\n"
        f"## Git 日志（已提交）\n{git_log_display}\n\n"
        f"## 工作区变更（未提交）\n{git_diff_display}\n\n"
        f"## 已有知识记录（lesson/process）\n{knowledge_text}"
    )

    # 系统提示词加载（保持现有 PromptManager 优先逻辑）
    system_prompt = _get_briefing_system_prompt(llm_endpoint, prompt_id)

    logger.info("[简报] 发送 LLM 请求...")
    result = llm_caller(system_prompt, user_prompt)
    if result:
        logger.info("[简报] LLM 响应成功，长度=%d 字符", len(result))
        try:
            parsed = json.loads(result) if isinstance(result, str) else result
            parsed["quality"] = "full"
            parsed["source"] = "auto_extracted"
            # Schema 校验
            validation = validate_briefing_schema(parsed)
            if validation["valid"]:
                return parsed
            else:
                logger.warning("[简报] Schema 校验失败: %s", validation["errors"])
                return None
        except json.JSONDecodeError as e:
            logger.error("[简报] LLM 输出非合法 JSON: %s", e)
            return None
    logger.warning("[简报] LLM 返回空结果")
    return None


def _get_briefing_system_prompt(llm_endpoint: str = None, prompt_id: str = None) -> str:
    """获取简报系统提示词，优先从 PromptManager 加载。"""
    from memos.config.models import _DEFAULT_BRIEFING_SYSTEM_PROMPT

    try:
        from memos.config.loader import get_config as _get_cfg

        tpl = _get_cfg().prompt.get_for_endpoint(llm_endpoint or "default", "briefing")
        if tpl:
            return tpl.effective_prompt().system_prompt
    except Exception:
        pass
    return _DEFAULT_BRIEFING_SYSTEM_PROMPT


def validate_briefing_schema(briefing: dict) -> dict:
    """校验简报输出是否符合新 Schema 的最小可行约束。

    校验项：
    - 顶层 7 字段存在性
    - task.progress 子对象完整性
    - task.status 枚举值
    - bug_fixes[].confidence 枚举值

    Returns: {"valid": bool, "errors": [str]}
    """
    errors = []
    required_fields = ["task", "achieved", "file_changes", "decisions", "bug_fixes", "new_knowledge", "suggested_next"]
    for f in required_fields:
        if f not in briefing:
            errors.append(f"缺少必需字段: {f}")

    task = briefing.get("task", {})
    if "progress" not in task:
        errors.append("task.progress 缺失")
    status = task.get("status")
    if status is None:
        errors.append("task.status 缺失")
    elif status not in ("active", "completed", "pending"):
        errors.append(f"task.status 值非法: {status}")

    for bf in briefing.get("bug_fixes", []):
        conf = bf.get("confidence")
        if conf and conf not in ("high", "medium", "low"):
            errors.append(f"bug_fixes[].confidence 值非法: {conf}")

    return {"valid": len(errors) == 0, "errors": errors}
