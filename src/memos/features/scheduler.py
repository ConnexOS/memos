"""Dashboard 调度线程 —— 定时生成简报 + TTL 遗忘扫描。

生成时间固定为当地时区 23:00（时区硬编码 Asia/Shanghai）。
"""

import json
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class TtlForgetTask:
    """定时扫描超时记忆并标记 forgotten。

    按类型覆盖/全局默认两级判定过期时间，支持首次扫描宽限期保护。
    """

    def __init__(self, mem):
        self.mem = mem

    def _check_ttl_warnings(self, ids, metas, documents=None):
        """检测 lesson 类型距过期 < 7 天的记忆，发送 ttl_warning。

        documents 参数用于取记忆文本作为标题（ChromaDB 文本存在 documents 而非 metadata.content）。
        """
        from ..config import get_config
        from ..features.notifications import get_notification_logger

        cfg = get_config()
        notifier = get_notification_logger()
        warn_days = getattr(cfg.memory, "ttl_warn_days", 7)
        warned = 0

        docs = documents or [None] * len(ids)

        for i, mid in enumerate(ids):
            meta = metas[i] or {}
            if meta.get("type") != "lesson":
                continue
            expires_at = meta.get("expires_at", 0) or 0
            if not expires_at:
                continue
            remaining = expires_at - time.time()
            if 0 < remaining < warn_days * 86400:
                doc_text = docs[i] if docs[i] else meta.get("content", "")
                notifier.notify(
                    type="ttl_warning",
                    title=f"即将过期: {doc_text[:40]}...",
                    message=f"距过期还有 {int(remaining / 86400)} 天",
                    metadata={"memory_id": mid, "expires_at": expires_at, "action": "renew"},
                )
                warned += 1
        return warned

    def run(self) -> int:
        from ..config import get_config

        cfg = get_config()
        if not cfg.memory.ttl_enabled:
            logger.debug("TTL 禁用，跳过扫描")
            return 0

        first_scan = not getattr(self, "_first_scan_done", False)
        if first_scan and cfg.memory.ttl_first_scan_grace_hours > 0:
            logger.info("TTL 首次扫描宽限期内，跳过执行")
            self._first_scan_done = True
            return 0

        now = time.time()
        type_overrides = cfg.memory.ttl_type_overrides
        default_expire = cfg.memory.ttl_default_expire_hours

        results = self.mem.store.get(
            where={"status": "active"},
            include=["metadatas", "documents"],
            limit=cfg.memory.ttl_scan_batch_size,
        )
        ids = results.get("ids", [])
        metas = results.get("metadatas", [])
        documents = results.get("documents", [])
        if not ids:
            return 0

        # v0.7.2: TTL warning 通知扫描
        self._check_ttl_warnings(ids, metas, documents)

        to_forget = []
        for i, mid in enumerate(ids):
            meta = metas[i] or {}
            mem_type = meta.get("type", "unknown")
            # 判定：类型覆盖 > 全局默认
            if mem_type in type_overrides:
                expire_hours = type_overrides[mem_type]
            else:
                expire_hours = default_expire
            if expire_hours == 0:
                continue  # 永不过期
            updated_at = meta.get("timestamp", meta.get("updated_at", now))
            if now - updated_at > expire_hours * 3600:
                to_forget.append(mid)

        if not to_forget:
            return 0

        batch_meta = [{"status": "forgotten", "forgotten_at": now} for _ in to_forget]
        self.mem.store.update(ids=to_forget, metadatas=batch_meta)
        logger.info("TTL 遗忘: %d 条", len(to_forget))

        # 写入活动日志
        try:
            from ..features.activity_log import _append_event as _log_event

            for mid in to_forget:
                _log_event(
                    {
                        "event": "memory_forgotten",
                        "timestamp": now,
                        "memory_id": mid,
                        "reason": "ttl_expired",
                    }
                )
        except Exception as e:
            logger.warning("TTL 活动日志写入失败（非致命）: %s", e)
        return len(to_forget)


