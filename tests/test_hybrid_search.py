from memos.engine.memory import ContextMemory

from tests.conftest import clean_collection

COLLECTION = "test_hybrid"


class TestHybridBackwardCompat:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)
        self.mem.remember("Python后端使用FastAPI框架")
        self.mem.remember("数据库使用PostgreSQL")

    def test_hybrid_false_works_as_before(self):
        r1 = self.mem.recall("FastAPI", top_k=5)
        r2 = self.mem.recall("FastAPI", top_k=5, hybrid=False)
        assert r1 == r2
        assert len(r1) > 0

    def test_recall_without_hybrid_no_bm25_index(self):
        self.mem.recall("FastAPI", top_k=5, hybrid=False)
        assert self.mem._bm25 is None


class TestHybridSearch:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_hybrid_returns_results(self):
        self.mem.remember("Python后端使用FastAPI框架")
        results = self.mem.recall("FastAPI", top_k=5, hybrid=True)
        assert len(results) > 0

    def test_hybrid_empty_corpus(self):
        results = self.mem.recall("anything", top_k=3, hybrid=True)
        assert results == []

    def test_hybrid_with_bm25_weight_param(self):
        self.mem.remember("Python后端使用FastAPI框架")
        self.mem.remember("数据库使用PostgreSQL")
        r = self.mem.recall("FastAPI框架", top_k=5, hybrid=True, bm25_weight=0.5)
        assert len(r) > 0

    def test_hybrid_combined_with_decay(self):
        self.mem.remember("快被遗忘的旧记忆", {"type": "fact"})
        self.mem.remember("新的记忆内容", {"type": "fact"})
        r = self.mem.recall("记忆", top_k=5, hybrid=True, decay_lambda=0.02)
        assert len(r) >= 2

    def test_hybrid_with_filter(self):
        self.mem.remember("项目A的FastAPI", {"type": "decision", "project_id": "proj_a"})
        self.mem.remember("项目B的FastAPI", {"type": "decision", "project_id": "proj_b"})
        r = self.mem.recall("FastAPI", top_k=5, hybrid=True, project_id="proj_a")
        assert "项目A的FastAPI" in r
        assert "项目B的FastAPI" not in r


class TestBm25IndexInvalidation:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_bm25_built_on_first_hybrid_call(self):
        self.mem.remember("第一条记忆")
        assert self.mem._bm25 is None
        self.mem.recall("记忆", top_k=5, hybrid=True)
        assert self.mem._bm25 is not None

    def test_bm25_rebuilt_after_remember(self):
        """v0.4.0: remember() 增量追加 BM25，同一对象但 corpus_size 增长"""
        self.mem.remember("记忆A")
        self.mem.recall("记忆", top_k=5, hybrid=True)
        old_size = self.mem._bm25.corpus_size
        self.mem.remember("记忆B")
        self.mem.recall("记忆", top_k=5, hybrid=True)
        assert self.mem._bm25.corpus_size == old_size + 1

    def test_bm25_rebuilt_after_delete(self):
        id1 = self.mem.remember("记忆A")
        self.mem.remember("记忆B")
        self.mem.recall("记忆", top_k=5, hybrid=True)
        old_bm25 = self.mem._bm25
        self.mem.delete_memory(id1)
        self.mem.recall("记忆", top_k=5, hybrid=True)
        # 删除触发 invalidate → 新对象
        assert self.mem._bm25 is not old_bm25

    def test_bm25_rebuilt_after_update(self):
        """v0.4.0: remember() 增量追加，BM25 同一对象增长"""
        self.mem.remember("记忆A")
        self.mem.recall("记忆", top_k=5, hybrid=True)
        old_size = self.mem._bm25.corpus_size
        self.mem.remember("记忆B")
        self.mem.recall("记忆", top_k=5, hybrid=True)
        assert self.mem._bm25.corpus_size == old_size + 1

    def test_bm25_unchanged_without_writes(self):
        self.mem.remember("记忆A")
        self.mem.recall("记忆", top_k=5, hybrid=True)
        bm25_a = self.mem._bm25
        self.mem.recall("记忆", top_k=5, hybrid=True)
        assert self.mem._bm25 is bm25_a
