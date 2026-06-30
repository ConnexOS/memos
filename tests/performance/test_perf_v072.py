"""v0.7.2 性能压力测试（10K / 50K 记忆规模）。

测试项：
  - test_recall_latency       recall 冷/热缓存响应 < 3s / < 1s
  - test_list_memories_pagination 翻页响应 < 500ms
  - test_write_throughput     写入吞吐 > 100 条/s
  - test_bm25_rebuild         BM25 惰性重建 < 3s
  - test_sse_latency          EventBus touch → consume < 1s
"""

import random
import time
import tempfile
import pytest

from memos.config import config as _config
from memos.engine.memory import ContextMemory
from memos.storage.chroma import ChromaDBPersistentStore


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(scope="module")
def perf_memory():
    """提供空着的新 ContextMemory 实例（使用临时 ChromaDB 目录）。

    模块级共享，所有测试复用同一存储。teardown 时删除临时数据。
    """
    tmp = tempfile.mkdtemp(prefix="memos-perf-")
    original_path = _config.chroma.path
    _config.chroma.path = tmp
    try:
        store = ChromaDBPersistentStore("test_perf")
        mem = ContextMemory(store=store)
        yield mem
    finally:
        _config.chroma.path = original_path
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def seeded_memory(perf_memory):
    """在 perf_memory 基础上预填充 10K 条测试记忆。

    使用预计算随机嵌入，跳过 SentenceTransformer 编码开销，
    仅测试存储/检索路径。
    """
    dim = _config.model.vector_dim  # 1024
    count = 10000
    for i in range(count):
        emb = [random.random() for _ in range(dim)]
        perf_memory.remember(
            f"测试记忆 {i}",
            metadata={"type": "solution", "content": f"性能测试数据 #{i}"},
            embedding=emb,
        )
    return perf_memory


# ============================================================
# Tests
# ============================================================


def test_recall_latency(seeded_memory):
    """recall 响应时间测试（10K 规模）。

    冷缓存：首次 recall 触发 BM25 惰性重建 + 向量检索，预期 < 3s。
    热缓存：BM25 和向量索引均已就绪，预期 < 1s。
    """
    # 冷缓存：BM25 尚未构建
    t0 = time.perf_counter()
    results = seeded_memory.recall("测试", top_k=5)
    elapsed = time.perf_counter() - t0

    assert elapsed < 3.0, (
        f"recall 冷缓存耗时 {elapsed:.2f}s > 3s 阈值"
    )
    assert len(results) > 0, "recall 应返回结果"

    # 热缓存：BM25 + 向量索引均已就绪
    t0 = time.perf_counter()
    results = seeded_memory.recall("测试", top_k=5)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, (
        f"recall 热缓存耗时 {elapsed:.2f}s > 1s 阈值"
    )


def test_list_memories_pagination(seeded_memory):
    """list_memories 翻页基准测试（10K 规模下查询第 1 页）。"""
    t0 = time.perf_counter()
    results = seeded_memory.list_memories(
        type_filter="solution", limit=20, offset=0
    )
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.5, (
        f"list_memories 翻页耗时 {elapsed:.2f}s > 500ms 阈值"
    )
    assert len(results) > 0, "应返回分页结果"


def test_write_throughput(perf_memory):
    """写入吞吐测试：100 条连续写入，吞吐 > 100 条/s。

    使用预计算随机嵌入跳过编码开销，仅衡量 ChromaDB 写入路径。
    """
    dim = _config.model.vector_dim
    count = 100
    texts = [f"批量写入测试 {i}" for i in range(count)]

    t0 = time.perf_counter()
    for t in texts:
        emb = [random.random() for _ in range(dim)]
        perf_memory.remember(t, metadata={"type": "decision"}, embedding=emb)
    elapsed = time.perf_counter() - t0

    throughput = count / elapsed
    assert throughput > 100, (
        f"写入吞吐 {throughput:.0f} 条/s < 100 条/s 阈值"
    )


def test_bm25_rebuild(seeded_memory):
    """BM25 惰性重建基准测试（10K 规模）。

    显式失效 BM25 索引，然后通过 recall 间接触发重建，
    测量从失效到完成重建的总耗时。
    """
    # 失效 BM25 确保触发重建
    seeded_memory._invalidate_bm25()

    t0 = time.perf_counter()
    results = seeded_memory.recall("触发 BM25 重建", top_k=5)
    elapsed = time.perf_counter() - t0

    assert elapsed < 3.0, (
        f"BM25 惰性重建耗时 {elapsed:.2f}s > 3s 阈值"
    )
    assert len(results) > 0, "recall 应返回结果"


def test_sse_latency():
    """SSE 推送延迟基准测试（EventBus touch → consume 延迟）。

    衡量 touch_event() 写入 + get_event_timestamp() 读取的完整
    往返延迟，模拟 Dashboard SSE 轮询路径。
    """
    from memos.features.event_bus import touch_event, get_event_timestamp

    t0 = time.perf_counter()
    touch_event("memory_stream")
    ts = get_event_timestamp("memory_stream")
    elapsed = time.perf_counter() - t0

    assert ts > 0, "SSE 事件时间戳未更新"
    latency_ms = elapsed * 1000
    assert latency_ms < 1000, (
        f"SSE 推送延迟 {latency_ms:.1f}ms > 1s 阈值"
    )
