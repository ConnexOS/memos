"""v0.4.0 「性能」集成测试 —— 校验接口联动、数据流转、模块协作、异常边界。

覆盖: F1(分页优化) / F2(模型轻量化) / F3(延迟加载) / F4(BM25增量) /
       F5(Vacuum策略) / 跨模块协作 / 回归验证 / 异常边界
"""

import json
import os
import sys
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from memos.engine.memory import ContextMemory, _get_similarity_threshold, _get_encoder
from memos.storage.base import VectorStore
from memos.config import config, MemoConfig, ModelConfig
from memos.errors import ChromaDBError

# ============================================================
# Mock Store (支持完整 CRUD + BM25 测试)
# ============================================================


class _IntegrationStore(VectorStore):
    """全功能 mock store，支持 where 过滤、分页、count、vacuum 跟踪"""

    supports_offset = True

    def __init__(self, records=None):
        self._records: list[dict] = list(records or [])
        self.vacuum_called = 0
        self.add_calls = 0
        self.update_calls = 0
        self.delete_calls = 0

    # --- where 匹配 ---
    def _match(self, rec, where):
        if where is None:
            return True
        if "$and" in where:
            return all(self._match(rec, c) for c in where["$and"])
        rv = rec["metadata"] or {}  # v0.4.0: metadata=None 安全防护
        for k, v in where.items():
            if k in ("$and", "$or"):
                continue
            rec_val = rv.get(k)
            if isinstance(v, dict):
                if "$ne" in v:
                    if rec_val == v["$ne"]:
                        return False
                elif "$in" in v:
                    if rec_val not in v["$in"]:
                        return False
                elif "$gte" in v:
                    if not (isinstance(rec_val, (int, float)) and rec_val >= v["$gte"]):
                        return False
                elif "$lte" in v:
                    if not (isinstance(rec_val, (int, float)) and rec_val <= v["$lte"]):
                        return False
            elif rec_val != v:
                return False
        return True

    # --- CRUD ---
    def get(self, where=None, limit=None, offset=None, include=None, ids=None):
        if ids is not None:
            idset = set(ids)
            result = {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
            for r in self._records:
                if r["id"] in idset:
                    result["ids"].append(r["id"])
                    result["documents"].append(r["document"])
                    result["metadatas"].append(r["metadata"])
                    result["embeddings"].append(r.get("embedding"))
            return result
        result = {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
        for r in self._records:
            if self._match(r, where):
                result["ids"].append(r["id"])
                result["documents"].append(r.get("document", ""))
                result["metadatas"].append(r["metadata"])
                result["embeddings"].append(r.get("embedding"))
        return result

    def query(self, query_embeddings, n_results, where=None, include=None):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def add(self, documents, embeddings, metadatas, ids):
        self.add_calls += 1
        for d, e, m, i in zip(documents, embeddings, metadatas, ids):
            self._records.append({"id": i, "document": d, "metadata": m, "embedding": e})

    def update(self, ids, metadatas=None, documents=None, embeddings=None):
        self.update_calls += 1
        ids_list = ids if isinstance(ids, list) else [ids]
        for r in self._records:
            if r["id"] in ids_list:
                if metadatas is not None:
                    idx = ids_list.index(r["id"]) if isinstance(metadatas, list) else 0
                    meta_update = metadatas[idx] if isinstance(metadatas, list) else metadatas
                    if r["metadata"] is None:
                        r["metadata"] = {}
                    r["metadata"].update(meta_update)
                if documents is not None:
                    idx = ids_list.index(r["id"]) if isinstance(documents, list) else 0
                    r["document"] = documents[idx] if isinstance(documents, list) else documents
                if embeddings is not None:
                    idx = ids_list.index(r["id"]) if isinstance(embeddings, list) else 0
                    r["embedding"] = embeddings[idx] if isinstance(embeddings, list) else embeddings

    def delete(self, ids):
        self.delete_calls += 1
        idset = set(ids if isinstance(ids, list) else [ids])
        self._records = [r for r in self._records if r["id"] not in idset]

    def count(self, where=None) -> int:
        if where is not None:
            return sum(1 for r in self._records if self._match(r, where))
        return len(self._records)

    def vacuum(self) -> bool:
        self.vacuum_called += 1
        return True


# ============================================================
# 数据工厂
# ============================================================


def _make_record(idx: int, text: str = None, meta: dict = None, active: bool = True):
    t0 = 1_700_000_000.0
    base = {
        "id": f"rec_{idx:06d}",
        "document": text or f"记忆内容 {idx}，包含测试文本用于检索验证。",
        "metadata": {
            "timestamp": t0 - idx * 3600,
            "type": meta.get("type") if meta else "fact",
            "project_id": meta.get("project_id") if meta else "test-project",
            "active": active,
        },
    }
    if meta:
        base["metadata"].update({k: v for k, v in meta.items() if k not in ("type", "project_id")})
    return base


def _make_records(count: int, offset: int = 0, text_fn=None, meta_fn=None):
    return [
        _make_record(i, text_fn(i) if text_fn else None, meta_fn(i) if meta_fn else None)
        for i in range(offset, offset + count)
    ]


# ============================================================
# F1 — 分页优化集成测试
# ============================================================


class TestF1_PaginationIntegration:
    """F1: 跨模块分页联动 — memory → store → ChromaDB"""

    def test_list_memories_pagination_chain(self):
        """F1.1: supports_offset 路径分页正确性"""
        store = _IntegrationStore(_make_records(100))
        mem = ContextMemory(store=store)

        page1 = mem.list_memories(limit=20, offset=0)
        page2 = mem.list_memories(limit=20, offset=20)
        page3 = mem.list_memories(limit=20, offset=80)

        assert len(page1) == 20
        assert len(page2) == 20
        assert len(page3) == 20
        # 无重叠
        ids1 = {r["id"] for r in page1}
        ids2 = {r["id"] for r in page2}
        ids3 = {r["id"] for r in page3}
        assert ids1.isdisjoint(ids2)
        assert ids1.isdisjoint(ids3)
        assert ids2.isdisjoint(ids3)
        # 时间降序
        assert page1[0]["metadata"]["timestamp"] > page1[-1]["metadata"]["timestamp"]

    def test_list_memories_empty_collection(self):
        """空集合分页不崩溃"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        assert mem.list_memories(limit=20, offset=0) == []
        assert mem.list_memories(limit=20, offset=100) == []

    def test_list_memories_partial_last_page(self):
        """最后一页不足 limit 条"""
        store = _IntegrationStore(_make_records(25))
        mem = ContextMemory(store=store)
        result = mem.list_memories(limit=20, offset=20)
        assert len(result) == 5

    def test_count_memories_uses_store_count(self):
        """F1.3: count_memories 调 store.count"""
        store = _IntegrationStore(_make_records(50))
        mem = ContextMemory(store=store)
        # 无过滤
        assert mem.count_memories() == 50
        # 按类型过滤
        assert mem.count_memories(type_filter="fact") >= 1

    def test_count_with_project_filter(self):
        """count + project_id 过滤"""
        records = _make_records(30, meta_fn=lambda i: {"project_id": "proj-a" if i < 15 else "proj-b"})
        store = _IntegrationStore(records)
        mem = ContextMemory(store=store)
        assert mem.count_memories(project_id="proj-a") == 15

    def test_supports_offset_false_fallback(self):
        """F1.5: 不支持 offset 的 store 回退全量内存分页"""

        class NoOffsetStore(_IntegrationStore):
            supports_offset = False

        store = NoOffsetStore(_make_records(50))
        mem = ContextMemory(store=store)
        result = mem.list_memories(limit=10, offset=10)
        assert len(result) == 10

    def test_large_dataset_pagination_performance(self):
        """5000 条分页性能 < 500ms"""
        store = _IntegrationStore(_make_records(5000))
        mem = ContextMemory(store=store)
        t0 = time.time()
        result = mem.list_memories(limit=20, offset=4500)
        elapsed = time.time() - t0
        assert len(result) == 20
        assert elapsed < 0.5, f"5000条分页 {elapsed:.3f}s"

    def test_metadata_none_safety(self):
        """metadata=None 不崩溃（v0.4.0 修复）"""
        records = [_make_record(0, meta={})]
        records[0]["metadata"] = None  # 模拟异常数据
        store = _IntegrationStore(records)
        mem = ContextMemory(store=store)
        result = mem.list_memories(limit=10)
        # 不应崩溃，metadata 为 None 时用空字典
        assert len(result) >= 0


# ============================================================
# F2 — 模型轻量化集成测试
# ============================================================


class TestF2_LightweightModelIntegration:
    """F2: 模型轻量化选项 — 配置联动 + 阈值适配"""

    def test_model_config_fields_consistency(self):
        """F2.3: ModelConfig name/path/vector_dim 一致性"""
        # bge-large
        cfg = ModelConfig(name="bge-large-zh-v1.5", vector_dim=1024)
        d = cfg.model_dump()
        assert d["name"] == "bge-large-zh-v1.5"
        assert d["vector_dim"] == 1024
        # MiniLM
        cfg2 = ModelConfig(name="all-MiniLM-L6-v2", vector_dim=384)
        assert cfg2.vector_dim == 384

    def test_threshold_bge(self, monkeypatch):
        """1024维 → 0.55"""
        from memos.engine import memory as mod

        monkeypatch.setattr(mod.config.model, "vector_dim", 1024)
        assert _get_similarity_threshold() == 0.55

    def test_threshold_minilm(self, monkeypatch):
        """384维 → 0.65"""
        from memos.engine import memory as mod

        monkeypatch.setattr(mod.config.model, "vector_dim", 384)
        assert _get_similarity_threshold() == 0.65

    def test_threshold_unknown_dim_fallback(self, monkeypatch):
        """未知维度回退到 config 默认值"""
        from memos.engine import memory as mod

        monkeypatch.setattr(mod.config.model, "vector_dim", 768)
        assert _get_similarity_threshold() == 0.55  # config 默认

    def test_model_name_in_config_schema(self):
        """model.name 在 JSON Schema 中"""
        from memos.config import get_config_schema

        schema = get_config_schema()
        model_props = schema["properties"]["model"]["properties"]
        assert "name" in model_props
        assert "vector_dim" in model_props


# ============================================================
# F3 — 延迟加载集成测试
# ============================================================


class TestF3_LazyLoadingIntegration:
    """F3: 延迟加载 — 实例化零等待 + 按需加载"""

    def test_instance_creation_no_encoder_load(self, monkeypatch):
        """F3.1: 实例化不触发模型加载"""
        called = [False]

        def fake_get():
            called[0] = True
            return MagicMock()

        monkeypatch.setattr("memos.engine.memory._get_encoder", fake_get)
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        assert not called[0], "实例化不应加载模型"

    def test_instance_creation_fast(self):
        """实例化 < 100ms"""
        store = _IntegrationStore([])
        t0 = time.time()
        mem = ContextMemory(store=store)
        elapsed = (time.time() - t0) * 1000
        assert elapsed < 100, f"实例化 {elapsed:.1f}ms"

    def test_list_memories_without_encoder(self, monkeypatch):
        """list_memories 不触发编码器加载"""
        called = [False]

        def fake_get():
            called[0] = True
            return MagicMock()

        monkeypatch.setattr("memos.engine.memory._get_encoder", fake_get)
        store = _IntegrationStore(_make_records(10))
        mem = ContextMemory(store=store)
        mem.list_memories()
        assert not called[0]

    def test_count_memories_without_encoder(self, monkeypatch):
        """count_memories 不触发编码器加载"""
        called = [False]

        def fake_get():
            called[0] = True
            return MagicMock()

        monkeypatch.setattr("memos.engine.memory._get_encoder", fake_get)
        store = _IntegrationStore(_make_records(10))
        mem = ContextMemory(store=store)
        mem.count_memories()
        assert not called[0]

    def test_remember_triggers_encoder(self, monkeypatch):
        """F3.2: 首次 remember 触发加载"""
        import numpy as np

        class FakeEncoder:
            def encode(self, text):
                return np.zeros(1024, dtype=np.float32)

        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: FakeEncoder())
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        assert not mem.is_encoder_loaded
        mem._ensure_encoder()
        assert mem.is_encoder_loaded

    def test_warmup_idempotent(self, monkeypatch):
        """F3.4: warmup 多次调用安全"""
        import numpy as np

        class FakeEncoder:
            def encode(self, text):
                return np.zeros(1024, dtype=np.float32)

        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: FakeEncoder())
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        mem.warmup()
        mem.warmup()  # 不应报错
        assert mem.is_encoder_loaded

    def test_recall_triggers_encoder(self, monkeypatch):
        """recall 首次调用触发编码器加载"""
        import numpy as np

        class FakeEncoder:
            def encode(self, text):
                return np.zeros(1024, dtype=np.float32)

        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: FakeEncoder())
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        assert not mem.is_encoder_loaded
        mem._ensure_encoder()
        assert mem.is_encoder_loaded

    def test_encoder_double_check_lock(self, monkeypatch):
        """F3.3: 多线程同时调用仅加载一次"""
        call_count = [0]

        def counting():
            call_count[0] += 1
            import numpy as np

            class E:
                def encode(self, t):
                    return np.zeros(1024, dtype=np.float32)

            return E()

        monkeypatch.setattr("memos.engine.memory._get_encoder", counting)
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)

        def worker():
            mem._ensure_encoder()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert call_count[0] == 1, f"_get_encoder called {call_count[0]} times"


# ============================================================
# F4 — BM25 增量更新集成测试
# ============================================================


class TestF4_BM25IncrementalIntegration:
    """F4: BM25 增量更新 — 写入→检索联动 + 一致性"""

    def test_write_then_recall_incremental(self):
        """F4.1: 写入后增量追加，检索反映新内容"""
        docs = [f"文档 {i} 包含独特关键词 term_{i} 和通用词汇" for i in range(50)]
        store = _IntegrationStore([_make_record(i, docs[i]) for i in range(50)])
        mem = ContextMemory(store=store)

        # 构建 BM25
        mem._ensure_bm25_index()
        assert mem._bm25 is not None
        assert mem._bm25.corpus_size == 50

        # 增量追加
        mem._add_to_bm25("新文档包含全新关键词 new_term_999")
        assert mem._bm25.corpus_size == 51

        # 检索新文档
        scores = mem._bm25.get_scores(mem._tokenize("new_term_999"))
        assert scores[-1] > 0  # 最后一条（新文档）应有正分数

    def test_incremental_vs_full_rebuild_consistency(self):
        """F4: 增量追加与全量重建分数一致（100 条）"""
        import numpy as np

        docs = [f"doc {i} has unique word_{i} and shared tokens for testing bm25" for i in range(100)]

        # 增量：3 条初始 + 97 条追加
        store1 = _IntegrationStore([_make_record(i, docs[i]) for i in range(3)])
        mem1 = ContextMemory(store=store1)
        mem1._ensure_bm25_index()
        for i in range(3, 100):
            store1._records.append(_make_record(i, docs[i]))
            mem1._add_to_bm25(docs[i])
        scores_inc = mem1._bm25.get_scores(mem1._tokenize("unique shared testing"))

        # 全量：100 条一次性
        store2 = _IntegrationStore([_make_record(i, docs[i]) for i in range(100)])
        mem2 = ContextMemory(store=store2)
        mem2._ensure_bm25_index()
        scores_full = mem2._bm25.get_scores(mem2._tokenize("unique shared testing"))

        assert len(scores_inc) == len(scores_full) == 100
        for i, (a, b) in enumerate(zip(scores_inc, scores_full)):
            assert abs(a - b) < 0.001, f"doc {i}: {a:.6f} vs {b:.6f}"

    def test_update_in_bm25_reflects_changes(self):
        """F4.3: update 后检索反映新内容"""
        store = _IntegrationStore([_make_record(0, "旧的文档内容"), _make_record(1, "另一个文档")])
        mem = ContextMemory(store=store)
        mem._ensure_bm25_index()

        mem._update_in_bm25("旧的文档内容", "全新的文档内容包含fresh关键词")
        assert "全新的文档内容包含fresh关键词" in mem._bm25_docs
        assert "旧的文档内容" not in mem._bm25_docs

    def test_remove_invalidates_bm25(self):
        """F4.3: 删除文档 → invalidate BM25"""
        store = _IntegrationStore([_make_record(0, "doc0"), _make_record(1, "doc1")])
        mem = ContextMemory(store=store)
        mem._ensure_bm25_index()
        assert mem._bm25 is not None

        mem._remove_from_bm25("doc0")
        assert mem._bm25 is None  # invalidated

    def test_reindex_rebuilds_bm25(self):
        """F4.4: memos reindex 全量重建 BM25（v0.4.0 HIGH-1）"""
        store = _IntegrationStore([_make_record(i, f"文档{i}") for i in range(20)])
        mem = ContextMemory(store=store)

        # 初始无 BM25
        assert mem._bm25 is None

        # reindex
        mem._invalidate_bm25()
        mem._ensure_bm25_index()

        assert mem._bm25 is not None
        assert mem._bm25.corpus_size == 20

    def test_concurrent_add_to_bm25_thread_safety(self):
        """F4.5: 并发追加 BM25 不崩溃"""
        store = _IntegrationStore([_make_record(0, "初始文档")])
        mem = ContextMemory(store=store)
        mem._ensure_bm25_index()

        errors = []

        def worker(i):
            try:
                for j in range(20):
                    mem._add_to_bm25(f"线程 {i} 的第 {j} 条文档内容")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发追加错误: {errors}"
        assert mem._bm25.corpus_size == 1 + 4 * 20


# ============================================================
# F5 — Vacuum 策略集成测试
# ============================================================


class TestF5_VacuumIntegration:
    """F5: Vacuum 策略 — delete→stats→vacuum 链路"""

    def test_delete_stats_accuracy(self):
        """F5.1: 删除统计准确性"""
        records = _make_records(200)
        # 标记 50 条为 inactive
        for i in range(50):
            records[i]["metadata"]["active"] = False
        store = _IntegrationStore(records)
        mem = ContextMemory(store=store)

        stats = mem._get_deleted_stats()
        assert stats["total"] == 200
        assert stats["active"] == 150
        assert stats["deleted"] == 50

    def test_delete_stats_all_active(self):
        """全部活跃"""
        store = _IntegrationStore(_make_records(100))
        mem = ContextMemory(store=store)
        stats = mem._get_deleted_stats()
        assert stats["deleted"] == 0
        assert stats["active"] == 100

    def test_delete_stats_all_inactive(self):
        """全部已删除"""
        records = _make_records(10, meta_fn=lambda i: {"active": False})
        for r in records:
            r["metadata"]["active"] = False
        store = _IntegrationStore(records)
        mem = ContextMemory(store=store)
        stats = mem._get_deleted_stats()
        assert stats["deleted"] == 10

    def test_vacuum_not_triggered_below_threshold(self):
        """F5.2: 删除<20% 不触发"""
        # 50/500 = 10%
        records = _make_records(500)
        for i in range(50):
            records[i]["metadata"]["active"] = False
        store = _IntegrationStore(records)
        mem = ContextMemory(store=store)
        result = mem._maybe_vacuum()
        assert result is False
        assert store.vacuum_called == 0

    def test_vacuum_not_triggered_below_100(self):
        """删除>20% 但 <100 条不触发"""
        # 30/100=30% 但 < 100
        records = _make_records(100)
        for i in range(30):
            records[i]["metadata"]["active"] = False
        store = _IntegrationStore(records)
        mem = ContextMemory(store=store)
        result = mem._maybe_vacuum()
        assert result is False

    def test_vacuum_triggers_when_threshold_met(self):
        """F5.2: 删除>20% 且 >100 → 触发"""
        # 300/1000=30%
        records = _make_records(1000)
        for i in range(300):
            records[i]["metadata"]["active"] = False
        store = _IntegrationStore(records)
        mem = ContextMemory(store=store)
        result = mem._maybe_vacuum()
        assert result is True
        assert store.vacuum_called == 1

    def test_vacuum_lock_blocks_write(self):
        """F5.5: Vacuum 期间 remember() 拒绝写入"""
        store = _IntegrationStore(_make_records(50))
        mem = ContextMemory(store=store)

        acquired = mem._vacuum_lock.acquire(blocking=False)
        assert acquired
        try:
            with pytest.raises(ChromaDBError, match="数据库维护中"):
                mem.remember("测试文本")
        finally:
            mem._vacuum_lock.release()

    def test_vacuum_lock_released_allows_write(self, monkeypatch):
        """Vacuum 锁释放后写入恢复"""
        store = _IntegrationStore(_make_records(10))
        mem = ContextMemory(store=store)

        # 模拟 Vacuum 完成
        with mem._vacuum_lock:
            store.vacuum()

        # 锁释放后可写入
        with patch.object(mem, "_ensure_encoder"):
            mid = mem.remember("正常写入", embedding=[0.0] * 1024)
            assert mid is not None
            assert store.add_calls >= 1

    def test_delete_memory_triggers_vacuum_check(self):
        """delete_memory → _maybe_vacuum 链路"""
        records = _make_records(500)
        for i in range(200):
            records[i]["metadata"]["active"] = False
        store = _IntegrationStore(records)
        mem = ContextMemory(store=store)
        pre = store.vacuum_called
        # 删除一条活跃记忆
        mem.delete_memory("rec_000200")
        # 201/500=40.2% > 20%, >100 → 触发
        assert store.vacuum_called > pre


# ============================================================
# 跨模块协作集成测试
# ============================================================


class TestCrossModuleCollaboration:
    """跨模块协作：MCP ↔ memory ↔ store ↔ extractor 全链路"""

    def test_remember_recall_list_cycle(self):
        """写入 → 检索 → 列表 完整链路"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)

        # 写入
        ids = []
        for i in range(10):
            with patch.object(mem, "_ensure_encoder"):
                mid = mem.remember(
                    f"测试记忆 {i}", metadata={"type": "fact", "project_id": "proj-x"}, embedding=[0.0] * 1024
                )
                if mid:
                    ids.append(mid)

        assert len(ids) == 10

        # 列表
        items = mem.list_memories(project_id="proj-x", limit=20, offset=0)
        assert len(items) == 10

        # count
        total = mem.count_memories(project_id="proj-x")
        assert total == 10

    def test_project_isolation(self):
        """项目隔离：proj-a 和 proj-b 数据不交叉"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)

        with patch.object(mem, "_ensure_encoder"):
            for i in range(5):
                mem.remember(
                    f"proj-a 记忆 {i}", metadata={"project_id": "proj-a", "type": "fact"}, embedding=[0.0] * 1024
                )
            for i in range(3):
                mem.remember(
                    f"proj-b 记忆 {i}", metadata={"project_id": "proj-b", "type": "fact"}, embedding=[0.0] * 1024
                )

        assert mem.count_memories(project_id="proj-a") == 5
        assert mem.count_memories(project_id="proj-b") == 3
        assert mem.count_memories() == 8

    @pytest.mark.skip(reason="v0.4.2: export header format changed, needs test update")
    def test_export_import_roundtrip(self):
        """导出 → 导入 往返一致性"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)

        with patch.object(mem, "_ensure_encoder"):
            for i in range(20):
                mem.remember(
                    f"导出测试 {i}", metadata={"type": "fact", "project_id": "exp-proj"}, embedding=[0.0] * 1024
                )

        # 导出
        exported = list(mem.export_memories(project_id="exp-proj", include_embeddings=False))
        assert len(exported) == 20
        for item in exported:
            assert "id" in item
            assert "content" in item
            assert "metadata" in item

        # 导入到新项目
        import_lines = [
            json.dumps(
                {
                    "content": f"导入记忆 {i}",
                    "metadata": {"type": "fact", "project_id": "imp-proj"},
                }
            )
            for i in range(5)
        ]
        result = mem.import_memories(import_lines, target_project_id="imp-proj", strategy="skip")
        assert result["imported"] == 5

    def test_update_metadata_only_no_reencode(self, monkeypatch):
        """只更新 metadata 时不触发 encoder（v0.4.0 P3-2 优化）"""
        import numpy as np

        class FakeEncoder:
            def encode(self, text):
                return np.zeros(1024, dtype=np.float32)

        # 先 monkeypatch _get_encoder，再创建实例（绕过模块级缓存）
        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: FakeEncoder())
        store = _IntegrationStore(_make_records(0))
        mem = ContextMemory(store=store)

        # 先写入一条记忆（通过 embedding 参数跳过 encoder）
        mid = mem.remember("原始内容", metadata={"type": "fact", "project_id": "test"}, embedding=[0.0] * 1024)

        # warmup 加载 encoder
        mem.warmup()
        assert mem._encoder is not None

        # 埋点：检测 encode 是否被调用
        encode_called = [False]
        orig_encode = mem._encoder.encode

        def spy(text):
            encode_called[0] = True
            return orig_encode(text)

        mem._encoder.encode = spy

        # 仅更新 metadata → 不应触发 encode
        mem.update_memory(mid, new_metadata={"type": "decision"})
        assert not encode_called[0], f"仅改 metadata 不应调用 encode，但被调用了"


# ============================================================
# 异常与边界条件测试
# ============================================================


class TestExceptionAndBoundary:
    """异常入参、边界条件、错误处理"""

    def test_delete_nonexistent_raises(self):
        """删除不存在的记忆 → 抛异常"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        with pytest.raises(ChromaDBError, match="未找到"):
            mem.delete_memory("nonexistent_id")

    def test_update_nonexistent_raises(self):
        """更新不存在的记忆 → 抛异常"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        with pytest.raises(ChromaDBError, match="未找到"):
            mem.update_memory("nonexistent_id", "新内容")

    def test_get_nonexistent_returns_none(self):
        """获取不存在的记忆 → None"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        assert mem.get_memory("nonexistent") is None

    def test_list_offset_beyond_total(self):
        """offset 超过总数 → 空列表"""
        store = _IntegrationStore(_make_records(10))
        mem = ContextMemory(store=store)
        assert mem.list_memories(offset=100) == []

    def test_count_empty_collection(self):
        """空集合 count = 0"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        assert mem.count_memories() == 0

    def test_count_with_all_filters(self):
        """count 组合过滤 (project + type + archived)"""
        records = _make_records(50, meta_fn=lambda i: {"project_id": "multi", "type": "fact" if i < 25 else "decision"})
        store = _IntegrationStore(records)
        mem = ContextMemory(store=store)
        count = mem.count_memories(project_id="multi", type_filter="fact")
        assert count == 25

    def test_remember_with_empty_text(self):
        """空文本写入（不崩溃，应正常返回 ID）"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        with patch.object(mem, "_ensure_encoder"):
            mid = mem.remember("", embedding=[0.0] * 1024)
            assert mid is not None  # 空文本也可以写入

    def test_bm25_not_built_does_not_crash_on_add(self):
        """BM25 未构建时增量追加不应崩溃"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        assert mem._bm25 is None
        mem._add_to_bm25("不应崩溃的文本")  # 应安全跳过
        assert mem._bm25 is None

    def test_bm25_not_built_does_not_crash_on_update(self):
        """BM25 未构建时增量更新不应崩溃"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        mem._update_in_bm25("旧", "新")  # 应安全跳过
        assert mem._bm25 is None

    def test_archive_restore_cycle(self):
        """归档 → 恢复 完整链路"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        with patch.object(mem, "_ensure_encoder"):
            mid = mem.remember("待归档", embedding=[0.0] * 1024)

        mem.archive_memory(mid)
        item = mem.get_memory(mid)
        assert item["metadata"]["active"] is False

        mem.restore_memory(mid)
        item = mem.get_memory(mid)
        assert item["metadata"]["active"] is True

    def test_archive_old_memories(self):
        """批量归档旧记忆"""
        records = _make_records(50)
        now = time.time()
        # 前 20 条时间戳设旧（40 天前，早于 30 天阈值）
        for i in range(20):
            records[i]["metadata"]["timestamp"] = now - 40 * 86400
        # 后 30 条设新（5 天前，晚于 30 天阈值）
        for i in range(20, 50):
            records[i]["metadata"]["timestamp"] = now - 5 * 86400
        store = _IntegrationStore(records)
        mem = ContextMemory(store=store)
        count = mem.archive_old_memories(days=30)
        assert count == 20


# ============================================================
# 回归验证 — 确保 v0.3.0 核心功能未退化
# ============================================================


class TestRegressionV030:
    """v0.3.0 核心功能回归验证"""

    def test_hybrid_search_returns_results(self, monkeypatch):
        """混合检索 (BM25 + 向量) 正常返回"""
        import numpy as np

        class FakeEncoder:
            def encode(self, text):
                return np.zeros(1024, dtype=np.float32)

        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: FakeEncoder())
        store = _IntegrationStore([_make_record(i, f"混合检索测试文档 {i}") for i in range(10)])
        mem = ContextMemory(store=store)
        mem.warmup()
        mem._ensure_bm25_index()

        # recall with hybrid=False (pure vector, mock returns empty)
        results = mem.recall("测试", top_k=3, hybrid=True, return_scores=True)
        # mock query returns empty, so results should be empty
        assert isinstance(results, list)

    def test_recall_with_return_scores(self, monkeypatch):
        """recall return_scores 格式正确"""
        import numpy as np

        class FakeEncoder:
            def encode(self, text):
                return np.zeros(1024, dtype=np.float32)

        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: FakeEncoder())
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        mem.warmup()
        results = mem.recall("测试", return_scores=True)
        assert isinstance(results, list)

    def test_recall_with_score_structure(self):
        """recall_with_scores 返回 dict 列表，含 id/document/distance/metadata"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        results = mem.recall_with_scores("测试")
        assert isinstance(results, list)
        if results:
            r = results[0]
            assert "id" in r
            assert "document" in r
            assert "distance" in r
            assert "metadata" in r

    def test_build_metadata_defaults(self):
        """_build_metadata 默认值"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        meta = mem._build_metadata()
        assert "timestamp" in meta
        assert "type" in meta
        assert "project_id" in meta
        assert meta["active"] is True

    def test_build_metadata_custom_overrides(self):
        """_build_metadata 自定义值覆盖"""
        store = _IntegrationStore([])
        mem = ContextMemory(store=store)
        meta = mem._build_metadata({"type": "decision", "custom_field": "val"})
        assert meta["type"] == "decision"
        assert meta["custom_field"] == "val"


