"""Phase 2 — F3: 模型延迟加载测试"""

import threading
import pytest

from memos.engine.memory import ContextMemory, _get_encoder, _encoder, _encoder_lock
from memos.storage.base import VectorStore


class _MinimalStore(VectorStore):
    """最小化 mock store，仅用于测试延迟加载不需要 encoder 的方法"""

    def get(self, where=None, limit=None, offset=None, include=None, ids=None):
        return {"ids": [], "documents": [], "metadatas": [], "embeddings": []}

    def query(self, query_embeddings, n_results, where=None, include=None):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def add(self, documents, embeddings, metadatas, ids):
        pass

    def update(self, ids, metadatas=None, documents=None, embeddings=None):
        pass

    def delete(self, ids):
        pass

    def count(self, where=None) -> int:
        return 0

    def vacuum(self) -> bool:
        return False


class TestLazyEncoderLoading:
    """F3.1-F3.3: 延迟加载核心逻辑"""

    def test_init_does_not_load_encoder(self, monkeypatch):
        """实例化时不应加载模型"""
        loaded = False

        def fake_get_encoder():
            nonlocal loaded
            loaded = True
            return None

        monkeypatch.setattr("memos.engine.memory._get_encoder", fake_get_encoder)
        mem = ContextMemory(store=_MinimalStore())
        assert not loaded, "实例化时不应调用 _get_encoder()"

    def test_remember_triggers_load(self, monkeypatch):
        """首次 remember() 应触发模型加载"""
        monkeypatch.setattr(
            "memos.engine.memory._get_encoder",
            lambda: _FakeEncoder(),
        )
        mem = ContextMemory(store=_MinimalStore())
        assert not mem.is_encoder_loaded
        # 简化的 remember（绕过实际 ChromaDB 写入）
        mem._ensure_encoder()
        assert mem.is_encoder_loaded

    def test_list_memories_no_encoder(self, monkeypatch):
        """list_memories() 不应触发模型加载"""
        called = False

        def fake_get_encoder():
            nonlocal called
            called = True
            return None

        monkeypatch.setattr("memos.engine.memory._get_encoder", fake_get_encoder)
        mem = ContextMemory(store=_MinimalStore())
        result = mem.list_memories()
        assert result == []
        assert not called, "list_memories 不需要 encoder，不应触发加载"

    def test_count_memories_no_encoder(self, monkeypatch):
        """count_memories() 不应触发模型加载"""
        called = False

        def fake_get_encoder():
            nonlocal called
            called = True
            return None

        monkeypatch.setattr("memos.engine.memory._get_encoder", fake_get_encoder)
        mem = ContextMemory(store=_MinimalStore())
        count = mem.count_memories()
        assert count == 0
        assert not called

    def test_delete_memory_no_encoder(self, monkeypatch):
        """delete_memory() 不应触发模型加载"""
        called = False

        def fake_get_encoder():
            nonlocal called
            called = True
            return None

        monkeypatch.setattr("memos.engine.memory._get_encoder", fake_get_encoder)
        mem = ContextMemory(store=_MinimalStore())
        # delete nonexistent — 不应触发 encoder 加载
        try:
            mem.delete_memory("nonexistent")
        except Exception:
            pass
        assert not called


class _FakeEncoder:
    """假 encoder，不加载真实模型"""

    def encode(self, text):
        import numpy as np

        return np.zeros(1024, dtype=np.float32)


class TestWarmupAndIsLoaded:
    """F3.4-F3.5: warmup 和 is_encoder_loaded"""

    def test_warmup_loads_encoder(self, monkeypatch):
        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: _FakeEncoder())
        mem = ContextMemory(store=_MinimalStore())
        assert not mem.is_encoder_loaded
        mem.warmup()
        assert mem.is_encoder_loaded

    def test_double_warmup_safe(self, monkeypatch):
        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: _FakeEncoder())
        mem = ContextMemory(store=_MinimalStore())
        mem.warmup()
        mem.warmup()  # 不应报错
        assert mem.is_encoder_loaded

    def test_ensure_encoder_double_check(self, monkeypatch):
        """双重检查锁：多线程同时调用仅加载一次"""
        call_count = 0

        def counting_encoder():
            nonlocal call_count
            call_count += 1
            return _FakeEncoder()

        monkeypatch.setattr("memos.engine.memory._get_encoder", counting_encoder)
        mem = ContextMemory(store=_MinimalStore())

        def worker():
            mem._ensure_encoder()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count == 1, f"双重检查锁失效，_get_encoder 被调用 {call_count} 次"


class TestDashboardWarmup:
    """F3.6: Dashboard 启动时预热"""

    def test_lifespan_warmup_called(self, monkeypatch):
        """验证 lifespan 中调用了 warmup"""
        monkeypatch.setattr("memos.engine.memory._get_encoder", lambda: _FakeEncoder())

        from memos.web.app import lifespan, app
        import asyncio

        # 创建新的 lifespan 上下文，但不真正启动服务器
        async def run_lifespan():
            async with lifespan(app):
                assert app.state.mem.is_encoder_loaded

        asyncio.run(run_lifespan())
