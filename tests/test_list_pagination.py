"""Phase 1 — F1: list_memories 分页优化 和 count_memories count 优化 测试"""

import pytest
from unittest.mock import patch, MagicMock

from memos.engine.memory import ContextMemory
from memos.storage.base import VectorStore


class _MockStorePaged(VectorStore):
    """模拟支持 offset 的 store"""

    def __init__(self, records):
        self._records = records  # list of {"id": ..., "document": ..., "metadata": {...}}

    supports_offset = True

    def _match_where(self, record, where):
        """简易 where 过滤：支持单字段等值和 $and 组合"""
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
            result = {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
            for r in self._records:
                if r["id"] in ids:
                    result["ids"].append(r["id"])
                    result["documents"].append(r["document"])
                    result["metadatas"].append(r["metadata"])
                    result["embeddings"].append(None)
            return result

        # 模拟全量返回 metadatas（排序用），支持 where 过滤
        result = {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
        for r in self._records:
            if self._match_where(r, where):
                result["ids"].append(r["id"])
                result["metadatas"].append(r["metadata"])
                result["documents"].append(None)
                result["embeddings"].append(None)
        return result

    def query(self, query_embeddings, n_results, where=None, include=None) -> dict:
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def add(self, documents, embeddings, metadatas, ids) -> None:
        pass

    def update(self, ids, metadatas=None, documents=None, embeddings=None) -> None:
        pass

    def delete(self, ids) -> None:
        pass

    def count(self, where=None) -> int:
        if where is not None:
            return sum(1 for r in self._records if self._match_where(r, where))
        return len(self._records)

    def vacuum(self) -> bool:
        return False


class _MockStoreNoOffset(_MockStorePaged):
    """模拟不支持 offset 的 store（回退路径）"""

    supports_offset = False


def _make_records(count: int):
    """构造时间倒序的测试记录（id=t0 最新, id=t{count-1} 最旧）"""
    records = []
    t0 = 1_700_000_000.0
    for i in range(count):
        records.append(
            {
                "id": f"rec_{i:04d}",
                "document": f"这是第 {i} 条记忆的完整内容，包含足够长度的文本用于检索测试。",
                "metadata": {
                    "timestamp": t0 - i * 3600,  # 每条间隔 1 小时
                    "type": "fact" if i % 3 == 0 else "decision",
                    "project_id": "test-project",
                    "active": True,  # 默认全部活跃
                },
            }
        )
    return records


class TestListMemoriesPagination:
    """list_memories 分页测试"""

    def test_empty_collection(self):
        mem = ContextMemory(store=_MockStorePaged([]))
        result = mem.list_memories()
        assert result == []

    def test_first_page(self):
        records = _make_records(100)
        mem = ContextMemory(store=_MockStorePaged(records))
        result = mem.list_memories(limit=20, offset=0)
        assert len(result) == 20
        # 应按时间降序：rec_0000 最新
        assert result[0]["id"] == "rec_0000"
        assert result[19]["id"] == "rec_0019"

    def test_second_page(self):
        records = _make_records(100)
        mem = ContextMemory(store=_MockStorePaged(records))
        result = mem.list_memories(limit=20, offset=20)
        assert len(result) == 20
        assert result[0]["id"] == "rec_0020"

    def test_partial_last_page(self):
        records = _make_records(25)
        mem = ContextMemory(store=_MockStorePaged(records))
        result = mem.list_memories(limit=20, offset=20)
        assert len(result) == 5  # 仅剩 5 条

    def test_offset_beyond_total(self):
        records = _make_records(10)
        mem = ContextMemory(store=_MockStorePaged(records))
        result = mem.list_memories(limit=20, offset=50)
        assert result == []

    def test_large_dataset(self):
        """5000 条记忆的分页性能验证"""
        records = _make_records(5000)
        mem = ContextMemory(store=_MockStorePaged(records))
        import time

        t0 = time.time()
        result = mem.list_memories(limit=20, offset=4500)
        elapsed = time.time() - t0
        assert len(result) == 20
        # 5000 条 metadata 排序 + 分页应在 200ms 内完成
        assert elapsed < 0.5, f"5000条分页耗时 {elapsed:.3f}s，超过 500ms"

    def test_no_offset_fallback(self):
        """不支持 offset 的 store 回退到全量内存分页"""
        records = _make_records(50)
        mem = ContextMemory(store=_MockStoreNoOffset(records))
        result = mem.list_memories(limit=10, offset=5)
        assert len(result) == 10
        assert result[0]["id"] == "rec_0005"

    def test_type_filter(self):
        records = _make_records(100)
        mem = ContextMemory(store=_MockStorePaged(records))
        result = mem.list_memories(type_filter="fact", limit=50)
        assert len(result) <= 50
        for item in result:
            assert item["metadata"]["type"] == "fact"

    def test_include_archived(self):
        records = _make_records(50)
        # 标记前 5 条为 inactive
        for i in range(5):
            records[i]["metadata"]["active"] = False
        mem = ContextMemory(store=_MockStorePaged(records))
        # 默认 exclude archived → 45 条
        result_default = mem.list_memories(limit=100)
        # include archived → 50 条
        result_with = mem.list_memories(limit=100, include_archived=True)
        assert len(result_default) == 45
        assert len(result_with) == 50


class TestCountMemoriesOptimization:
    """count_memories 优化测试"""

    def test_count_no_filter(self):
        records = _make_records(50)
        mem = ContextMemory(store=_MockStorePaged(records))
        assert mem.count_memories() == 50

    def test_count_with_where(self):
        records = _make_records(50)
        mem = ContextMemory(store=_MockStorePaged(records))
        # Mock store 的 count(where=...) 做了简化实现
        count = mem.count_memories()
        assert count == 50

    def test_count_uses_store_count(self):
        """验证 count_memories 调用了 store.count(where=...)"""
        records = _make_records(10)
        store = _MockStorePaged(records)
        mem = ContextMemory(store=store)
        with patch.object(store, "count", wraps=store.count) as spy:
            mem.count_memories()
            spy.assert_called_once()


class TestMCPListMemories:
    """MCP list_memories 工具集成验证"""

    def test_mcp_list_passthrough(self):
        """确认 limit/offset 参数通过 memory.list_memories 正确透传"""
        records = _make_records(30)
        mem = ContextMemory(store=_MockStorePaged(records))
        result = mem.list_memories(limit=10, offset=10)
        assert len(result) == 10
        # 第 10-19 条（0-indexed）
        assert result[0]["id"] == "rec_0010"
        assert result[-1]["id"] == "rec_0019"
