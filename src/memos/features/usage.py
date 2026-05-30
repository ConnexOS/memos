"""v0.4.1: LLM 用量统计 —— JSONL 持久化 + 聚合查询"""

import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _usage_log_path() -> Path:
    from ..config import get_memos_home

    return get_memos_home() / "etc" / "usage_log.jsonl"


class UsageLogger:
    """LLM 用量统计日志器（JSONL 追加写入 + 内存查询）"""

    def __init__(self, log_path: str = None):
        if log_path is None:
            log_path = str(_usage_log_path())
        self._log_path = log_path
        self._lock = threading.Lock()

    def log(self, event: dict):
        """追加一条事件到 JSONL"""
        event.setdefault("timestamp", time.time())
        try:
            with self._lock:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("用量日志写入失败: %s", e)

    def query(self, since: float = None, until: float = None, endpoint: str = None) -> list[dict]:
        """查询时间范围内的统计事件"""
        results = []
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = evt.get("timestamp", 0)
                    if since and ts < since:
                        continue
                    if until and ts > until:
                        continue
                    if endpoint and endpoint != "all" and evt.get("endpoint") != endpoint:
                        continue
                    results.append(evt)
        except FileNotFoundError:
            pass
        return results

    def get_stats(self, period: str = "today", endpoint: str = "all", memory=None, project_id: str = None) -> dict:
        """聚合统计。若传入 memory 则从 ChromaDB 按 source 真实统计保存数，
        而非依赖 LLM 调用事件（提炼≠保存）。"""
        now = time.time()
        if period == "today":
            since = now - (now % 86400)
        elif period == "week":
            # 本周一 00:00（与 memories API days=7 对齐）
            tm = time.localtime(now)
            since = now - (now % 86400) - tm.tm_wday * 86400
        elif period == "month":
            since = now - 30 * 86400
        else:
            since = now - 86400

        events = self.query(since=since, endpoint=endpoint)
        # 手工 / 自动 分类统计（LLM 调用次数）
        auto_success = sum(1 for e in events if e.get("event") == "extract_auto_success")
        auto_failed = sum(1 for e in events if e.get("event") == "extract_auto_failed")
        manual_success = sum(1 for e in events if e.get("event") == "extract_manual_success")
        manual_failed = sum(1 for e in events if e.get("event") == "extract_manual_failed")
        # 兼容旧日志（event 名不含 _auto/_manual 前缀）
        legacy_success = sum(1 for e in events if e.get("event") == "extract_success")
        legacy_failed = sum(1 for e in events if e.get("event") == "extract_failed")
        auto_success += legacy_success
        auto_failed += legacy_failed

        total_auto_calls = auto_success + auto_failed
        total_manual_calls = manual_success + manual_failed
        total_calls = total_auto_calls + total_manual_calls
        success_count = auto_success + manual_success
        failed_count = auto_failed + manual_failed

        # 按手工/自动统计知识卡片数
        if memory is not None:
            # 从 ChromaDB 按 source + timestamp 真实统计（保存数，非提炼数）
            auto_memories = 0
            manual_memories = 0
            try:
                and_clauses = [{"timestamp": {"$gte": since}}, {"active": {"$ne": False}}]
                if project_id:
                    and_clauses.append({"project_id": project_id})
                all_records = memory.store.get(
                    where={"$and": and_clauses},
                    include=["metadatas"],
                )
                for meta in all_records.get("metadatas") or []:
                    src = meta.get("source", "")
                    if src == "auto_extracted":
                        auto_memories += 1
                    elif src in ("user_extracted", "user_appended", "user_instructed"):
                        manual_memories += 1
            except Exception:
                pass  # ChromaDB 查询失败时回退到事件计数
            if auto_memories == 0 and manual_memories == 0:
                # 回退：ChromaDB 不支持 $gte 时用事件计数
                auto_memories = sum(
                    e.get("memories_extracted", 0)
                    for e in events
                    if e.get("event") in ("extract_auto_success", "extract_success")
                )
                manual_memories = sum(
                    e.get("memories_extracted", 0) for e in events if e.get("event") in ("extract_manual_success",)
                )
        else:
            auto_memories = sum(
                e.get("memories_extracted", 0)
                for e in events
                if e.get("event") in ("extract_auto_success", "extract_success")
            )
            manual_memories = sum(
                e.get("memories_extracted", 0) for e in events if e.get("event") in ("extract_manual_success",)
            )
        total_memories = auto_memories + manual_memories
        input_tokens = sum(e.get("input_tokens", 0) for e in events)
        output_tokens = sum(e.get("output_tokens", 0) for e in events)

        # v0.4.1 过期统计
        expiring_soon = 0
        expired = 0
        if memory is not None:
            try:
                from ..config import config

                archive_sec = config.memory.archive_days * 86400
                warn_sec = config.memory.expiry_warn_days * 86400
                expired_cutoff = now - archive_sec
                warn_start = expired_cutoff
                warn_end = expired_cutoff + warn_sec
                # 已过期: timestamp < now - archive_days*86400
                expired_records = memory.store.get(
                    where={"$and": [{"timestamp": {"$lt": expired_cutoff}}, {"active": {"$ne": False}}]},
                    include=["metadatas"],
                )
                expired = len(expired_records.get("ids", []))
                # 即将过期: expired_cutoff <= timestamp < expired_cutoff + warn_days*86400
                expiring_records = memory.store.get(
                    where={
                        "$and": [
                            {"timestamp": {"$gte": warn_start}},
                            {"timestamp": {"$lt": warn_end}},
                            {"active": {"$ne": False}},
                        ]
                    },
                    include=["metadatas"],
                )
                expiring_soon = len(expiring_records.get("ids", []))
            except Exception:
                pass

        return {
            "period": period,
            "total_calls": total_calls,
            "auto_calls": total_auto_calls,
            "manual_calls": total_manual_calls,
            "total_cards": total_memories,
            "auto_cards": auto_memories,
            "manual_cards": manual_memories,
            "success_count": success_count,
            "failed_count": failed_count,
            "success_rate": round(success_count / total_calls * 100, 1) if total_calls > 0 else 0,
            "total_tokens": input_tokens + output_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "expiring_soon": expiring_soon,
            "expired": expired,
        }

    def get_trend(self, days: int = 7) -> list[dict]:
        """近 N 天每日统计趋势"""
        now = time.time()
        trend = []
        for d in range(days - 1, -1, -1):
            day_start = now - (now % 86400) - d * 86400
            day_end = day_start + 86400
            day_events = self.query(since=day_start, until=day_end)
            total = len(day_events)
            success = sum(
                1
                for e in day_events
                if e.get("event")
                in ("extract_auto_success", "extract_manual_success", "extract_success", "daily_review_success")
            )
            trend.append(
                {
                    "date": time.strftime("%m/%d", time.localtime(day_start)),
                    "weekday": ["一", "二", "三", "四", "五", "六", "日"][time.localtime(day_start).tm_wday],
                    "count": total,
                    "success_rate": round(success / total * 100, 1) if total > 0 else 0,
                }
            )
        return trend

    def cleanup(self, retention_days: int = 90):
        """清理过期日志"""
        cutoff = time.time() - retention_days * 86400
        try:
            events = self.query()
            kept = [e for e in events if e.get("timestamp", 0) >= cutoff]
            if len(kept) < len(events):
                with self._lock:
                    with open(self._log_path, "w", encoding="utf-8") as f:
                        for e in kept:
                            f.write(json.dumps(e, ensure_ascii=False) + "\n")
                logger.info("用量日志清理: %d → %d 条 (保留 %d 天)", len(events), len(kept), retention_days)
        except FileNotFoundError:
            pass


# 全局单例
usage_logger = UsageLogger()
