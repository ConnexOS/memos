"""
MEMOS 单机版压力测试套件 (Phase 7 / F4)
============================================
支持场景:
  1. 数据生成并写入  2. 纯向量检索  3. 混合检索
  4. 时间衰减检索    5. 复用加成检索  6. 列表查询
  7. 单条/批量写入   8. 并发写入      9. 内存监控

用法:
  python scripts/benchmark.py                          # 检查数据 → 生成(如不足) → 跑基准 → 输出报告
  python scripts/benchmark.py --count 10000            # 指定目标规模
  python scripts/benchmark.py --generate-only          # 仅生成数据
  python scripts/benchmark.py --bench-only             # 仅跑基准(数据已存在)
  python scripts/benchmark.py --collection bench_mem   # 指定 collection 名
"""

import argparse
import json
import logging
import os
import random
import sys
import threading
import time
import traceback
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark")


# ── 测试数据模板 ──────────────────────────────────────────────
TOPICS = [
    "使用FastAPI构建RESTful API接口",
    "PostgreSQL数据库表结构设计与索引优化",
    "React前端组件化开发实践",
    "Docker容器化部署方案",
    "Redis缓存策略与过期时间设置",
    "Celery异步任务队列管理",
    "Nginx反向代理与负载均衡配置",
    "Elasticsearch全文搜索实现",
    "Kubernetes集群管理与Pod调度",
    "Python异步编程与asyncio使用",
    "Git工作流与分支管理策略",
    "JWT认证与权限控制实现",
    "SQLAlchemy ORM查询优化",
    "Pytest测试框架与Fixture管理",
    "日志收集与分析体系搭建",
    "Prometheus监控与告警规则",
    "消息队列RabbitMQ使用实践",
    "WebSocket实时通信方案",
    "CI/CD流水线与自动化部署",
    "代码审查规范与质量门禁",
]

TYPES = ["fact", "decision", "preference", "todo"]

PROJECTS = ["project-alpha", "project-beta", "project-gamma", "project-delta"]

# ── 辅助 ──────────────────────────────────────────────────────


def _generate_content(idx: int) -> str:
    topic = TOPICS[idx % len(TOPICS)]
    variations = [
        f"团队决定{topic}",
        f"在项目中使用{topic}",
        f"讨论了{topic}的实施方案",
        f"优化了{topic}的相关配置",
        f"解决了{topic}中遇到的性能问题",
        f"总结了{topic}的最佳实践",
        f"记录了{topic}的技术选型过程",
        f"完成了{topic}的代码实现",
        f"修复了{topic}相关的Bug",
        f"重构了{topic}的代码结构",
    ]
    return variations[idx % len(variations)] + "，为后续开发提供了参考依据。"


def _generate_metadata(idx: int, timestamp: float) -> dict:
    return {
        "type": TYPES[idx % len(TYPES)],
        "project_id": PROJECTS[idx % len(PROJECTS)],
        "timestamp": timestamp,
        "quality_score": round(random.uniform(0.3, 1.0), 2),
        "reuse_count": random.randint(0, 20),
        "last_reused_at": timestamp - random.randint(0, 86400 * 30),
    }


def _load_psutil():
    """尝试加载 psutil，失败返回 None"""
    try:
        import psutil

        return psutil
    except ImportError:
        return None


# ── 场景运行器 ──────────────────────────────────────────────


