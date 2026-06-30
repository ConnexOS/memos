"""v0.4.2: 系统通知中心 —— JSONL 持久化 + 频率限制 + 自动清理

三类通知：
- extract_complete：提炼完成
- conflict_detected：冲突检测
- expiry_alert：知识过期提醒
"""

import json
import logging
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def _notify_log_path() -> Path:
    from ..config import get_memos_home

    return get_memos_home() / "etc" / "notifications.jsonl"


class NotificationLogger:
    """系统通知日志器（JSONL 追加写入 + 内存查询）"""

    def __init__(self, log_path: str = None):
        if log_path is None:
            log_path = str(_notify_log_path())
        self._log_path = log_path
        self._lock = threading.Lock()

    def _read_all(self) -> list[dict]:
        """读取全部通知记录。"""
        results = []
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass
        return results

    def _write_all(self, records: list[dict]) -> None:
        """全量写回通知文件。"""
        import os as _os

        _os.makedirs(_os.path.dirname(self._log_path), exist_ok=True)
        with open(self._log_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def notify(self, type: str, title: str, message: str, link: str = "", metadata: dict = None) -> str | None:
        """新增一条通知。频率限制：同类型 rate_limit_minutes 内不重复。

        Returns: 通知 ID，若被频率限制跳过则返回 None。
        """
        return self._notify(type, title, message, link, metadata)

    def _notify(self, type: str, title: str, message: str, link: str = "", metadata: dict = None) -> str | None:
        """内部通知写入（含频率限制检查）。"""
        from ..config import config

        rate_limit = config.notification.rate_limit_minutes if hasattr(config, "notification") else 60

        notif_id = uuid.uuid4().hex[:12]
        now = time.time()

        record = {
            "id": notif_id,
            "timestamp": now,
            "type": type,
            "title": title,
            "message": message,
            "link": link,
            "read": False,
            "dismissed": False,
            "metadata": metadata or {},
        }

        try:
            with self._lock:
                # 频率限制：检查最近一条同类型通知
                existing = self._read_all()
                for rec in reversed(existing):
                    if rec.get("type") == type:
                        age_minutes = (now - rec.get("timestamp", 0)) / 60
                        if age_minutes < rate_limit:
                            logger.debug("通知频率限制: type=%s, 上次=%d分钟前", type, int(age_minutes))
                            return None
                        break

                # 写入新通知
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("通知写入失败: %s", e)
            return None

        return notif_id

    def get_unread_counts(self) -> dict:
        """获取按类型分组的未读计数（动态统计 JSONL 中所有类型）。"""
        counts: dict[str, int] = {}
        for rec in self._read_all():
            if not rec.get("read") and not rec.get("dismissed"):
                t = rec.get("type", "unknown")
                counts[t] = counts.get(t, 0) + 1
        counts["total"] = sum(counts.values())
        return counts

    def list_notifications(
        self,
        type_filter: list[str] = None,
        status: str = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """列出通知（按时间倒序）。

        Args:
            type_filter: 类型过滤列表，None 返回全部
            status: "unread" 仅未读，"read" 仅已读，None 返回全部
            limit: 返回条数上限
            offset: 分页偏移

        Returns: (records, total_count)
        """
        from ..config import config

        retention_days = config.notification.retention_days if hasattr(config, "notification") else 30
        cutoff = time.time() - retention_days * 86400

        # TOCTOU 修复：过期清理在锁保护下执行，防止与 _update_field 并发
        with self._lock:
            all_records = self._read_all()
            all_records.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
            valid_records = []
            cleaned = 0
            for rec in all_records:
                if rec.get("read") and rec.get("timestamp", 0) < cutoff:
                    cleaned += 1
                    continue
                valid_records.append(rec)
            if cleaned > 0:
                self._write_all(valid_records)

        # 过滤
        filtered = []
        for rec in valid_records:
            if type_filter and rec.get("type") not in type_filter:
                continue
            if status == "unread" and (rec.get("read") or rec.get("dismissed")):
                continue
            if status == "read" and not rec.get("read"):
                continue
            if rec.get("dismissed"):
                continue
            filtered.append(rec)

        total = len(filtered)
        page = filtered[offset : offset + limit]
        return page, total

    def mark_read(self, notif_id: str) -> bool:
        """标记通知为已读。"""
        return self._update_field(notif_id, "read", True)

    def dismiss(self, notif_id: str) -> bool:
        """忽略通知（dismissed=true）。"""
        return self._update_field(notif_id, "dismissed", True)

    def _update_field(self, notif_id: str, field: str, value: bool) -> bool:
        """更新通知的布尔字段。"""
        with self._lock:
            records = self._read_all()
            found = False
            for rec in records:
                if rec.get("id") == notif_id:
                    rec[field] = value
                    found = True
                    break
            if found:
                self._write_all(records)
            return found

    def get_recent(self, limit: int = 5) -> list[dict]:
        """获取最近 N 条未忽略通知（供导航栏下拉使用）。"""
        records = self._read_all()
        records.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
        return [r for r in records if not r.get("dismissed")][:limit]


# 模块级单例
_notification_logger: NotificationLogger | None = None


def get_notification_logger() -> NotificationLogger:
    """获取通知日志器单例。"""
    global _notification_logger
    if _notification_logger is None:
        _notification_logger = NotificationLogger()
    return _notification_logger
