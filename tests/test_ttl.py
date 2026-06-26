"""TTL 遗忘 SchedulerTask 单元测试。"""

import time

import pytest

from src.memos.features.scheduler import TtlForgetTask


class MockStore:
    """模拟 ChromaDB 存储，供 TTL 测试使用。"""

    def __init__(self):
        self.data = {"ids": [], "metadatas": [], "documents": []}

    def get(self, where=None, include=None, limit=None):
        include = include or ["metadatas"]
        result = {"ids": [], "metadatas": [], "documents": []}
        for i, mid in enumerate(self.data["ids"]):
            meta = self.data["metadatas"][i] or {}
            if where and "$and" in where:
                conds = where["$and"]
                match = all(
                    meta.get(k) == v for cond in conds for k, v in cond.items()
                )
                if not match:
                    continue
            result["ids"].append(mid)
            if "metadatas" in include:
                result["metadatas"].append(meta)
            if "documents" in include:
                result["documents"].append(self.data["documents"][i])
        if limit:
            result["ids"] = result["ids"][:limit]
            result["metadatas"] = result["metadatas"][:limit]
            result["documents"] = result["documents"][:limit]
        return result

    def update(self, ids=None, metadatas=None):
        for i, mid in enumerate(self.data["ids"]):
            if mid in ids:
                idx = ids.index(mid)
                if metadatas and idx < len(metadatas):
                    self.data["metadatas"][i] = {
                        **(self.data["metadatas"][i] or {}),
                        **metadatas[idx],
                    }


class MockMemory:
    """模拟 ContextMemory，供 TTL 测试使用。"""

    def __init__(self):
        self.store = MockStore()

    def add_memory(self, doc, metadata=None):
        mid = f"mem_{int(time.time()*1000)}_{len(self.store.data['ids'])}"
        self.store.data["ids"].append(mid)
        self.store.data["metadatas"].append(metadata or {})
        self.store.data["documents"].append(doc)
        self.last_id = mid
        return mid

    def get_memory(self, mem_id):
        for i, mid in enumerate(self.store.data["ids"]):
            if mid == mem_id:
                return {
                    "id": mid,
                    "document": self.store.data["documents"][i],
                    "metadata": self.store.data["metadatas"][i],
                }
        return None


def test_ttl_scan_expired(monkeypatch):
    """task 类型 48h 后应被遗忘。"""
    from src.memos.config import get_config

    cfg = get_config()
    monkeypatch.setattr(cfg.memory, "ttl_enabled", True)
    monkeypatch.setattr(cfg.memory, "ttl_first_scan_grace_hours", 0)

    mem = MockMemory()
    mem.add_memory(
        "test",
        metadata={
            "type": "task",
            "timestamp": time.time() - 50 * 3600,
            "status": "active",
        },
    )
    task = TtlForgetTask(mem)
    count = task.run()
    assert count == 1
    record = mem.get_memory(mem.last_id)
    assert record["metadata"]["status"] == "forgotten"


def test_ttl_skip_never_expire(monkeypatch):
    """solution 类型 expire_hours=0 永不过期。"""
    from src.memos.config import get_config

    cfg = get_config()
    monkeypatch.setattr(cfg.memory, "ttl_enabled", True)
    monkeypatch.setattr(cfg.memory, "ttl_first_scan_grace_hours", 0)

    mem = MockMemory()
    mem.add_memory(
        "test",
        metadata={
            "type": "solution",
            "timestamp": time.time() - 365 * 24 * 3600,
            "status": "active",
        },
    )
    task = TtlForgetTask(mem)
    count = task.run()
    assert count == 0


def test_ttl_disabled(monkeypatch):
    """ttl_enabled=false 时不扫描。"""
    from src.memos.config import get_config

    cfg = get_config()
    monkeypatch.setattr(cfg.memory, "ttl_enabled", False)

    mem = MockMemory()
    mem.add_memory(
        "test",
        metadata={
            "type": "task",
            "timestamp": time.time() - 50 * 3600,
            "status": "active",
        },
    )
    task = TtlForgetTask(mem)
    count = task.run()
    assert count == 0


def test_ttl_first_scan_grace(monkeypatch):
    """首次扫描宽限期跳过一次，第二次正常扫描。"""
    from src.memos.config import get_config

    cfg = get_config()
    monkeypatch.setattr(cfg.memory, "ttl_enabled", True)
    monkeypatch.setattr(cfg.memory, "ttl_first_scan_grace_hours", 24)

    mem = MockMemory()
    mem.add_memory(
        "test",
        metadata={
            "type": "task",
            "timestamp": time.time() - 50 * 3600,
            "status": "active",
        },
    )
    task = TtlForgetTask(mem)
    count = task.run()
    assert count == 0  # 跳过
    # 第二次调用应该执行
    count2 = task.run()
    assert count2 == 1  # 应该遗忘
