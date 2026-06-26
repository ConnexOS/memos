"""性能基线采集 —— 记录关键路径耗时数据。

设计原则：仅 Dashboard 进程启动时写入 performance_baseline.json，
避免高频 Hook 调用造成并发冲突和数据刷满。

高频指标（Hook 延迟等）写入活动日志，由 Dashboard 定期分析后汇总。
"""

import json
import logging
import time

from ..config.models import get_memos_home

logger = logging.getLogger(__name__)

_MAX_RECORDS = 10


def record_baseline(metrics: dict):
    """记录一条性能基线数据。

    每次 Dashboard 进程启动时追加一条。
    保留最近 10 条运行记录。
    """
    path = get_memos_home() / "etc" / "performance_baseline.json"
    records = []
    if path.exists():
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            records = []

    metrics["timestamp"] = time.time()
    records.append(metrics)

    if len(records) > _MAX_RECORDS:
        records = records[-_MAX_RECORDS:]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("性能基线已记录 (共 %d 条)", len(records))
