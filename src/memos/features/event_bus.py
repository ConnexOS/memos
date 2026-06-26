"""Event Timestamp Bus —— 线程安全的事件时间戳存储，供 SSE 实时推送使用。

每个事件类型维护一个单调递增的时间戳。Dashboard SSE 端点通过轮询此总线
检测变化，向客户端推送增量事件通知。

事件类型：
  - memory_stream: 活动日志 / 知识写入
  - watchlist: 待关注列表变更
  - task: Task 状态变更
  - briefing: 简报生成
  - feedback: 反馈反哺（useful_feedback_count 变更）
"""

import threading
import time

# 线程安全的事件时间戳字典
_event_timestamps: dict[str, float] = {
    "memory_stream": 0.0,
    "watchlist": 0.0,
    "task": 0.0,
    "briefing": 0.0,
    "feedback": 0.0,
}

_lock = threading.Lock()


def touch_event(event_type: str) -> None:
    """更新指定事件类型的时间戳为当前时间。

    由各写入路径在关键操作完成后调用，触发 SSE 推送。
    如果 event_type 不在预定义列表中，静默忽略（便于扩展）。
    """
    if event_type not in _event_timestamps:
        return
    with _lock:
        _event_timestamps[event_type] = time.time()


def get_event_timestamps() -> dict[str, float]:
    """返回事件时间戳的深拷贝快照，供 SSE 端点比较。"""
    with _lock:
        return dict(_event_timestamps)


def get_event_timestamp(event_type: str) -> float:
    """返回指定事件类型的时间戳。"""
    with _lock:
        return _event_timestamps.get(event_type, 0.0)
