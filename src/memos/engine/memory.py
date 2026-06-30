import inspect
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from .._version import __version__
from ..config import config
from ..errors import ChromaDBError

# v0.6.0 新 6 类（L2+L3）
V060_KNOWLEDGE_TYPES = {"task", "briefing", "solution", "decision", "lesson", "process"}

# 全量可检索类型（含旧 7 类兼容，确保向后兼容）
ALL_RECALL_TYPES = V060_KNOWLEDGE_TYPES | {
    "fact", "preference", "bug_fix", "feature_design", "code_optimize", "tech_knowledge",
}

# F5 source 规范值域（6 种 + 3 种过渡兼容）
ALLOWED_SOURCES = {
    "stop_hook",      # Stop Hook 自评采集
    "manual",         # 用户手工追溯/创建
    "user_instructed", # Claude Code 自写（save_knowledge）
    "mcp",            # MCP 工具创建
    "scheduler",      # Dashboard 定时调度
    "lazy_hook",      # Hook 兜底生成
    "remember",       # remember() MCP 工具
    # 过渡期兼容值（v0.8.0 评估移除）
    "auto_extracted", # 旧提取管线
    "watchlist_conversion", # 待关注→知识转换
}

# inactive_reason 枚举常量（F5）
INACTIVE_REASON_VALUES = {
    "manual_forget",
    "obsolete",
    "auto_archived",
    "manual_archive",
}

# 限制 PyTorch 线程数，防止 Windows 上 safetensors 多线程加载导致内存访问冲突 (access violation)
# macOS/Linux 不限线程，避免不必要地降低 torch 并行性能
if os.name == "nt":
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("SAFETENSORS_FAST_LOAD", "0")

from rank_bm25 import BM25Okapi  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

from ..storage.base import VectorStore  # noqa: E402
from ..storage.chroma import create_store  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_DECAY_LAMBDA = config.memory.decay_lambda


# 去重阈值按向量维度精确映射（v0.4.0 MED-2 修复：避免 <=384 范围过宽）
_THRESHOLD_MAP = {1024: 0.55, 384: 0.65}


def _get_similarity_threshold() -> float:
    """按向量维度自适应去重阈值：1024维=0.55, 384维=0.65。未知维度回退到 config 默认值。"""
    dim = config.model.vector_dim
    if dim in _THRESHOLD_MAP:
        return _THRESHOLD_MAP[dim]
    logger.warning("未知向量维度 %d，使用 config 默认阈值 %.2f", dim, config.memory.similarity_threshold)
    return config.memory.similarity_threshold


# 模块级常量 — 已废弃，仅保留向后兼容。v0.5.0 将移除。新代码请使用 _get_similarity_threshold()
SIMILARITY_THRESHOLD = config.memory.similarity_threshold

_encoder = None
_encoder_lock = threading.Lock()


def _get_encoder() -> SentenceTransformer:
    """模块级单例，双重检查锁避免重复加载 1.3GB 模型权重导致 Windows 内存耗尽 (access violation)。"""
    global _encoder
    if _encoder is None:
        with _encoder_lock:
            if _encoder is None:
                _encoder = SentenceTransformer(config.model.path)
    return _encoder


