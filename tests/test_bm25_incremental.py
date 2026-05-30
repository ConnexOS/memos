"""Phase 4 — F4: BM25 索引增量更新测试"""

import pytest
from unittest.mock import patch, MagicMock
from rank_bm25 import BM25Okapi

from memos.engine.memory import ContextMemory


class _BM25Store:
    """最小化 mock store，支持 BM25 测试需要的 get() 操作"""

    def __init__(self, docs=None):
        self._docs = list(docs or [])
        self._metas = [{"active": True, "timestamp": 1_700_000_000 - i * 3600} for i in range(len(self._docs))]

    def get(self, where=None, limit=None, offset=None, include=None, ids=None):
        if ids is not None:
            result = {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
            for i, d in enumerate(self._docs):
                rid = f"rec_{i:04d}"
                if rid in ids:
                    result["ids"].append(rid)
                    result["documents"].append(d)
                    result["metadatas"].append(self._metas[i])
                    result["embeddings"].append(None)
            return result
        # Full get for _ensure_bm25_index
        result = {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
        for i, d in enumerate(self._docs):
            result["ids"].append(f"rec_{i:04d}")
            result["documents"].append(d)
            result["metadatas"].append(self._metas[i])
            result["embeddings"].append(None)
        return result

    def count(self, where=None):
        return len(self._docs)

    def query(self, *a, **kw):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def add(self, documents, embeddings, metadatas, ids):
        for d, m, i in zip(documents, metadatas, ids):
            self._docs.append(d)
            self._metas.append(m)

    def update(self, ids, metadatas=None, documents=None, embeddings=None):
        pass

    def delete(self, ids):
        pass

    def vacuum(self) -> bool:
        return False

    supports_offset = True


def _tokenize(text):
    import re

    return re.findall(r"\w+", text.lower())


class TestBM25Incremental:
    """F4 核心: 增量追加/更新/删除"""

    def test_add_to_bm25(self):
        """增量追加后 BM25 内部状态正确：corpus_size/doc_len/doc_freqs 更新"""
        mem = ContextMemory(store=_BM25Store(["hello world", "foo bar", "hello memos"]))
        mem._ensure_bm25_index()

        # 增量追加
        mem._add_to_bm25("hello world incremental test")

        # BM25 内部状态验证（增量在内存中，不依赖 store）
        assert mem._bm25.corpus_size == 4
        assert len(mem._bm25.doc_len) == 4
        assert len(mem._bm25.doc_freqs) == 4
        assert "incremental" in mem._bm25.idf  # 新词应有 IDF 值

    def test_add_when_not_built(self):
        """BM25 未构建时增量追加应安全跳过（不 crash）"""
        mem = ContextMemory(store=_BM25Store([]))
        assert mem._bm25 is None
        mem._add_to_bm25("some text")  # 不应报错
        assert mem._bm25 is None

    def test_update_in_bm25(self):
        """增量更新后文档内容应反映新文本"""
        mem = ContextMemory(store=_BM25Store(["hello world", "old content", "another doc"]))
        mem._ensure_bm25_index()

        mem._update_in_bm25("old content", "new content here")
        # 确认文档列表已更新
        assert "new content here" in mem._bm25_docs
        assert "old content" not in mem._bm25_docs
        assert mem._bm25.corpus_size == 3  # 数量不变

    def test_update_nonexistent_falls_back(self):
        """更新不存在的文档 → 触发全量失效"""
        mem = ContextMemory(store=_BM25Store(["hello world"]))
        mem._ensure_bm25_index()
        assert mem._bm25 is not None
        mem._update_in_bm25("nonexistent doc", "new doc")
        assert mem._bm25 is None  # 找不到 → 全量失效

    def test_remove_triggers_invalidate(self):
        """删除文档 → 全量失效（删除场景低频，invalidate 最安全）"""
        mem = ContextMemory(store=_BM25Store(["hello world", "foo bar"]))
        mem._ensure_bm25_index()
        assert mem._bm25 is not None
        mem._remove_from_bm25("hello world")
        assert mem._bm25 is None

    def test_thread_safety(self):
        """并发追加不 crash"""
        import threading

        mem = ContextMemory(store=_BM25Store(["initial doc"]))
        mem._ensure_bm25_index()

        def worker(i):
            for _ in range(50):
                mem._add_to_bm25(f"thread worker {i} document text")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证一致性
        assert mem._bm25 is not None
        assert mem._bm25.corpus_size == 1 + 4 * 50  # 1 initial + 200 added


class TestBM25VsFullRebuild:
    """增量 vs 全量重建一致性验证"""

    def test_incremental_matches_full_rebuild(self):
        """增量追加与全量重建（同一批文档）的 BM25 分数完全一致"""
        docs = [f"document {i} contains unique terms and some common words for testing" for i in range(100)]

        # 增量构建：3 条初始 + 97 条增量
        mem1 = ContextMemory(store=_BM25Store(docs[:3]))
        mem1._ensure_bm25_index()
        for d in docs[3:]:
            mem1._add_to_bm25(d)
        inc_scores = mem1._bm25.get_scores(_tokenize("unique testing common"))

        # 全量构建：100 条一次性
        mem2 = ContextMemory(store=_BM25Store(docs))
        mem2._ensure_bm25_index()
        full_scores = mem2._bm25.get_scores(_tokenize("unique testing common"))

        # 每项分数应完全一致
        assert len(inc_scores) == len(full_scores) == 100
        for i, (a, b) in enumerate(zip(inc_scores, full_scores)):
            assert abs(a - b) < 0.0001, f"文档 {i} 分数不一致: {a} vs {b}"