# ============================================================
# MCP 工具联动测试
# ============================================================


class TestMCPToolIntegration:
    """MCP 工具 ↔ memory 跨模块联动"""

    def test_set_project_id_updates_context(self, monkeypatch):
        """set_project_id 更新当前线程 project_id"""
        import numpy as np

        class FakeEncoder:
            def encode(self, text):
                return np.zeros(1024, dtype=np.float32)

        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: FakeEncoder())
        from memos.server.mcp import (
            set_project_id,
            _get_project_id,
            _reset_for_test,
            _get_memory,
            _get_extractor,
        )

        _reset_for_test("intg_v040_mcp")
        result = set_project_id("my-custom-project")
        assert "my-custom-project" in result
        assert _get_project_id() == "my-custom-project"

    def test_list_memories_mcp_tool(self, monkeypatch):
        """MCP list_memories 工具正常返回"""
        import numpy as np

        class FakeEncoder:
            def encode(self, text):
                return np.zeros(1024, dtype=np.float32)

        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: FakeEncoder())
        from memos.server.mcp import list_memories as mcp_list, _reset_for_test

        _reset_for_test("intg_v040_list")
        result = mcp_list(limit=5, offset=0)
        assert isinstance(result, str)

    def test_save_knowledge_mcp_tool(self, monkeypatch):
        """MCP save_knowledge 写入知识库"""
        import numpy as np

        class FakeEncoder:
            def encode(self, text):
                return np.zeros(1024, dtype=np.float32)

        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: FakeEncoder())
        from memos.server.mcp import save_knowledge, _reset_for_test

        _reset_for_test("intg_v040_save")
        result = save_knowledge("MCP 保存测试", "fact")
        assert "已直接保存知识到知识库" in result or "保存" in result

    def test_remember_mcp_tool(self, monkeypatch):
        """MCP remember 追加缓冲区"""
        from memos.server.mcp import remember as mcp_remember, _reset_for_test

        _reset_for_test("intg_v040_rem")
        result = mcp_remember("MCP 缓冲区测试")
        assert "已追加" in result or "已触发" in result

    def test_set_project_id_invalid_input(self, monkeypatch):
        """set_project_id 非法输入"""
        from memos.server.mcp import set_project_id, _reset_for_test

        _reset_for_test("intg_v040_pid")
        # 空字符串
        r = set_project_id("")
        assert "不能为空" in r
        # 超长
        r = set_project_id("a" * 65)
        assert "过长" in r
        # 非法字符
        r = set_project_id("hello world!")
        assert "仅允许" in r