class BenchmarkRunner:
    """封装所有测试场景"""

    def __init__(self, collection_name: str, target_count: int, psutil_mod=None):
        self.collection = collection_name
        self.target = target_count
        self.psutil = psutil_mod
        self.mem = None
        self._results = {}
        self._env_info = {}

    # --- 生命周期 ---

    def setup(self):
        from memos.engine.memory import ContextMemory

        logger.info("初始化 ContextMemory, collection=%s", self.collection)
        self.mem = ContextMemory(collection_name=self.collection)
        self.mem.warmup()

        # 收集环境信息
        import platform

        self._env_info = {
            "os": platform.platform(),
            "python": sys.version,
            "collection": self.collection,
            "target_count": self.target,
            "timestamp": datetime.now().isoformat(),
        }
        try:
            from memos._version import __version__

            self._env_info["memos_version"] = __version__
        except Exception:
            pass
        logger.info("环境信息: %s", json.dumps(self._env_info, ensure_ascii=False))

    def teardown(self):
        """清理临时 collection"""
        if self.mem:
            try:
                all_ids = self.mem.store.get()["ids"]
                if all_ids:
                    self.mem.store.delete(ids=all_ids)
                    logger.info("已清理 %d 条记忆", len(all_ids))
            except Exception as e:
                logger.warning("清理失败: %s", e)

    # --- 数据生成 ---

    def ensure_data(self) -> int:
        """生成到 target_count 条，返回实际条数"""
        existing = self._count()
        logger.info("当前数据: %d, 目标: %d", existing, self.target)
        if existing >= self.target:
            return existing

        need = self.target - existing
        batch_size = 100
        now = time.time()
        logger.info("需要生成 %d 条数据 (批次=%d)", need, batch_size)

        t0 = time.time()
        generated = 0
        for start in range(0, need, batch_size):
            end = min(start + batch_size, need)
            batch = []
            for i in range(start, end):
                idx = existing + i
                ts = now - random.randint(0, 86400 * 60)  # 60天内随机
                content = _generate_content(idx)
                meta = _generate_metadata(idx, ts)
                batch.append((content, meta))
            for content, meta in batch:
                self.mem.remember(content, metadata=meta)
            generated += len(batch)
            if generated % 1000 == 0:
                logger.info("  已生成 %d / %d (%.1f%%)", generated, need, generated / need * 100)

        elapsed = time.time() - t0
        actual = self._count()
        logger.info("数据生成完成: %d 条, 耗时 %.1fs (%.0f 条/s)", actual, elapsed, actual / elapsed if elapsed else 0)
        return actual

    def _count(self) -> int:
        try:
            return self.mem.list_memories(limit=1, include_archived=True)["total"]
        except Exception:
            return 0

    # --- 内存采样 ---

    def _sample_memory(self, label: str):
        if not self.psutil:
            return None
        try:
            proc = self.psutil.Process(os.getpid())
            rss = proc.memory_info().rss / (1024 * 1024)
            logger.info("  内存 [%s]: %.1f MB", label, rss)
            return {"label": label, "rss_mb": round(rss, 1)}
        except Exception:
            return None

    # --- 场景: 检索 ---

    def bench_retrieval(self, scales: list[int]):
        """纯向量检索"""
        logger.info("\n=== 场景: 纯向量检索 (top_k=5) ===")
        queries = [_generate_content(i * 37) for i in range(min(10, self.target))]

        for scale in scales:
            if scale > self.target:
                continue
            latencies = []
            for q in queries:
                t0 = time.perf_counter()
                self.mem.recall(q, top_k=5, days_limit=0)
                latencies.append((time.perf_counter() - t0) * 1000)
            self._record("retrieval_vector", scale, latencies)

    def bench_hybrid_retrieval(self, scales: list[int]):
        """混合检索"""
        logger.info("\n=== 场景: 混合检索 (top_k=5, bm25_weight=0.3) ===")
        queries = [_generate_content(i * 37) for i in range(min(10, self.target))]

        for scale in scales:
            if scale > self.target:
                continue
            latencies = []
            for q in queries:
                t0 = time.perf_counter()
                self.mem.recall(q, top_k=5, hybrid=True, bm25_weight=0.3)
                latencies.append((time.perf_counter() - t0) * 1000)
            self._record("retrieval_hybrid", scale, latencies)

    def bench_decay_retrieval(self, scales: list[int]):
        """时间衰减检索"""
        logger.info("\n=== 场景: 时间衰减检索 (top_k=5, decay=0.01) ===")
        queries = [_generate_content(i * 37) for i in range(min(10, self.target))]

        for scale in scales:
            if scale > self.target:
                continue
            latencies = []
            for q in queries:
                t0 = time.perf_counter()
                self.mem.recall(q, top_k=5, days_limit=30)
                latencies.append((time.perf_counter() - t0) * 1000)
            self._record("retrieval_decay", scale, latencies)

    # --- 场景: 列表 ---

    def bench_list(self, scales: list[int]):
        """列表查询"""
        logger.info("\n=== 场景: 列表查询 ===")

        for scale in scales:
            if scale > self.target:
                continue
            # limit=20
            lat20 = []
            for _ in range(50):
                t0 = time.perf_counter()
                self.mem.list_memories(limit=20)
                lat20.append((time.perf_counter() - t0) * 1000)
            self._record("list_limit20", scale, lat20)

            # limit=100
            lat100 = []
            for _ in range(50):
                t0 = time.perf_counter()
                self.mem.list_memories(limit=100)
                lat100.append((time.perf_counter() - t0) * 1000)
            self._record("list_limit100", scale, lat100)

    # --- 场景: 写入 ---

    def bench_write(self):
        """单条写入"""
        logger.info("\n=== 场景: 单条写入 ===")
        latencies = []
        for i in range(100):
            content = f"基准测试写入 {i} " + _generate_content(i)
            t0 = time.perf_counter()
            self.mem.remember(content, metadata={"type": "fact", "project_id": "bench"})
            latencies.append((time.perf_counter() - t0) * 1000)
        self._record("write_single", self.target, latencies)
        self._record_simple("write_single_ops", 100 / (sum(latencies) / 1000 / len(latencies)))

    def bench_batch_write(self):
        """批量写入 (循环内单条，模拟实际使用模式)"""
        logger.info("\n=== 场景: 批量写入 (100条) ===")
        batch_times = []
        for batch in range(5):
            t0 = time.perf_counter()
            for i in range(100):
                content = f"批量写入 batch{batch} item{i} " + _generate_content(i)
                self.mem.remember(content, metadata={"type": "fact", "project_id": "bench"})
            batch_times.append((time.perf_counter() - t0) * 1000)
        self._record("write_batch100", self.target, batch_times)

    # --- 场景: 并发 ---

    def bench_concurrent_write(self):
        """并发写入 (4线程 × 25条)"""
        logger.info("\n=== 场景: 并发写入 (4线程×25条) ===")
        barrier = threading.Barrier(4)
        results = []
        lock = threading.Lock()

        def _worker(wid: int):
            barrier.wait()
            local_times = []
            for i in range(25):
                content = f"并发写入 worker{wid} item{i}"
                t0 = time.perf_counter()
                try:
                    self.mem.remember(content, metadata={"type": "fact", "project_id": "bench"})
                    local_times.append((time.perf_counter() - t0) * 1000)
                except Exception:
                    pass
            with lock:
                results.extend(local_times)

        threads = [threading.Thread(target=_worker, args=(w,)) for w in range(4)]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total_ms = (time.perf_counter() - t0) * 1000

        self._record("concurrent_write", self.target, results)
        self._record_simple("concurrent_write_total_ms", round(total_ms, 1))

    # --- 记录 ---

    def _record(self, scenario: str, scale: int, latencies_ms: list[float]):
        if not latencies_ms:
            return
        sorted_lats = sorted(latencies_ms)
        n = len(sorted_lats)
        p50 = sorted_lats[int(n * 0.5)]
        p95 = sorted_lats[int(n * 0.95)]
        p99 = sorted_lats[int(n * 0.99)]
        avg = sum(sorted_lats) / n
        key = f"{scenario}@{scale}"
        self._results[key] = {
            "scenario": scenario,
            "scale": scale,
            "count": n,
            "avg_ms": round(avg, 2),
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "min_ms": round(sorted_lats[0], 2),
            "max_ms": round(sorted_lats[-1], 2),
        }
        logger.info(
            "  %s@%d: avg=%.1f p50=%.1f p95=%.1f p99=%.1f (n=%d)",
            scenario,
            scale,
            avg,
            p50,
            p95,
            p99,
            n,
        )

    def _record_simple(self, key: str, value):
        self._results[key] = value

    # --- 报告 ---

    def generate_report(self, output_dir: str = "document/42版本") -> dict:
        """生成 JSON + Markdown 报告"""
        memory_samples = []

        # 各规模内存采样
        for scale in [1000, 5000, 10000, 50000]:
            if scale > self.target:
                continue
            s = self._sample_memory(f"{scale}条")
            if s:
                memory_samples.append(s)

        report = {
            "env": self._env_info,
            "memory_samples": memory_samples,
            "results": {k: v for k, v in self._results.items() if isinstance(v, dict)},
            "summary": self._results,
            "generated_at": datetime.now().isoformat(),
        }

        # JSON
        json_path = os.path.join(output_dir, "benchmark_report.json")
        os.makedirs(output_dir, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info("JSON 报告已写入: %s", json_path)

        # Markdown
        md_path = os.path.join(output_dir, "benchmark_report.md")
        self._write_markdown(md_path, report, memory_samples)
        logger.info("Markdown 报告已写入: %s", md_path)

        return report

    def _write_markdown(self, path: str, report: dict, memory_samples: list):
        lines = []
        lines.append("# MEMOS v0.4.2 压力测试报告\n")
        lines.append(f"**生成时间**: {report['generated_at']}\n")
        lines.append(f"**环境**: {report['env'].get('os', 'N/A')}\n")
        lines.append(f"**Python**: {report['env'].get('python', 'N/A')}\n")
        lines.append(f"**Collection**: {report['env'].get('collection', 'N/A')}\n")
        lines.append(f"**目标规模**: {report['env'].get('target_count', 'N/A')}\n\n")

        # 内存
        lines.append("---\n## 内存占用\n\n")
        lines.append("| 规模 | RSS (MB) |\n|------|----------|\n")
        for s in memory_samples:
            lines.append(f"| {s['label']} | {s['rss_mb']} |\n")

        # 场景分组
        lines.append("\n---\n## 检索场景延迟 (ms)\n\n")
        lines.append("| 场景 | 规模 | 次数 | P50 | P95 | P99 | 平均 |\n")
        lines.append("|------|------|------|-----|-----|-----|------|\n")

        retrieval_keys = [k for k in report["results"] if "retrieval" in k]
        for key in sorted(retrieval_keys):
            r = report["results"][key]
            lines.append(
                f"| {r['scenario']} | {r['scale']} | {r['count']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} | {r['avg_ms']} |\n"
            )

        lines.append("\n---\n## 列表查询延迟 (ms)\n\n")
        lines.append("| 场景 | 规模 | 次数 | P50 | P95 | P99 | 平均 |\n")
        lines.append("|------|------|------|-----|-----|-----|------|\n")
        list_keys = [k for k in report["results"] if "list" in k]
        for key in sorted(list_keys):
            r = report["results"][key]
            lines.append(
                f"| {r['scenario']} | {r['scale']} | {r['count']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} | {r['avg_ms']} |\n"
            )

        lines.append("\n---\n## 写入场景\n\n")
        lines.append("| 场景 | 规模 | 次数 | P50 | P95 | P99 | 平均 |\n")
        lines.append("|------|------|------|-----|-----|-----|------|\n")
        write_keys = [k for k in report["results"] if "write" in k or "concurrent" in k]
        for key in sorted(write_keys):
            r = report["results"][key]
            lines.append(
                f"| {r['scenario']} | {r['scale']} | {r['count']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} | {r['avg_ms']} |\n"
            )

        lines.append("\n---\n## 瓶颈分析与优化建议\n\n")
        lines.append("> 以下分析基于报告数据，待基准运行后人工标注。\n\n")
        lines.append("| 场景 | 是否达标 | 瓶颈分析 | 建议 |\n")
        lines.append("|------|---------|---------|------|\n")
        lines.append("| 纯向量检索 (50K) | - | 待分析 | - |\n")
        lines.append("| 混合检索 (50K) | - | 待分析 | - |\n")
        lines.append("| 列表查询 (50K) | - | 待分析 | - |\n")
        lines.append("| 单条写入 | - | 待分析 | - |\n")
        lines.append("| 批量写入 | - | 待分析 | - |\n")
        lines.append("| 并发写入 | - | 待分析 | - |\n")
        lines.append("| 内存占用 (50K) | - | 待分析 | - |\n")

        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)


