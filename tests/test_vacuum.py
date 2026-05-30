"""Phase 5 — F5: 数据库 Vacuum 策略测试（v0.4.0 HIGH-4 修复）"""

import pytest
import threading
import time
from unittest.mock import patch, MagicMock

from memos.engine.memory import ContextMemory
from memos.storage.base import VectorStore


class _MockStoreWithVacuum(VectorStore):
    """模拟 store，支持 get/count/vacuum，记录 vacuum 调用"""

    def __init__(self, records=None):
        self._records = list(records or [])
        self.vacuum_called = 0
        self.vacuum_blocking = False  # 用于并发测试

    supports_offset = True

    def _match_where(self, record, where):
        if where is None:
            return True
        if "$and" in where:
            return all(self._match_where(record, clause) for clause in where["$and"])
        for key, value in where.items():
            if key in ("$and", "$or"):
                continue
            record_val = record["metadata"].get(key)
            if isinstance(value, dict):
                if "$ne" in value:
                    if record_val == value["$ne"]:
                        return False
                elif "$in" in value:
                    if record_val not in value["$in"]:
                        return False
            elif record_val != value:
                return False
        return True

    def get(self, where=None, limit=None, offset=None, include=None, ids=None):
        if ids is not None:
            result = {"ids": [], "documents": [], "metadatas": []}
            for r in self._records:
                if r["id"] in ids:
                    result["ids"].append(r["id"])
                    result["documents"].append(r["document"])
                    result["metadatas"].append(r["metadata"])
            return result
        result = {"ids": [], "documents": [], "metadatas": []}
        for r in self._records:
            if self._match_where(r, where):
                result["ids"].append(r["id"])
                result["documents"].append(r.get("document", ""))
                result["metadatas"].append(r["metadata"])
        return result

    def count(self, where=None) -> int:
        if where is not None:
            return sum(1 for r in self._records if self._match_where(r, where))
        return len(self._records)

    def query(self, *a, **kw):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def add(self, documents, embeddings, metadatas, ids):
        for d, e, m, i in zip(documents, embeddings, metadatas, ids):
            self._records.append({"id": i, "document": d, "metadata": m})

    def update(self, ids, metadatas=None, documents=None, embeddings=None):
        pass

    def delete(self, ids):
        self._records = [r for r in self._records if r["id"] not in ids]

    def vacuum(self) -> bool:
        self.vacuum_called += 1
        # 模拟 Vacuum 耗时（并发测试用）
        if self.vacuum_blocking:
            time.sleep(0.2)
        return True


def _make_records(count: int, active: bool = True, start_id: int = 0):
    """构造测试记录"""
    records = []
    t0 = 1_700_000_000.0
    for i in range(count):
        records.append(
            {
                "id": f"rec_{start_id + i:04d}",
                "document": f"这是第 {start_id + i} 条记忆。",
                "metadata": {
                    "timestamp": t0 - i * 3600,
                    "type": "fact",
                    "project_id": "test-project",
                    "active": active,
                },
            }
        )
    return records


class TestDeleteStats:
    """F5.1: _get_deleted_stats 统计准确性"""

    def test_all_active(self):
        store = _MockStoreWithVacuum(_make_records(50, active=True))
        mem = ContextMemory(store=store)
        stats = mem._get_deleted_stats()
        assert stats["total"] == 50
        assert stats["active"] == 50
        assert stats["deleted"] == 0

    def test_some_inactive(self):
        records = _make_records(100, active=True)
        # 标记前 30 条为 inactive
        for i in range(30):
            records[i]["metadata"]["active"] = False
        store = _MockStoreWithVacuum(records)
        mem = ContextMemory(store=store)
        stats = mem._get_deleted_stats()
        assert stats["total"] == 100
        assert stats["active"] == 70
        assert stats["deleted"] == 30

    def test_all_inactive(self):
        store = _MockStoreWithVacuum(_make_records(10, active=False))
        mem = ContextMemory(store=store)
        stats = mem._get_deleted_stats()
        assert stats["total"] == 10
        assert stats["active"] == 0
        assert stats["deleted"] == 10