class SchedulerThread:
    """简报调度线程。在配置时间（默认 23:00，配置时区）触发生成。"""

    def __init__(self, briefing_generator=None, memory_instance=None):
        self._running = False
        self._thread = None
        self._generator = briefing_generator
        self._memory = memory_instance
        self._ttl_task = TtlForgetTask(memory_instance) if memory_instance else None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="scheduler")
        self._thread.start()
        logger.info("SchedulerThread 已启动")

    def stop(self):
        self._running = False
        logger.info("SchedulerThread 已停止")

    def _now_in_tz(self) -> datetime:
        """返回本地时区的当前时间。"""
        from zoneinfo import ZoneInfo

        from ..config import get_local_timezone

        return datetime.now(ZoneInfo(get_local_timezone()))

    def _run_loop(self):
        while self._running:
            try:
                self._check_and_generate()
            except Exception as e:
                logger.error("调度循环异常: %s", e)
            time.sleep(60)

    def _check_and_generate(self):
        """方案A v0.7.2: 发现活跃项目 → 按项目循环 → 最小数据量门禁。

        1. TTL 遗忘 + 归档扫描（全局，不受项目影响）
        2. 23:00 后，发现过去 48h 有活动记录的项目
        3. 对每个项目：对话 < 3 轮 → 最小化简报；≥ 3 轮 → 完整简报管线
        """
        now = self._now_in_tz()

        # F7: 每日执行 forgotten→archived 自动归档扫描（仅当天首次）
        self._auto_archive_forgotten()

        # TTL 遗忘扫描（复用缓存实例确保 _first_scan_done 持久化）
        if self._ttl_task is None and self._memory is not None:
            self._ttl_task = TtlForgetTask(self._memory)
        ttl_task = self._ttl_task or TtlForgetTask(self._memory)
        try:
            count = ttl_task.run()
            if count > 0:
                logger.info("TTL 任务完成: %d 条已遗忘", count)
                from .event_bus import touch_event

                touch_event("memory_stream")
        except Exception as e:
            logger.error("TTL 任务失败: %s", e)

        if now.hour < 23:
            return

        today = now.strftime("%Y-%m-%d")

        if not self._generator:
            return

        projects = self._discover_active_projects()
        if not projects:
            logger.info("调度器: 无活跃项目，跳过简报生成")
            return

        logger.info("调度器: 发现 %d 个活跃项目: %s", len(projects), projects)

        for pid in projects:
            if self._has_today_project_briefing(today, pid):
                logger.debug("项目 %s 今日(%s)已有简报，跳过", pid, today)
                continue

            daily_rounds = self._count_today_rounds(pid)
            if daily_rounds < 3:
                logger.info("项目 %s 当日对话 %d 轮，未达 3 轮门槛，写入最小化简报", pid, daily_rounds)
                self._write_minimal_briefing(pid, today, daily_rounds)
                continue

            self._generator(project_id=pid)
            logger.info("调度器: 简报已生成 (%s, project=%s)", today, pid)

            # F9: SSE 事件总线通知（简报级别，非项目级别）
            try:
                from .event_bus import touch_event as _touch

                _touch("briefing")
            except Exception:
                logger.debug("调度器: SSE 事件总线通知失败 (briefing)", exc_info=True)

    def _auto_archive_forgotten(self):
        """自动归档过期记忆：forgotten→archived + pending→archived（合并扫描）。

        v0.7.1: 新增 pending→archived 扫描，30 天未激活的 pending 自动归档。
        """
        if self._memory is None:
            return
        # 每日首次执行（通过 _last_archive_date 跟踪）
        today = self._now_in_tz().strftime("%Y-%m-%d")
        if getattr(self, "_last_archive_date", "") == today:
            return
        self._last_archive_date = today
        try:
            # 1. forgotten→archived（现有逻辑）
            count = self._memory.archive_old_memories()
            if count > 0:
                logger.info("调度器: 自动归档 %d 条 forgotten 记忆", count)

            # 2. pending→archived（v0.7.1 新增：30 天未激活的 pending）
            cutoff = time.time() - 30 * 86400
            results = self._memory.store.get(
                where={
                    "$and": [
                        {"type": "task"},
                        {"status": "pending"},
                        {"updated_at": {"$lte": cutoff}},
                    ]
                },
                include=["metadatas"],
            )
            ids = results.get("ids", [])
            if ids:
                metas = [{"status": "archived", "inactive_reason": "never_activated"} for _ in ids]
                self._memory.store.update(ids=ids, metadatas=metas)
                logger.info("调度器: 自动归档 %d 条 pending task（30 天未激活）", len(ids))
        except Exception as e:
            logger.warning("调度器: 自动归档扫描异常: %s", e)

    def _has_today_project_briefing(self, today: str, project_id: str) -> bool:
        """检查指定项目今日是否已有简报（full 或 simple 均视为存在）。

        任何今日已存在的简报（包括最小化简报 quality=simple）
        阻止重复生成，防止调度循环每 60s 重复写入。
        """
        if self._memory is None:
            return False
        try:
            results = self._memory.list_memories(
                type_filter="briefing",
                project_id=project_id,
                limit=5,
            )
            for item in results:
                meta = item.get("metadata", {})
                if meta.get("briefing_date") == today:
                    return True
            return False
        except Exception as e:
            logger.warning("检查项目简报失败: %s", e)
            return False

    def _discover_active_projects(self) -> list[str]:
        """发现过去 48h 内有活动记录的项目（从 ChromaDB metadata 提取）。"""
        if self._memory is None:
            return []
        cutoff = time.time() - 172800  # 48h
        try:
            results = self._memory.store.get(
                where={"timestamp": {"$gte": cutoff}},
                include=["metadatas"],
                limit=2000,
            )
            pids: set[str] = set()
            for meta in results.get("metadatas", []):
                pid = (meta or {}).get("project_id", "")
                if pid:
                    pids.add(pid)
            return sorted(pids)
        except Exception as e:
            logger.warning("项目发现扫描失败: %s", e)
            return []

    def _count_today_rounds(self, project_id: str) -> int:
        """统计指定项目今日对话记录数（仅 metadatas 快速路径）。"""
        if self._memory is None:
            return 0
        now = self._now_in_tz()
        today_start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo).timestamp()
        today_end = now.timestamp()
        try:
            results = self._memory.store.get(
                where={
                    "$and": [
                        {"project_id": project_id},
                        {"type": {"$in": ["user_input", "assistant_output"]}},
                        {"timestamp": {"$gte": today_start}},
                    ]
                },
                include=["metadatas"],
                limit=500,
            )
            count = 0
            for meta in results.get("metadatas", []):
                ts = (meta or {}).get("timestamp", 0)
                if isinstance(ts, (int, float)) and today_start <= ts < today_end:
                    count += 1
            return count
        except Exception as e:
            logger.warning("统计对话轮次失败: %s", e)
            return 0

    def _write_minimal_briefing(self, project_id: str, today: str, rounds: int) -> None:
        """低活跃项目最小化简报：直接写兜底文本，无 LLM 无数据管线。"""
        briefing = {
            "summary": f"今日对话极少（共 {rounds} 轮），跳过详细简报",
            "task_status": "当前项目无活跃任务记录",
            "key_events": [],
            "new_knowledge": [],
            "plan_tomorrow": "无",
            "quality": "simple",
            "source": "auto_extracted",
            "session_count": 0,
            "task_done_count": 0,
            "task_todo_count": 0,
            "new_knowledge_count": 0,
        }
        briefing_meta = {
            "type": "briefing",
            "briefing_date": today,
            "source": "auto_extracted",
            "quality": "simple",
            "generated_at": time.time(),
            "delivered": False,
            "project_id": project_id,
            "task_done_count": 0,
            "task_todo_count": 0,
            "new_knowledge_count": 0,
            "session_count": 0,
        }
        try:
            self._memory.remember(
                json.dumps(briefing, ensure_ascii=False),
                metadata=briefing_meta,
            )
            logger.info("调度器: 最小化简报已写入 (%s, project=%s, rounds=%d)", today, project_id, rounds)
        except Exception as e:
            logger.error("最小化简报写入失败: %s", e)
