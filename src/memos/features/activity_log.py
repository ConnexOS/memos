"""活动日志采集 —— 三类事件（recall/写入/注入）的记录与轮转。

按天轮转（配置时区），保留 30 天。非阻塞写入（异步文件追加）。
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from ..config import config

logger = logging.getLogger(__name__)

_last_cleanup_time = 0.0


def _get_timezone() -> str:
    """检测本地时区。"""
    from ..config import get_local_timezone

    return get_local_timezone()


def _now_in_tz() -> datetime:
    """返回当地时区的当前时间。"""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(_get_timezone()))


def _get_log_path() -> Path:
    """获取活动日志目录路径。"""
    from ..config.models import get_memos_home

    custom = config.activity_log.log_path
    if custom:
        return Path(custom)
    return get_memos_home() / "etc"


def _get_log_filename(project_id: str = None, log_date: str = None) -> str:
    """获取活动日志文件名。按项目隔离：activity_log_{project_id}_{date}.jsonl"""
    if log_date is None:
        log_date = _now_in_tz().strftime("%Y-%m-%d")
    if project_id:
        return f"activity_log_{project_id}_{log_date}.jsonl"
    return f"activity_log_{log_date}.jsonl"


def _get_log_filepath(project_id: str = None, log_date: str = None) -> Path:
    return _get_log_path() / _get_log_filename(project_id, log_date)


def _cleanup_expired():
    """清理超 retention_days 的活动日志文件。首次写入时触发，节流 1h。"""
    global _last_cleanup_time
    now = time.time()
    if now - _last_cleanup_time < 3600:
        return
    _last_cleanup_time = now
    retention = config.activity_log.retention_days
    log_dir = _get_log_path()
    if not log_dir.exists():
        return
    now = time.time()
    cutoff = now - retention * 86400
    for f in log_dir.glob("activity_log_*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                logger.info("已删除过期活动日志: %s", f.name)
        except OSError as e:
            logger.warning("删除过期日志失败 %s: %s", f.name, e)


def _append_event(event_data: dict, project_id: str = None):
    """追加一条事件到当前日期的活动日志文件（按项目隔离）。非阻塞异步写入。"""
    _cleanup_expired()
    pid = project_id or event_data.get("project_id")
    filepath = _get_log_filepath(project_id=pid)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_data, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error("写入活动日志失败: %s", e)


def log_recall(query: str, result_count: int, match_types: list[str], extra: dict = None, project_id: str = None):
    """记录 recall 调用事件。"""
    event = {
        "event": "recall",
        "timestamp": time.time(),
        "query": query[:200],
        "result_count": result_count,
        "match_types": match_types or [],
    }
    if project_id:
        event["project_id"] = project_id
    if extra:
        event.update(extra)
    _append_event(event, project_id=project_id)
    # F9: SSE 事件总线通知
    try:
        from .event_bus import touch_event as _touch

        _touch("memory_stream")
    except Exception:
        logger.debug("活动日志: SSE 事件总线通知失败 (memory_stream)", exc_info=True)


def log_knowledge_write(type_: str, summary: str, source: str, extra: dict = None, project_id: str = None):
    """记录知识写入事件。"""
    event = {
        "event": "knowledge_write",
        "timestamp": time.time(),
        "type": type_,
        "summary": summary[:200],
        "source": source,
    }
    if project_id:
        event["project_id"] = project_id
    if extra:
        event.update(extra)
    _append_event(event, project_id=project_id)
    # F9: SSE 事件总线通知
    try:
        from .event_bus import touch_event as _touch

        _touch("memory_stream")
    except Exception:
        logger.debug("活动日志: SSE 事件总线通知失败 (memory_stream / knowledge_write)", exc_info=True)


def log_context_injection(
    memory_ids: list[str],
    types: list[str],
    injection_type: str = "knowledge",
    extra: dict = None,
    project_id: str = None,
):
    """记录上下文注入事件。injection_type 标记 knowledge(知识注入) 或 manual(用户建议)。"""
    event = {
        "event": "context_injection",
        "timestamp": time.time(),
        "memory_ids": memory_ids,
        "types": types,
        "injection_type": injection_type,
    }
    if project_id:
        event["project_id"] = project_id
    if extra:
        event.update(extra)
    _append_event(event, project_id=project_id)


def log_manual_injection(count: int, extra: dict = None, project_id: str = None):
    """记录用户建议注入事件。"""
    event = {
        "event": "manual_injection",
        "timestamp": time.time(),
        "count": count,
        "summary": f"用户建议 {count} 条注入",
    }
    if project_id:
        event["project_id"] = project_id
    if extra:
        event.update(extra)
    _append_event(event, project_id=project_id)


def log_ai_reference(memory_id: str, content_snippet: str, injected_at: float, matched: bool, project_id: str = None):
    """记录 AI 引用检测事件。"""
    event = {
        "event": "ai_reference",
        "timestamp": time.time(),
        "memory_id": memory_id,
        "content_snippet": content_snippet[:200],
        "injected_at": injected_at,
        "referenced": matched,
    }
    if project_id:
        event["project_id"] = project_id
    _append_event(event, project_id=project_id)


def read_events(project_id: str = None, date: str = None, page: int = 1, page_size: int = 20) -> dict:
    """从指定日期的活动日志文件中读取事件（按项目隔离，分页，时间倒序）。

    文件名格式：activity_log_{project_id}_{date}.jsonl
    """
    filepath = _get_log_filepath(project_id=project_id, log_date=date)
    if not filepath.exists():
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    events = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    events.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    total = len(events)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "items": events[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