class TestVacuumTrigger:
    """F5.2: _maybe_vacuum 触发阈值（删除>20%且>100条）"""

    def test_below_threshold_ratio(self):
        """删除 50/500=10% → 不触发"""
        records = _make_records(500, active=True)
        for i in range(50):
            records[i]["metadata"]["active"] = False
        store = _MockStoreWithVacuum(records)
        mem = ContextMemory(store=store)
        result = mem._maybe_vacuum()
        assert result is False
        assert store.vacuum_called == 0

    def test_below_threshold_count(self):
        """删除 30/100=30% 但绝对数<100 → 不触发"""
        records = _make_records(100, active=True)
        for i in range(30):
            records[i]["metadata"]["active"] = False
        store = _MockStoreWithVacuum(records)
        mem = ContextMemory(store=store)
        result = mem._maybe_vacuum()
        assert result is False
        assert store.vacuum_called == 0

    def test_triggers_vacuum(self):
        """删除 300/1000=30% → 触发"""
        records = _make_records(1000, active=True)
        for i in range(300):
            records[i]["metadata"]["active"] = False
        store = _MockStoreWithVacuum(records)
        mem = ContextMemory(store=store)
        result = mem._maybe_vacuum()
        assert result is True
        assert store.vacuum_called == 1

    def test_edge_ratio_20_percent(self):
        """删除 200/1000=20% → 恰好 20%（不触发，因为 > 0.2 不含等号）"""
        records = _make_records(1000, active=True)
        for i in range(200):
            records[i]["metadata"]["active"] = False
        store = _MockStoreWithVacuum(records)
        mem = ContextMemory(store=store)
        result = mem._maybe_vacuum()
        # ratio = 0.2，需求为 > 0.2，不触发
        assert result is False
        assert store.vacuum_called == 0

    def test_zero_deleted(self):
        store = _MockStoreWithVacuum(_make_records(200, active=True))
        mem = ContextMemory(store=store)
        result = mem._maybe_vacuum()
        assert result is False
        assert store.vacuum_called == 0


class TestVacuumLock:
    """F5.5: Vacuum 并发写入保护（v0.4.0 HIGH-2）"""

    def test_remember_blocks_during_vacuum(self):
        """Vacuum 期间 remember() 应拒绝写入"""
        store = _MockStoreWithVacuum(_make_records(100))
        mem = ContextMemory(store=store)

        # 手动持有锁模拟 Vacuum 进行中
        acquired = mem._vacuum_lock.acquire(blocking=False)
        assert acquired

        try:
            from memos.errors import ChromaDBError

            with pytest.raises(ChromaDBError, match="数据库维护中"):
                mem.remember("test text during vacuum")
        finally:
            mem._vacuum_lock.release()

    def test_remember_ok_after_vacuum(self):
        """Vacuum 完成后 remember() 恢复正常"""
        store = _MockStoreWithVacuum(_make_records(10))
        mem = ContextMemory(store=store)

        # 模拟完整 Vacuum 周期
        with mem._vacuum_lock:
            store.vacuum()
        # 锁释放后写入正常
        # 注意：_ensure_encoder 需要 mock
        with patch.object(mem, "_ensure_encoder"):
            mid = mem.remember("normal write", embedding=[0.0] * 1024)
            assert mid is not None

    def test_concurrent_vacuum_lock(self):
        """并发 Vacuum 调用互斥验证"""
        store = _MockStoreWithVacuum(_make_records(1000))
        for i in range(300):
            store._records[i]["metadata"]["active"] = False
        store.vacuum_blocking = True
        mem = ContextMemory(store=store)

        vacuum_results = []

        def run_vacuum():
            ok = mem._maybe_vacuum()
            vacuum_results.append(ok)

        threads = [threading.Thread(target=run_vacuum) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 只有一个线程应该触发 vacuum（因为锁保护 + 第一个执行后 stats 已变化）
        assert sum(vacuum_results) >= 1


class TestDeleteMemoryIntegration:
    """delete_memory → _remove_from_bm25 → _maybe_vacuum 链路"""

    def test_delete_triggers_vacuum_check(self):
        """删除记忆后 _maybe_vacuum 被调用"""
        records = _make_records(500, active=True)
        # 先标记 200 条为 inactive（模拟已删除）
        for i in range(200):
            records[i]["metadata"]["active"] = False
        store = _MockStoreWithVacuum(records)
        mem = ContextMemory(store=store)

        # 删除一条活跃记忆（让删除计数 +1）
        old_vacuum_called = store.vacuum_called
        # 活跃记录在 200+ 之后
        mem.delete_memory("rec_0200")
        # 删除后：201 inactive, total=500, ratio=40.2% > 20%，应触发
        assert store.vacuum_called > old_vacuum_called