# ── CLI 入口 ──────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="MEMOS 单机版压力测试")
    parser.add_argument("--count", type=int, default=5000, help="目标记忆数量 (默认 5000)")
    parser.add_argument(
        "--collection", default=None, help="ChromaDB collection 名 (默认 bench_YYYYMMDD_HHMMSS)"
    )
    parser.add_argument("--generate-only", action="store_true", help="仅生成数据，不跑基准")
    parser.add_argument("--bench-only", action="store_true", help="仅跑基准，不生成数据")
    parser.add_argument("--output", default="document/42版本", help="报告输出目录")
    parser.add_argument("--skip-cleanup", action="store_true", help="跑完后不清除数据")
    args = parser.parse_args()

    if args.collection:
        collection = args.collection
    else:
        collection = f"bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    psutil_mod = _load_psutil()
    if psutil_mod:
        logger.info("psutil 可用，将监控内存")
    else:
        logger.info("psutil 不可用，跳过内存监控 (pip install psutil)")

    runner = BenchmarkRunner(collection, args.count, psutil_mod)
    runner.setup()

    try:
        # 数据生成
        if not args.bench_only:
            actual = runner.ensure_data()
            if args.generate_only:
                logger.info("仅生成模式完成，共 %d 条", actual)
                return

        actual_count = runner._count()
        logger.info("基准测试启动: %d 条记忆", actual_count)

        # 确定测试规模挡位
        scales = [1000, 5000, 10000, 50000]
        scales = [s for s in scales if s <= actual_count]

        # 预热
        logger.info("预热...")
        for _ in range(10):
            runner.mem.recall("预热查询", top_k=5)

        # 运行场景
        runner.bench_retrieval(scales)
        runner.bench_hybrid_retrieval(scales)
        runner.bench_decay_retrieval(scales)
        runner.bench_list(scales)
        runner.bench_write()
        runner.bench_batch_write()
        runner.bench_concurrent_write()

        # 报告
        runner.generate_report(args.output)
        logger.info("基准测试完成！")

    finally:
        if not args.skip_cleanup and not args.bench_only:
            runner.teardown()


if __name__ == "__main__":
    main()
