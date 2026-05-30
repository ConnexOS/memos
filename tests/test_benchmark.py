"""Phase 7 / F4: 基准测试脚本基本验证 (5+ Smoke 用例)"""

import json
import pytest

from memos.engine.memory import ContextMemory

COLLECTION = "bench_test_smoke"


def _cleanup():
    """清理临时 collection"""
    try:
        mem = ContextMemory(COLLECTION)
        all_ids = mem.store.get()["ids"]
        if all_ids:
            mem.store.delete(ids=all_ids)
    except Exception:
        pass


class TestBenchmarkDataGenerator:
    """验证数据生成器产出正确格式"""

    COLLECTION = COLLECTION

    def setup_method(self):
        _cleanup()
        self.mem = ContextMemory(self.COLLECTION)
        self.mem.warmup()

    def teardown_method(self):
        _cleanup()

    def test_generate_and_count(self):
        """生成 10 条记忆并验证计数"""
        for i in range(10):
            self.mem.remember(f"测试记忆第{i}条", metadata={"type": "fact", "project_id": "bench_test"})
        all_items = self.mem.list_memories(limit=100, include_archived=True)
        assert len(all_items) >= 10

    def test_generate_with_metadata(self):
        """验证写入的 metadata 完整"""
        mem_id = self.mem.remember(
            "带元数据的测试记忆",
            metadata={"type": "decision", "project_id": "bench_test", "quality_score": 0.85, "reuse_count": 5},
        )
        m = self.mem.get_memory(mem_id)
        assert m is not None
        assert m["metadata"]["type"] == "decision"
        assert m["metadata"]["quality_score"] == 0.85
        assert m["metadata"]["reuse_count"] == 5

    def test_retrieval_basic(self):
        """生成 20 条后基本检索可用"""
        for i in range(20):
            self.mem.remember(f"检索测试内容第{i}条", metadata={"type": "fact", "project_id": "bench_test"})
        results = self.mem.recall("检索测试", top_k=5)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_list_pagination(self):
        """列表分页正确"""
        for i in range(25):
            self.mem.remember(f"分页测试第{i}条", metadata={"type": "fact", "project_id": "bench_test"})
        page1 = self.mem.list_memories(limit=10, offset=0)
        page2 = self.mem.list_memories(limit=10, offset=10)
        assert len(page1) == 10
        assert len(page2) == 10
        # 两页不重复
        ids1 = {m["id"] for m in page1}
        ids2 = {m["id"] for m in page2}
        assert ids1 & ids2 == set(), f"分页重叠: {ids1 & ids2}"

    def test_concurrent_read(self):
        """并发读取不崩溃"""
        import threading

        for i in range(50):
            self.mem.remember(f"并发读取第{i}条", metadata={"type": "fact", "project_id": "bench_test"})

        errors = []
        lock = threading.Lock()

        def _reader(wid: int):
            try:
                for _ in range(10):
                    self.mem.recall("并发读取", top_k=5)
            except Exception as e:
                with lock:
                    errors.append(f"worker{wid}: {e}")

        threads = [threading.Thread(target=_reader, args=(w,)) for w in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0, f"并发读取异常: {errors}"