class ContextMemory:
    # 分页查询最大加载量：超过此值记录日志但不阻塞（防止 OOM）
    _LIST_MAX_LOAD = 5000

    def __init__(self, collection_name: str = None, store: VectorStore = None):
        if store is not None:
            self.store = store
            logger.info("初始化 ContextMemory (使用外部 store)")
        else:
            resolved = collection_name or config.chroma.collection_name
            logger.info("初始化 ContextMemory, collection=%s (显式指定=%s)", resolved, bool(collection_name))
            self.store = create_store(collection_name)
        self._encoder = None
        self._encoder_lock = threading.Lock()
        self._bm25 = None
        self._bm25_docs = []
        self._bm25_lock = threading.Lock()
        self._bm25_building = False
        self._vacuum_lock = threading.Lock()
        # F5: 启动时自动检测并迁移存量 active → status
        self._auto_migrate_status()

    def _auto_migrate_status(self):
        """F5: 自动检测存量数据并迁移 active → status。

        查找含有 old active 字段但缺少 status 字段的记录，批量转换：
          active=true  → status=active
          active=false + archived=true → status=archived
          active=false → status=forgotten

        仅扫描有限条记录（_LIST_MAX_LOAD），迁移完成后不再全表扫描。
        """
        try:
            # 添加 limit 防止全表扫描 OOM，迁移分多次完成
            results = self.store.get(include=["metadatas"], limit=self._LIST_MAX_LOAD)
            ids = results.get("ids", [])
            metas = results.get("metadatas", [])
            if not ids:
                return

            updates = []
            for i, meta in enumerate(metas):
                meta = meta or {}
                if "active" in meta and "status" not in meta:
                    active_val = meta.get("active")
                    archived_val = meta.get("archived", False)
                    if active_val is True or str(active_val).lower() == "true":
                        new_status = "active"
                    elif archived_val is True or str(archived_val).lower() == "true":
                        new_status = "archived"
                    else:
                        new_status = "forgotten"
                    updates.append((ids[i], new_status))

            if not updates:
                return

            logger.info("F5 存量迁移: 发现 %d 条记录需要迁移 active → status", len(updates))
            # 批量更新而非逐条 — P1-3 优化
            batch_ids = [u[0] for u in updates]
            batch_statuses = [{"status": u[1]} for u in updates]
            self.store.update(ids=batch_ids, metadatas=batch_statuses)
            logger.info("F5 存量迁移完成: %d 条", len(updates))
        except Exception as e:
            logger.warning("F5 存量迁移失败（非致命）: %s", e)

    def _ensure_encoder(self):
        """双重检查锁：首次需要编码时才加载模型，避免实例化时 2s + 1.3GB 开销"""
        if self._encoder is not None:
            return
        with self._encoder_lock:
            if self._encoder is not None:
                return
            self._encoder = _get_encoder()

    def warmup(self):
        """显式预热模型，供 Dashboard 启动时调用，避免首个请求超时"""
        self._ensure_encoder()

    def close(self):
        """释放 ChromaDB 连接和模型资源，确保干净关闭（包括线程池）。"""
        import gc

        # 1. 关闭 ChromaDB PersistentClient 的内部线程
        if self.store is not None:
            try:
                store = self.store
                if hasattr(store, "_col") and store._col is not None:
                    # 尝试关闭 ChromaDB 内部 segment 系统
                    client = getattr(store, "_client", None)
                    if client is not None and hasattr(client, "_system"):
                        try:
                            client._system.stop()
                        except Exception:
                            logger.debug("关闭 ChromaDB system 失败", exc_info=True)
                    del store._col
                self.store = None
                del store
            except Exception:
                self.store = None

        # 2. 释放 BM25 索引
        if self._bm25 is not None:
            self._bm25 = None
        self._bm25_docs = []

        # 3. 释放 SentenceTransformer 模型（torch 线程池）
        if self._encoder is not None:
            encoder = self._encoder
            self._encoder = None
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                logger.debug("释放 torch 资源异常（非致命）", exc_info=True)
            del encoder

        gc.collect()
        logger.info("ContextMemory 资源已释放")

    @property
    def is_encoder_loaded(self) -> bool:
        return self._encoder is not None

    @staticmethod
    def _tokenize(text: str) -> list:
        return re.findall(r"\w+", text.lower())

    @staticmethod
    def _build_metadata(metadata: dict = None) -> dict:
        import hashlib as _hlib

        meta = {
            "timestamp": time.time(),
            "type": config.memory.default_type,
            "project_id": _hlib.md5(os.getcwd().encode()).hexdigest()[:8],
            "status": config.memory.default_status,
            "source": "user_instructed",
        }
        if metadata:
            meta.update(metadata)
        # F5 source 值域校验：非法值强制设为 unknown
        if meta.get("source") not in ALLOWED_SOURCES:
            meta["source"] = "unknown"
        return meta

    def remember(
        self, text: str, metadata: dict = None, dedup_strategy: str = None, embedding: list[float] = None
    ) -> str | None:
        # P0-2 修复: 支持外部传入预计算 embedding，避免导入时重复编码
        if embedding is not None and len(embedding) == config.model.vector_dim:
            embedding = list(embedding)  # 确保是 list 类型
        else:
            self._ensure_encoder()
            embedding = self._encoder.encode(text).tolist()
        if dedup_strategy:
            where_clauses = []
            if metadata and "project_id" in metadata:
                where_clauses.append({"project_id": metadata["project_id"]})
            if metadata and "type" in metadata:
                where_clauses.append({"type": metadata["type"]})
            if len(where_clauses) == 1:
                where = where_clauses[0]
            elif len(where_clauses) > 1:
                where = {"$and": where_clauses}
            else:
                where = None
            similar = self.store.query(
                query_embeddings=[embedding],
                n_results=config.memory.dedup_top_k,
                where=where if where else None,
                include=["documents", "distances", "metadatas"],
            )
            if similar["documents"][0]:
                dup_doc, dist = similar["documents"][0][0], similar["distances"][0][0]
                dup_meta = similar["metadatas"][0][0] if similar.get("metadatas") else {}
                if dist < _get_similarity_threshold():
                    logger.info(
                        "去重命中 dist=%.3f | threshold=%.2f | 策略=%s\n  新记录: %s\n  旧记录 [%s]: %s",
                        dist,
                        _get_similarity_threshold(),
                        dedup_strategy,
                        text,
                        dup_meta.get("type", "?"),
                        dup_doc,
                    )
                    if dedup_strategy == "skip":
                        return None
                    elif dedup_strategy == "overwrite":
                        self.store.delete(ids=[similar["ids"][0][0]])

        # v0.4.0 HIGH-2: VACUUM 期间拒绝写入，防止 SQLite 锁冲突致数据损坏
        if self._vacuum_lock.locked():
            raise ChromaDBError("数据库维护中，请稍后重试", suggestion="VACUUM 正在回收磁盘空间，完成后自动恢复写入")
        mem_id = uuid.uuid4().hex
        meta = self._build_metadata(metadata)
        self.store.add(documents=[text], embeddings=[embedding], metadatas=[meta], ids=[mem_id])
        self._add_to_bm25(text)
        logger.info("remembered %s...", mem_id[:8])
        return mem_id

    def _ensure_bm25_index(self):
        if self._bm25 is not None:
            return
        with self._bm25_lock:
            if self._bm25 is not None:
                return
            # v0.4.4 P0-1: 标记构建中，_add_to_bm25 检查此标记拒绝增量写入
            self._bm25_building = True
            try:
                # F5: 使用 status 替代 active，仅索引 active 记录
                all_docs = self.store.get(where={"status": {"$in": ["active"]}})["documents"]
                if not all_docs:
                    self._bm25 = None
                    self._bm25_docs = []
                    return
                tokenized = [self._tokenize(d) for d in all_docs]
                self._bm25 = BM25Okapi(tokenized)
                self._bm25_docs = all_docs
            finally:
                self._bm25_building = False

    def _invalidate_bm25(self):
        """全量失效 BM25 索引（异常恢复用）。下一次检索时触发全量重建。"""
        with self._bm25_lock:
            self._bm25 = None

    @staticmethod
    def _bm25_build_nd(doc_freqs: list) -> dict:
        """从 doc_freqs (list-of-dict) 构建 nd (word→doc_count) 字典"""
        nd = {}
        for freq_dict in doc_freqs:
            for word in freq_dict:
                nd[word] = nd.get(word, 0) + 1
        return nd

    def _add_to_bm25(self, document: str):
        """增量追加单条文档到 BM25 索引。更新 doc_freqs/doc_len 后触发 IDF 重算。"""
        with self._bm25_lock:
            # v0.4.4 P0-1: 构建中或未就绪时拒绝增量写入，防止写入半初始化索引
            if self._bm25 is None or self._bm25_building:
                return
            tokens = self._tokenize(document)
            freq_dict = {}
            for t in tokens:
                freq_dict[t] = freq_dict.get(t, 0) + 1
            self._bm25.doc_freqs.append(freq_dict)
            self._bm25.doc_len.append(len(tokens))
            self._bm25_docs.append(document)
            self._bm25.corpus_size += 1
            self._bm25.avgdl = sum(self._bm25.doc_len) / self._bm25.corpus_size
            nd = self._bm25_build_nd(self._bm25.doc_freqs)
            self._bm25._calc_idf(nd)

    def _update_in_bm25(self, old_doc: str, new_doc: str):
        """增量更新 BM25 中的单条文档。找不到旧文档时退化为 invalidate。"""
        with self._bm25_lock:
            if self._bm25 is None:
                return
            try:
                idx = self._bm25_docs.index(old_doc)
            except ValueError:
                self._bm25 = None
                return
            new_tokens = self._tokenize(new_doc)
            new_freq = {}
            for t in new_tokens:
                new_freq[t] = new_freq.get(t, 0) + 1
            self._bm25.doc_freqs[idx] = new_freq
            self._bm25.doc_len[idx] = len(new_tokens)
            self._bm25_docs[idx] = new_doc
            self._bm25.avgdl = sum(self._bm25.doc_len) / self._bm25.corpus_size
            nd = self._bm25_build_nd(self._bm25.doc_freqs)
            self._bm25._calc_idf(nd)

    def _remove_from_bm25(self, document: str):
        """从 BM25 索引中删除单条文档。删除场景低频，直接全量失效更安全。"""
        # 删除导致 doc_freqs 计数复杂（需全局重算），直接 invalidate 更可靠
        self._invalidate_bm25()

    def _build_where(
        self,
        where: dict = None,
        days_limit: int = None,
        project_id: str = None,
        type_filter: str | list[str] = None,
        include_archived: bool = False,
        exclude_types: list[str] = None,
        creator_id: str = None,
        ignore_scope: bool = True,
        exclude_forgotten: bool = False,
    ):
        """统一构建 ChromaDB where 条件，所有查询路径复用。

        creator_id + ignore_scope: M6 数据隔离 — 当 ignore_scope=False 且传入 creator_id 时，
        自动追加 scope/creator_id 过滤，确保用户只能看到 team 范围或自己创建的 personal 数据。
        默认 ignore_scope=True 保持向后兼容（legacy 模式无隔离）。

        F5: 使用 status 三态替代 active(bool)。过渡期兼容 active(status 优先)。
        exclude_forgotten: 额外排除 status=forgotten（recall 使用）。
        """
        clauses = {}
        and_items = []
        if where:
            # 平展已存在的 $and，避免嵌套 $and
            if "$and" in where:
                and_items.extend(where["$and"])
            else:
                clauses.update(where)

        # M6 数据隔离：scope/creator_id 过滤（仅统一模式下启用）
        # v0.5.1: {"scope": "team"} → {"scope": {"$ne": "personal"}} 兼容旧记录（缺少 scope 视为 team）
        if not ignore_scope and creator_id:
            and_items.append(
                {
                    "$or": [
                        {"scope": {"$ne": "personal"}},
                        {"creator_id": creator_id},
                        {"creator_id": "unknown"},  # v0.5.2: Hook 写入时未认证的记录对所有登录用户可见
                    ]
                }
            )

        if days_limit:
            clauses["timestamp"] = {"$gte": time.time() - days_limit * 86400}
        if project_id:
            clauses["project_id"] = project_id
        if type_filter:
            if isinstance(type_filter, list):
                clauses["type"] = {"$in": type_filter}
            else:
                clauses["type"] = type_filter
        if exclude_types:
            and_items.append({"type": {"$nin": exclude_types}})
        if not include_archived:
            # F5: 过渡期兼容（status 优先，active 兜底）
            # TODO v0.8.0: 移除 active fallback，迁移完成后仅使用 status
            # 使用 $eq True 而非 $ne False，避免缺失 active 字段的记录误通过检查
            and_items.append({
                "$or": [
                    {"status": {"$ne": "archived"}},
                    {"active": {"$eq": True}},
                ]
            })
        if exclude_forgotten:
            and_items.append({
                "$or": [
                    {"status": {"$ne": "forgotten"}},
                    {"active": {"$eq": True}},
                ]
            })
        if not clauses and not and_items:
            return None
        if not and_items and len(clauses) == 1:
            return clauses
        # 将单个 clauses 和 and_items 合并为一个 $and
        all_items = and_items + [{k: v} for k, v in clauses.items()]
        return {"$and": all_items} if len(all_items) > 1 else all_items[0]

    def recall(
        self,
        query: str,
        top_k: int = None,
        where: dict = None,
        days_limit: int = None,
        project_id: str = None,
        decay_lambda: float = None,
        include_archived: bool = False,
        hybrid: bool = False,
        bm25_weight: float = None,
        return_scores: bool = False,
        creator_id: str = None,
        ignore_scope: bool = True,
    ) -> list[str] | list[dict]:
        # --- 调用监控：调用方 + 时间戳 + 完整参数 ---
        _start = time.perf_counter()
        _caller = inspect.currentframe().f_back
        _caller_info = f"{_caller.f_globals['__name__']}:{_caller.f_code.co_name}" if _caller else "unknown"
        logger.info(
            "[RECALL] ⏺ 调用方=%s | query=%s | top_k=%s | where=%s | days_limit=%s | "
            "project_id=%s | decay_lambda=%s | hybrid=%s | bm25_weight=%s | return_scores=%s",
            _caller_info,
            query[:100],
            top_k,
            where,
            days_limit,
            project_id,
            decay_lambda,
            hybrid,
            bm25_weight,
            return_scores,
        )

        if top_k is None:
            top_k = config.memory.default_top_k
        if decay_lambda is None:
            decay_lambda = config.memory.decay_lambda
        if bm25_weight is None:
            bm25_weight = 0.7
        # v0.6.0: recall 默认 restricted 类型列表（新 6 类 + 旧 7 类兼容）
        if where is None:
            where = {"type": {"$in": list(ALL_RECALL_TYPES)}}
        self._ensure_encoder()
        query_vec = self._encoder.encode(query).tolist()
        needs_rerank = decay_lambda > 0 or hybrid or return_scores
        rerank_mult = config.memory.rerank_multiplier
        rerank_min = config.memory.rerank_min_candidates
        n_results = max(top_k * rerank_mult, rerank_min) if needs_rerank else top_k
        include = ["documents", "metadatas", "distances"]
        results = self.store.query(
            query_embeddings=[query_vec],
            n_results=n_results,
            where=self._build_where(
                where,
                days_limit,
                project_id,
                include_archived=include_archived,
                creator_id=creator_id,
                ignore_scope=ignore_scope,
                exclude_forgotten=True,
            ),
            include=include,
        )
        docs = results["documents"][0]

        # F7 活动日志埋点（非阻塞）
        try:
            from ..features.activity_log import log_recall as _log_recall
            _raw = where.get("type", "all")
            if isinstance(_raw, dict) and "$in" in _raw:
                _match_types = _raw["$in"]
            elif isinstance(_raw, str):
                _match_types = [_raw]
            elif isinstance(_raw, list):
                _match_types = _raw
            else:
                _match_types = ["all"]
            _log_recall(query=query, result_count=len(docs), match_types=_match_types, project_id=project_id)
        except Exception:
            logger.debug("F7 活动日志埋点失败（非致命）", exc_info=True)

        if not docs:
            _elapsed = (time.perf_counter() - _start) * 1000
            logger.info("[RECALL] ⏹ 无结果 | 耗时=%.0fms | 调用方=%s", _elapsed, _caller_info)
            return []

        if hybrid:
            self._ensure_bm25_index()

        if needs_rerank:
            metadatas = results["metadatas"][0]
            distances = results["distances"][0]
            ids = results["ids"][0] if return_scores else None
            now = time.time()

            if hybrid and self._bm25 and self._bm25_docs:
                query_tokens = self._tokenize(query)
                bm25_scores = self._bm25.get_scores(query_tokens)
                doc_to_bm25 = dict(zip(self._bm25_docs, bm25_scores))
                max_bm25 = float(bm25_scores.max()) if bm25_scores.size > 0 else 1

            scored = []
            for i, (doc, meta, dist) in enumerate(zip(docs, metadatas, distances)):
                similarity = 1 - dist
                # v0.4.4 增强版: quality_score 前置过滤 — 低质量记忆不参与排序
                quality_score = meta.get("quality_score", 0.5)
                if quality_score < 0.30:
                    logger.debug("质量分数 %.2f < 0.30, 跳过: %s", quality_score, doc[:60])
                    continue
                age_days = (now - meta.get("timestamp", now)) / 86400
                decay_factor = math.exp(-decay_lambda * age_days) if decay_lambda > 0 else 1.0
                if hybrid and self._bm25 and self._bm25_docs:
                    bm25_score = doc_to_bm25.get(doc, 0) / max_bm25 if max_bm25 > 0 else 0
                    score = bm25_weight * similarity + (1 - bm25_weight) * bm25_score
                else:
                    score = similarity
                # v0.4.6: 统一复用/反馈加成（合并原 reuse_boost + feedback_boost）
                reuse_boost = self._compute_reuse_boost(meta, now)
                effective_multiplier = 1.0 + reuse_boost
                if effective_multiplier < 0:
                    effective_multiplier = 0.0  # 防御：防止负数 multiplier 导致排序反转
                score *= decay_factor * effective_multiplier
                scored.append((score, doc, similarity, decay_factor, ids[i] if ids else None, meta))
            scored.sort(key=lambda x: -x[0])
            top_items = scored[:top_k]
            if return_scores:
                _elapsed = (time.perf_counter() - _start) * 1000
                logger.info(
                    "[RECALL] ⏹ 返回 %d 条(含分数) | 耗时=%.0fms | 调用方=%s | 结果=%s",
                    len(top_items),
                    _elapsed,
                    _caller_info,
                    json.dumps(
                        [{"id": i[4], "final_score": round(i[0], 4), "doc": i[1][:60]} for i in top_items],
                        ensure_ascii=False,
                    ),
                )
                return [
                    {
                        "id": item[4],
                        "document": item[1],
                        "metadata": item[5],
                        "similarity": round(item[2], 4),
                        "decay_factor": round(item[3], 4),
                        "final_score": round(item[0], 4),
                    }
                    for item in top_items
                ]
            docs = [item[1] for item in top_items]

        _elapsed = (time.perf_counter() - _start) * 1000
        logger.info(
            "[RECALL] ⏹ 返回 %d 条 | 耗时=%.0fms | 调用方=%s | 结果=%s",
            len(docs),
            _elapsed,
            _caller_info,
            json.dumps(docs[: min(len(docs), 5)], ensure_ascii=False),
        )
        return docs

    def recall_with_scores(
        self,
        query: str,
        top_k: int = None,
        where: dict = None,
        days_limit: int = None,
        project_id: str = None,
        creator_id: str = None,
        ignore_scope: bool = True,
    ) -> list[dict]:
        """返回 dict 列表，每项含 id/document/distance/metadata，供去重和冲突检测使用。"""
        _start = time.perf_counter()
        _caller = inspect.currentframe().f_back
        _caller_info = f"{_caller.f_globals['__name__']}:{_caller.f_code.co_name}" if _caller else "unknown"
        logger.info(
            "[RECALL_SCORES] ⏺ 调用方=%s | query=%s | top_k=%s | where=%s | days_limit=%s | project_id=%s",
            _caller_info,
            query[:100],
            top_k,
            where,
            days_limit,
            project_id,
        )

        if top_k is None:
            top_k = config.memory.dedup_top_k
        self._ensure_encoder()
        query_vec = self._encoder.encode(query).tolist()
        results = self.store.query(
            query_embeddings=[query_vec],
            n_results=top_k,
            where=self._build_where(where, days_limit, project_id, creator_id=creator_id, ignore_scope=ignore_scope),
            include=["documents", "distances", "metadatas"],
        )
        ids = results["ids"][0]  # ChromaDB query 始终返回 ids，无需显式 include
        docs = results["documents"][0]
        dists = results["distances"][0]
        metas = results["metadatas"][0]

        out = (
            [
                {"id": iid, "document": doc, "distance": dist, "metadata": meta or {}}
                for iid, doc, dist, meta in zip(ids, docs, dists, metas)
            ]
            if ids
            else []
        )

        _elapsed = (time.perf_counter() - _start) * 1000
        logger.info(
            "[RECALL_SCORES] ⏹ 返回 %d 条 | 耗时=%.0fms | 调用方=%s | 结果=%s",
            len(out),
            _elapsed,
            _caller_info,
            json.dumps(
                [{"id": r["id"], "distance": round(r["distance"], 4), "doc": r["document"][:60]} for r in out[:5]],
                ensure_ascii=False,
            ),
        )
        return out

    def list_memories(
        self,
        project_id: str = None,
        type_filter: str | list[str] = None,
        limit: int = None,
        offset: int = 0,
        include_archived: bool = False,
        where: dict = None,  # v0.4.1: 附加 ChromaDB where 过滤条件
        exclude_types: list[str] = None,  # v0.4.8: 排除指定类型
        creator_id: str = None,
        ignore_scope: bool = True,
    ) -> list[dict]:
        if limit is None:
            limit = config.dashboard.list_default_limit
        # v0.4.4 P1-3: 复用统一 where 构建方法
        where_clause = self._build_where(
            where=where,
            project_id=project_id,
            type_filter=type_filter,
            include_archived=include_archived,
            exclude_types=exclude_types,
            creator_id=creator_id,
            ignore_scope=ignore_scope,
        )

        # v0.7.1-P3: 改用 get_ids_by_time 通过 SQLite 索引直接获取时间序 ID，
        # 避免全量元数据加载 + 内存排序的 O(N) 问题
        page_ids = self.store.get_ids_by_time(where=where_clause, limit=limit, offset=offset)

        if not page_ids:
            return []

        result = self.store.get(ids=page_ids)
        id_order = {id_: idx for idx, id_ in enumerate(page_ids)}
        items = []
        for i in range(len(result["ids"])):
            items.append(
                {
                    "id": result["ids"][i],
                    "document": result["documents"][i],
                    "metadata": result["metadatas"][i] or {},
                }
            )
        items.sort(key=lambda x: id_order.get(x["id"], 0))
        return items

    def list_todos(
        self,
        project_id: str = None,
        todo_status: str = None,
        limit: int = 10,
        offset: int = 0,
        creator_id: str = None,
        ignore_scope: bool = True,
    ) -> list[dict]:
        """查询待办列表，独立实现（不共享 list_memories）。
        按 sort_order 升序排列。
        """
        # F5: 使用 status 替代 active，过渡期兼容
        where_clauses = [{"type": "todo"}, {"status": {"$ne": "archived"}}]
        if todo_status:
            where_clauses.append({"todo_status": todo_status})
        if project_id:
            where_clauses.append({"project_id": project_id})
        if not ignore_scope and creator_id:
            where_clauses.append(
                {
                    "$or": [
                        {"scope": {"$ne": "personal"}},
                        {"creator_id": creator_id},
                    ]
                }
            )
        where = {"$and": where_clauses} if len(where_clauses) > 1 else where_clauses[0]

        all_meta = self.store.get(where=where, include=["metadatas"], limit=self._LIST_MAX_LOAD)
        actual_count = len(all_meta["ids"])
        if actual_count >= self._LIST_MAX_LOAD:
            logger.warning("list_todos 结果集超限(%d)，仅处理前 %d 条", actual_count, self._LIST_MAX_LOAD)
        meta_count = len(all_meta["ids"])
        if meta_count == 0 or offset >= meta_count:
            return []

        # 按 sort_order 升序排列
        sorted_pairs = sorted(
            [(all_meta["ids"][i], (all_meta["metadatas"][i] or {}).get("sort_order", 0)) for i in range(meta_count)],
            key=lambda x: x[1],
        )
        page_ids = [p[0] for p in sorted_pairs[offset : offset + limit]]

        if not page_ids:
            return []

        result = self.store.get(ids=page_ids)
        id_order = {id_: idx for idx, id_ in enumerate(page_ids)}
        items = []
        for i in range(len(result["ids"])):
            items.append(
                {
                    "id": result["ids"][i],
                    "document": result["documents"][i],
                    "metadata": result["metadatas"][i] or {},
                }
            )
        items.sort(key=lambda x: id_order.get(x["id"], 0))
        return items

    def count_memories(
        self,
        project_id: str = None,
        type_filter: str | list[str] = None,
        include_archived: bool = False,
        where: dict = None,
        creator_id: str = None,
        ignore_scope: bool = True,
    ) -> int:
        # v0.4.4 P1-3: 复用统一 where 构建方法
        where_clause = self._build_where(
            where=where,
            project_id=project_id,
            type_filter=type_filter,
            include_archived=include_archived,
            creator_id=creator_id,
            ignore_scope=ignore_scope,
        )
        return self.store.count(where=where_clause)

    def _export_by_ids(self, memory_ids: list[str], include_embeddings: bool, project_id: str = None):
        """按指定 ID 列表导出记忆，支持分批获取嵌入向量。"""
        from datetime import datetime, timezone

        stored = self.store.get(ids=memory_ids, include=["embeddings", "metadatas", "documents"])
        if not stored or not stored.get("ids"):
            return

        ids = stored["ids"]
        documents = stored.get("documents", [])
        metadatas = stored.get("metadatas", [])
        embeddings = stored.get("embeddings")

        yield {
            "_header": {
                "format_version": "1.0",
                "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": f"memos v{__version__}",
                "model": {"name": config.model.name, "vector_dim": config.model.vector_dim},
                "project_id": project_id,
                "total": len(ids),
            }
        }

        for i, mem_id in enumerate(ids):
            item = {
                "id": mem_id,
                "content": documents[i] if i < len(documents) else "",
                "metadata": metadatas[i] if i < len(metadatas) else {},
            }
            if include_embeddings and embeddings is not None and i < len(embeddings):
                emb = embeddings[i]
                item["embedding"] = emb if emb is None else (emb.tolist() if hasattr(emb, "tolist") else emb)
            else:
                item["embedding"] = None
            yield item

    def export_memories(
        self,
        project_id: str = None,
        type_filter: list[str] = None,
        include_embeddings: bool = False,
        batch_size: int = 500,
        memory_ids: list[str] = None,
        since: str = None,
        until: str = None,
    ):
        """分批导出记忆为生成器，每批 yield 一个 dict 列表。
        调用方逐批处理，避免全量加载到内存。

        type_filter 为 None 时默认导出全部知识库类型（fact/decision/preference/todo/
        bug_fix/feature_design/code_optimize/tech_knowledge），排除 Pipeline C 对话原文。
        传入空列表 [] 可导出所有类型（含 user_input/assistant_output）。

        since/until: ISO 日期字符串 "YYYY-MM-DD"，按记忆 timestamp 过滤。
        memory_ids: 指定记忆 ID 列表，优先级最高，传入后忽略 type_filter/since/until。
        """
        if memory_ids:
            yield from self._export_by_ids(memory_ids, include_embeddings, project_id)
            return

        if type_filter is None:
            type_filter = [
                "task",
                "briefing",
                "solution",
                "decision",
                "lesson",
                "process",
            ]

        # 构建 where 子句
        and_clauses = []
        if project_id:
            and_clauses.append({"project_id": project_id})
        if type_filter:
            and_clauses.append({"type": {"$in": type_filter}})

        # 日期范围过滤
        if since:
            try:
                since_ts = datetime.strptime(since, "%Y-%m-%d").timestamp()
                and_clauses.append({"timestamp": {"$gte": since_ts}})
            except ValueError:
                raise ChromaDBError(f"日期格式无效: {since}，应为 YYYY-MM-DD")
        if until:
            try:
                until_ts = datetime.strptime(until + " 23:59:59", "%Y-%m-%d %H:%M:%S").timestamp()
                and_clauses.append({"timestamp": {"$lte": until_ts}})
            except ValueError:
                raise ChromaDBError(f"日期格式无效: {until}，应为 YYYY-MM-DD")

        # 先统计总数
        where = {"$and": and_clauses} if len(and_clauses) > 1 else (and_clauses[0] if and_clauses else None)
        total_count = self.store.count(where=where)
        _batch_sentinel = 0

        offset = 0
        while True:
            batch = self.list_memories(
                project_id=project_id,
                type_filter=type_filter,
                limit=batch_size,
                offset=offset,
                include_archived=True,
            )
            # 在内存中应用额外的过滤条件（since/until——这些字段 ChromaDB where 不完全支持）
            if since or until:
                filtered = []
                for item in batch:
                    meta = item.get("metadata", {})
                    ts = meta.get("timestamp", 0)
                    if since and ts < since_ts:
                        continue
                    if until and ts > until_ts:
                        continue
                    filtered.append(item)
                batch = filtered

            if not batch:
                break

            if include_embeddings and batch:
                ids = [item["id"] for item in batch]
                stored = self.store.get(ids=ids, include=["embeddings"])
                emb_map = {}
                if stored.get("embeddings") is not None:
                    for i, sid in enumerate(stored["ids"]):
                        emb = stored["embeddings"][i]
                        emb_map[sid] = emb if emb is None else (emb.tolist() if hasattr(emb, "tolist") else emb)
                for item in batch:
                    item["embedding"] = emb_map.get(item["id"])

            # 首批时 yield 头部
            if _batch_sentinel == 0:
                yield {  # 格式头部
                    "_header": {
                        "format_version": "1.0",
                        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "source": f"memos v{__version__}",
                        "model": {"name": config.model.name, "vector_dim": config.model.vector_dim},
                        "project_id": project_id,
                        "total": total_count,
                    }
                }

            for item in batch:
                yield {
                    "id": item["id"],
                    "content": item["document"],
                    "metadata": item["metadata"],
                    "embedding": item.get("embedding"),
                }
            offset += batch_size
            _batch_sentinel += batch_size

    def import_memories(
        self,
        lines,
        target_project_id: str = None,
        strategy: str = "skip",
        preserve_ids: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """导入 JSON Lines 格式的记忆。返回 {"imported": N, "skipped": N, "failed": N, "errors": [...]}

        strategy: "skip"（默认）/ "overwrite" / "duplicate"
        preserve_ids: True 时保留原始 ID（默认重新生成）
        dry_run: True 时仅校验不写入
        """
        import sys

        result = {"imported": 0, "skipped": 0, "failed": 0, "errors": [], "total_lines": 0, "valid_lines": 0}
        vec_dim = config.model.vector_dim

        for line_num, line in enumerate(lines, 1):
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            line = line.strip()
            if not line:
                continue

            # 跳过格式头部注释行
            if line.startswith("# "):
                continue

            result["total_lines"] += 1

            # 进度输出（每 100 条打印到 stderr）
            if line_num % 100 == 0:
                print(f"已处理 {line_num} 条...", file=sys.stderr)

            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                result["failed"] += 1
                result["errors"].append({"line": line_num, "error": f"JSON 解析失败: {e}"})
                continue

            content = item.get("content", "")
            if not content:
                result["failed"] += 1
                result["errors"].append({"line": line_num, "error": "缺少必填字段 content"})
                continue

            metadata = item.get("metadata", {})
            if "type" not in metadata:
                result["failed"] += 1
                result["errors"].append({"line": line_num, "error": "metadata 缺少 type 字段"})
                continue

            # 类型校验
            allowed_types = {
                "fact",
                "decision",
                "preference",
                "todo",
                "bug_fix",
                "feature_design",
                "code_optimize",
                "tech_knowledge",
            }
            if metadata["type"] not in allowed_types:
                result["failed"] += 1
                result["errors"].append({"line": line_num, "error": f"type 值非法: {metadata['type']}"})
                continue

            # embedding 维度校验
            ext_embedding = item.get("embedding")
            if ext_embedding is not None:
                if len(ext_embedding) != vec_dim:
                    logger.info(
                        "行 %d: embedding 维度不匹配 (源 %d != 当前 %d)，将重新编码",
                        line_num,
                        len(ext_embedding),
                        vec_dim,
                    )
                    ext_embedding = None  # 强制重新编码

            # dry-run: 仅校验不写入
            if dry_run:
                result["valid_lines"] += 1
                continue

            if target_project_id:
                metadata["project_id"] = target_project_id

            # 确保导入的记忆被用量统计正确计数
            metadata.setdefault("source", "user_instructed")

            # strategy=duplicate: 强制生成新 ID
            if strategy == "duplicate":
                mid = uuid.uuid4().hex
                meta = self._build_metadata(metadata)
                if ext_embedding is not None:
                    self.store.add(documents=[content], embeddings=[ext_embedding], metadatas=[meta], ids=[mid])
                else:
                    self._ensure_encoder()
                    embedding = self._encoder.encode(content).tolist()
                    self.store.add(documents=[content], embeddings=[embedding], metadatas=[meta], ids=[mid])
                self._add_to_bm25(content)
                result["imported"] += 1
                continue

            # preserve_ids + overwrite: 保留原始 ID 覆盖
            original_id = item.get("id") if preserve_ids else None
            if original_id and strategy == "overwrite":
                existing = self.get_memory(original_id)
                if existing:
                    self.update_memory(original_id, new_content=content, new_metadata=metadata)
                    result["imported"] += 1
                    continue

            # 标准路径: 去重检查
            dedup = strategy if strategy in ("skip", "overwrite") else None
            mid = self.remember(content, metadata=metadata, dedup_strategy=dedup, embedding=ext_embedding)
            if mid:
                result["imported"] += 1
            else:
                result["skipped"] += 1

        return result

    def get_memory(self, mem_id: str) -> dict | None:
        result = self.store.get(ids=[mem_id])
        if not result["ids"]:
            return None
        return {"id": result["ids"][0], "document": result["documents"][0], "metadata": result["metadatas"][0]}

    def update_memory(self, mem_id: str, new_content: str = None, new_metadata: dict = None):
        old = self.get_memory(mem_id)
        if old is None:
            raise ChromaDBError(f"记忆未找到: {mem_id[:8]}...", detail=f"id={mem_id}")

        if new_content is None and not new_metadata:
            raise ChromaDBError("至少需要提供 new_content 或 new_metadata", suggestion="请提供 text 或 metadata 参数")

        meta = old["metadata"].copy()
        if new_metadata:
            meta.update(new_metadata)
        # F10: useful_feedback_count 下限校验，与 _compute_reuse_boost() 的 clamp 保持一致
        if "useful_feedback_count" in meta:
            meta["useful_feedback_count"] = max(meta["useful_feedback_count"], -10)

        doc = new_content if new_content is not None else old["document"]
        # P3-2: 仅当文本内容变更时才重新编码 embedding，只改 metadata 时复用旧向量
        if new_content is not None and new_content != old["document"]:
            self._ensure_encoder()
            embedding = self._encoder.encode(doc).tolist()
        else:
            # B3: ChromaDB 内部索引损坏时 "Error finding id"，回退到重新编码
            try:
                stored = self.store.get(ids=[mem_id], include=["embeddings"])
                emb_list = stored.get("embeddings")
                # emb_list 可能是 list/numpy array，用 is not None 避免 numpy 歧义错误
                if emb_list is not None and len(emb_list) > 0 and emb_list[0] is not None:
                    embedding = emb_list[0]
                else:
                    self._ensure_encoder()
                    embedding = self._encoder.encode(doc).tolist()
            except ChromaDBError:
                logger.warning("复用 embedding 失败(id=%s)，回退到重新编码", mem_id[:8])
                self._ensure_encoder()
                embedding = self._encoder.encode(doc).tolist()
        self.store.update(ids=[mem_id], documents=[doc], embeddings=[embedding], metadatas=[meta])
        if new_content is not None and new_content != old["document"]:
            self._update_in_bm25(old["document"], doc)
        # F9: SSE 事件总线 — status 变更触发 task 面板刷新
        if new_metadata and "status" in new_metadata:
            old_status = old["metadata"].get("status", "")
            new_status = new_metadata["status"]
            if new_status != old_status:
                try:
                    from ..features.event_bus import touch_event as _touch
                    _touch("task")
                except Exception:
                    logger.debug("SSE 事件总线触发失败（非致命）", exc_info=True)
        logger.info("updated: %s...", mem_id[:8])

    def _get_deleted_stats(self) -> dict:
        """返回 (total, active, deleted) 统计 — F5: 使用 status 替代 active。"""
        total = self.count_memories(include_archived=True)
        active = self.count_memories(include_archived=False)
        return {"total": total, "active": active, "deleted": total - active}

    def _maybe_vacuum(self) -> bool:
        """检测是否需要 Vacuum（删除占比>20%且>100条），需要则执行。
        v0.4.0 HIGH-2: 加 _vacuum_lock 防止执行期间并发写入致 SQLite 数据损坏。"""
        stats = self._get_deleted_stats()
        if stats["deleted"] == 0:
            return False
        ratio = stats["deleted"] / max(stats["total"], 1)
        if ratio > 0.2 and stats["deleted"] > 100:
            logger.info("触发自动 VACUUM: 删除 %d/%d (%.0f%%)", stats["deleted"], stats["total"], ratio * 100)
            with self._vacuum_lock:
                return self.store.vacuum()
        return False

    def delete_memory(self, mem_id: str):
        old = self.get_memory(mem_id)
        if old is None:
            raise ChromaDBError(f"记忆未找到: {mem_id[:8]}...", detail=f"id={mem_id}")
        self.store.delete(ids=[mem_id])
        self._remove_from_bm25(old["document"])
        self._maybe_vacuum()
        logger.info("deleted: %s...", mem_id[:8])

    def forget_memory(self, mem_id: str, inactive_reason: str = "manual_forget"):
        """F6: 标记记忆为 forgotten 状态，记录 forgotten_at 时间戳用于自动归档倒计时。"""
        if inactive_reason not in INACTIVE_REASON_VALUES:
            inactive_reason = "manual_forget"
        old = self.get_memory(mem_id)
        if old is None:
            raise ChromaDBError(f"记忆未找到: {mem_id[:8]}...", detail=f"id={mem_id}")
        import time as _time
        self.store.update(
            ids=[mem_id],
            metadatas=[{"status": "forgotten", "inactive_reason": inactive_reason, "forgotten_at": _time.time()}],
        )
        logger.info("forgotten: %s... (reason=%s)", mem_id[:8], inactive_reason)

    def supersede_memory(self, old_id: str, new_id: str) -> None:
        """标记旧记忆为被新记忆覆盖。"""
        self.update_memory(old_id, metadata={
            "status": "forgotten",
            "inactive_reason": "superseded",
            "superseded_by": new_id,
        })
        logger.info("superseded: %s... -> %s...", old_id[:8], new_id[:8])

    def archive_memory(self, mem_id: str, inactive_reason: str = "manual_archive"):
        if inactive_reason not in INACTIVE_REASON_VALUES:
            inactive_reason = "manual_archive"
        old = self.get_memory(mem_id)
        if old is None:
            raise ChromaDBError(f"记忆未找到: {mem_id[:8]}...", detail=f"id={mem_id}")
        self.store.update(ids=[mem_id], metadatas=[{"status": "archived", "inactive_reason": inactive_reason}])
        logger.info("archived: %s... (reason=%s)", mem_id[:8], inactive_reason)

    def restore_memory(self, mem_id: str):
        """F7: 恢复记忆为 active 状态，同时清除 inactive_reason 和 forgotten_at。"""
        old = self.get_memory(mem_id)
        if old is None:
            raise ChromaDBError(f"记忆未找到: {mem_id[:8]}...", detail=f"id={mem_id}")
        import time as _time
        self.store.update(ids=[mem_id], metadatas=[{
            "status": "active",
            "inactive_reason": "",
            "forgotten_at": 0,
            "updated_at": _time.time(),  # 重置计时起点，避免恢复后立即被遗忘
        }])
        logger.info("restored: %s...", mem_id[:8])

    def restore_from_forgotten(self, mem_id: str):
        """F7: 恢复已 forgotten 的记忆。等效于 restore_memory。"""
        return self.restore_memory(mem_id)

    def permanent_archive(self, mem_id: str):
        """F7: 永久归档记忆（同 archive_memory），设置 status=archived, inactive_reason=manual_archive。"""
        return self.archive_memory(mem_id)

    def archive_old_memories(self, days: int = None) -> int:
        """F7: 扫描 forgotten 超过指定天数的记忆，自动归档为 archived。

        仅处理继承了 forgotten_at 的已遗忘记忆（跳过 forgotten_at=0 的未迁移旧数据）。
        """
        if days is None:
            days = config.memory.archive_days
        cutoff = time.time() - days * 86400
        # F7: 扫描 status=forgotten + forgotten_at < cutoff
        results = self.store.get(
            where={"$and": [{"status": "forgotten"}, {"forgotten_at": {"$lte": cutoff}}]},
            include=["metadatas"],
        )
        ids = results["ids"]
        if not ids:
            logger.info("没有超过 %d 天的 forgotten 记忆需要归档", days)
            return 0

        # F7: 跳过 forgotten_at=0 的未迁移旧数据（8.7）
        valid_ids = []
        for i, mid in enumerate(ids):
            fa = (results["metadatas"][i] or {}).get("forgotten_at", 0)
            if not fa:
                logger.debug("跳过 forgotten_at=0 的未迁移记录: %s", mid[:8])
                continue
            valid_ids.append(mid)

        if not valid_ids:
            return 0

        self.store.update(
            ids=valid_ids,
            metadatas=[{"status": "archived", "inactive_reason": "auto_archive"} for _ in valid_ids],
        )
        logger.info("自动归档 %d 条 forgotten 超过 %d 天的记忆", len(valid_ids), days)
        return len(valid_ids)

    def get_expiry_status(self) -> dict:
        """获取过期状态统计：即将过期和已过期的记忆数量。F5: 使用 status 替代 active。"""
        now = time.time()
        archive_sec = config.memory.archive_days * 86400
        warn_sec = config.memory.expiry_warn_days * 86400
        expired_cutoff = now - archive_sec
        warn_start = expired_cutoff
        warn_end = expired_cutoff + warn_sec

        expired_records = self.store.get(
            where={"$and": [{"timestamp": {"$lt": expired_cutoff}}, {"status": "active"}]},
            include=["metadatas"],
        )
        expired = len(expired_records.get("ids", []))

        expiring_records = self.store.get(
            where={
                "$and": [
                    {"timestamp": {"$gte": warn_start}},
                    {"timestamp": {"$lt": warn_end}},
                    {"status": "active"},
                ]
            },
            include=["metadatas"],
        )
        expiring_soon = len(expiring_records.get("ids", []))

        return {"expiring_soon": expiring_soon, "expired": expired}

    def renew_memory(self, mem_id: str) -> bool:
        """将指定记忆的 timestamp 更新为当前时间（续期）。"""
        old = self.get_memory(mem_id)
        if old is None:
            return False
        now = time.time()
        meta = old.get("metadata", {})
        meta["timestamp"] = now
        self.store.update(ids=[mem_id], metadatas=[meta])
        logger.info("renewed memory %s... timestamp reset to now", mem_id[:8])
        return True

    # F10: 统一复用/反馈加成 — reuse_count + useful_feedback_count 线性组合
    def _compute_reuse_boost(self, meta: dict, now: float = None) -> float:
        reuse_count = meta.get("reuse_count", 0) or 0
        useful_feedback_count = meta.get("useful_feedback_count", 0) or 0
        # useful_feedback_count 允许负值，最低 -10
        useful_feedback_count = max(useful_feedback_count, -10)
        return max(0.0, math.log2(reuse_count + 1) * 0.15 + useful_feedback_count * 0.30)

    # v0.4.6: 反馈反哺 — 将建议反馈写回源记忆的 reuse_count
    def _apply_feedback_to_source(self, source_memory_id: str, feedback: str):
        """反哺反馈到源记忆：正向 +1 reuse_count，负向 -1（最低 0）。
        幂等性由调用方保证（已检查 suggestion status 不是 reacted）。
        异常静默捕获，不阻断主流程。
        """
        if not source_memory_id:
            return

        source = self.get_memory(source_memory_id)
        if source is None:
            logger.warning("反馈反哺: 源记忆不存在 %s...", source_memory_id[:8])
            return

        meta = dict(source["metadata"])
        current = int(meta.get("reuse_count", 0) or 0)

        if feedback == "useful":
            meta["reuse_count"] = current + 1
            meta["useful_feedback_count"] = int(meta.get("useful_feedback_count", 0)) + 1
        elif feedback == "not_useful":
            meta["reuse_count"] = max(0, current - 1)
            # F10: not_useful 同时递减 useful_feedback_count，最低 -10
            current_useful = int(meta.get("useful_feedback_count", 0) or 0)
            meta["useful_feedback_count"] = max(-10, current_useful - 1)
        else:
            logger.warning("反馈反哺: 未知反馈类型 %s", feedback)
            return

        meta["last_feedback_at"] = time.time()

        self.store.update(ids=[source_memory_id], metadatas=[meta])
        # F10: SSE 事件总线 — useful_feedback_count 变更触发 feedback 面板刷新
        try:
            from ..features.event_bus import touch_event as _touch
            _touch("feedback")
        except Exception:
            logger.debug("SSE 事件总线通知失败（反馈面板刷新）", exc_info=True)
        logger.info(
            "反馈反哺: 源记忆 %s... reuse_count=%d useful_feedback_count=%d",
            source_memory_id[:8],
            meta.get("reuse_count", 0),
            meta.get("useful_feedback_count", 0),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mem = ContextMemory()
    print(mem.recall("我用的什么后端框架？"))
    print(mem.recall("端口是多少？"))
