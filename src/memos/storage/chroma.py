import json
import logging
import os
import time
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from ..config import config
from ..errors import ChromaDBError
from .base import VectorStore

logger = logging.getLogger(__name__)

# 受保护的生产集合名，禁止无意识写入测试数据
_PROTECTED_COLLECTIONS = {"project_memory"}


def _audit_log_path() -> Path:
    """审计日志路径：统一到 MEMOS_HOME，避免在每个 CWD 下创建独立日志。"""
    from ..config.models import get_memos_home

    return get_memos_home() / "data" / "logs" / "audit.log"


def _log_audit(record: dict):
    """将破坏性操作写入审计日志文件（追加模式）"""
    try:
        path = _audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("写入审计日志失败: %s", e)


# ── SQLite 时间序查询：where→SQL 翻译 ────────────────────────────


def _resolve_value(value):
    """根据 Python 值类型返回 (column, sql_value)。"""
    if isinstance(value, bool):
        return "bool_value", 1 if value else 0
    elif isinstance(value, int):
        return "int_value", value
    elif isinstance(value, float):
        return "float_value", value
    else:
        return "string_value", str(value)


def _resolve_values(values):
    """解析值列表，通过首元素类型确定列。"""
    if not values:
        return "string_value", []
    col, _ = _resolve_value(values[0])
    sql_values = []
    for v in values:
        _, sv = _resolve_value(v)
        sql_values.append(sv)
    return col, sql_values


def _operator_to_sql(field: str, op: str, operand) -> tuple[str, list]:
    """翻译带运算符的条件为 EXISTS/NOT EXISTS 子查询。
    返回 (sql_fragment, params_list)。
    """
    if op == "$in":
        col, vals = _resolve_values(operand)
        if not vals:
            return "1=0", []
        placeholders = ",".join("?" for _ in vals)
        return (
            f"EXISTS (SELECT 1 FROM embedding_metadata em_ "
            f"WHERE em_.id = e.id AND em_.key = ? AND em_.{col} IN ({placeholders}))",
            [field] + vals,
        )

    elif op == "$nin":
        col, vals = _resolve_values(operand)
        if not vals:
            return "", []
        placeholders = ",".join("?" for _ in vals)
        return (
            f"NOT EXISTS (SELECT 1 FROM embedding_metadata em_ "
            f"WHERE em_.id = e.id AND em_.key = ? AND em_.{col} IN ({placeholders}))",
            [field] + vals,
        )

    elif op == "$ne":
        col, val = _resolve_value(operand)
        return (
            f"NOT EXISTS (SELECT 1 FROM embedding_metadata em_ WHERE em_.id = e.id AND em_.key = ? AND em_.{col} = ?)",
            [field, val],
        )

    elif op == "$gte":
        col, val = _resolve_value(operand)
        if col not in ("float_value", "int_value"):
            logger.warning("$gte 仅支持数值字段, field=%s col=%s", field, col)
            return "", []
        return (
            f"EXISTS (SELECT 1 FROM embedding_metadata em_ WHERE em_.id = e.id AND em_.key = ? AND em_.{col} >= ?)",
            [field, val],
        )

    elif op == "$lte":
        col, val = _resolve_value(operand)
        if col not in ("float_value", "int_value"):
            logger.warning("$lte 仅支持数值字段, field=%s col=%s", field, col)
            return "", []
        return (
            f"EXISTS (SELECT 1 FROM embedding_metadata em_ WHERE em_.id = e.id AND em_.key = ? AND em_.{col} <= ?)",
            [field, val],
        )

    elif op == "$eq":
        col, val = _resolve_value(operand)
        return (
            f"EXISTS (SELECT 1 FROM embedding_metadata em_ WHERE em_.id = e.id AND em_.key = ? AND em_.{col} = ?)",
            [field, val],
        )

    else:
        logger.warning("不支持的 ChromaDB where 操作符: %s (field=%s)", op, field)
        return "", []


def _leaf_to_sql(field: str, condition) -> tuple[str, list]:
    """翻译单字段条件为 EXISTS 子查询。
    输入: ("project_id", "abc") 或 ("type", {"$in": ["a","b"]})。
    """
    if isinstance(condition, dict):
        for op, operand in condition.items():
            return _operator_to_sql(field, op, operand)

    # 简单值等值匹配
    col, val = _resolve_value(condition)
    return (
        f"EXISTS (SELECT 1 FROM embedding_metadata em_ WHERE em_.id = e.id AND em_.key = ? AND em_.{col} = ?)",
        [field, val],
    )


def _where_to_sql(where: dict) -> tuple[str, list]:
    """递归翻译 ChromaDB where dict 为 SQL 条件。

    返回 (sql_fragment, params_list)，失败时返回 ("", [])。
    支持: $and, $or, 精确匹配, $in, $nin, $ne, $gte, $lte, $eq。
    """
    if not where:
        return "", []

    # $and / $or
    if "$and" in where:
        clauses, params = [], []
        for sub in where["$and"]:
            sql, p = _where_to_sql(sub)
            if sql:
                clauses.append(f"({sql})")
                params.extend(p)
        if not clauses:
            return "", []
        return " AND ".join(clauses), params

    if "$or" in where:
        clauses, params = [], []
        for sub in where["$or"]:
            sql, p = _where_to_sql(sub)
            if sql:
                clauses.append(f"({sql})")
                params.extend(p)
        if not clauses:
            return "", []
        return " OR ".join(clauses), params

    # 叶子节点 — 可能有多个字段（隐式 AND）
    clauses, params = [], []
    for field, condition in where.items():
        if field in ("$and", "$or"):
            continue
        sql, p = _leaf_to_sql(field, condition)
        if sql:
            clauses.append(sql)
            params.extend(p)

    if not clauses:
        return "", []
    return " AND ".join(clauses), params


# ── Store 实现 ────────────────────────────────────────────────


class ChromaDBPersistentStore(VectorStore):
    # ChromaDB 内部异常关键词，匹配到任一即翻译为 ChromaDBError
    _CHROMA_INTERNAL_ERRORS = ("error finding id", "could not find", "index already exists", "illegal instruction")

    def __init__(self, collection_name: str = None):
        c = config.chroma
        self._collection_name = collection_name or c.collection_name

        # 保护检测：pytest 环境 + 受保护集合 → 告警
        if self._collection_name in _PROTECTED_COLLECTIONS and "PYTEST_CURRENT_TEST" in os.environ:
            logger.warning(
                "检测到测试环境操作生产集合 '%s'！如非预期请检查测试配置。",
                self._collection_name,
            )

        client = chromadb.PersistentClient(
            path=c.path,
            settings=ChromaSettings(
                chroma_server_connect_timeout=c.timeout,
                chroma_server_read_timeout=c.timeout,
                anonymized_telemetry=False,
            ),
        )
        self._client = client
        self._col = client.get_or_create_collection(self._collection_name)

        # 启用 SQLite WAL 模式：消除 DELETE 日志模式下写锁阻塞所有读写的死锁问题
        self._enable_wal()

    def _enable_wal(self):
        """启用 SQLite WAL 模式，允许并发读 + 一个写。

        WAL（Write-Ahead Logging）模式下读操作不阻塞写、写不阻塞读，
        从根本上消除多线程（SchedulerThread + asyncio.to_thread）并发
        访问 ChromaDB 时的 SQLite 锁竞争死锁。
        """
        import sqlite3

        db_path = Path(config.chroma.path) / "chroma.sqlite3"
        if not db_path.exists():
            logger.debug("SQLite 数据库不存在，跳过 WAL 启用: %s", db_path)
            return
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.close()
            logger.info("SQLite WAL 模式已启用: %s", db_path)
        except Exception as e:
            logger.warning("启用 WAL 模式失败（非致命，将使用默认 DELETE 模式）: %s", e)

    def _col_call(self, method_name, *args, **kwargs):
        """调用 ChromaDB collection 方法，内部异常统一翻译为 ChromaDBError。

        v0.4.7: 添加指数退避重试（0.2s/0.5s），应对 SQLite 并发冲突导致的
        ChromaDB 内部索引不一致。最大重试 2 次，仅对 ChromaDBError 类型重试。
        """
        import time as _time

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                return getattr(self._col, method_name)(*args, **kwargs)
            except ChromaDBError:
                if attempt < max_retries:
                    logger.warning("ChromaDB 操作重试(%s) %d/%d", method_name, attempt + 1, max_retries)
                    _time.sleep(0.2 * (attempt + 1))
                    continue
                raise
            except Exception as e:
                msg = str(e).lower()
                for pattern in self._CHROMA_INTERNAL_ERRORS:
                    if pattern in msg:
                        raise ChromaDBError(f"ChromaDB 内部异常({method_name}): {e}") from e
                raise

    def query(self, query_embeddings, n_results, where=None, include=None) -> dict:
        return self._col_call(
            "query", query_embeddings=query_embeddings, n_results=n_results, where=where, include=include
        )

    def get(self, where=None, limit=None, offset=None, include=None, ids=None) -> dict:
        kwargs = {
            k: v
            for k, v in [("where", where), ("limit", limit), ("offset", offset), ("include", include), ("ids", ids)]
            if v is not None
        }
        return self._col_call("get", **kwargs)

    def add(self, documents, embeddings, metadatas, ids) -> None:
        self._col_call("add", documents=documents, embeddings=embeddings, metadatas=metadatas, ids=ids)

    def update(self, ids, metadatas=None, documents=None, embeddings=None) -> None:
        if documents is not None or metadatas is not None:
            ids_list = ids if isinstance(ids, list) else [ids]
            record = {
                "action": "update",
                "ids_count": len(ids_list),
                "ids_preview": ids_list[:3],
                "timestamp": time.time(),
                "collection": self._collection_name,
            }
            logger.warning("修改操作: %s", record)
            _log_audit(record)
        kwargs = {"ids": ids}
        if metadatas is not None:
            kwargs["metadatas"] = metadatas
        if documents is not None:
            kwargs["documents"] = documents
        if embeddings is not None:
            kwargs["embeddings"] = embeddings
        self._col_call("update", **kwargs)

    def delete(self, ids) -> None:
        ids_list = ids if isinstance(ids, list) else [ids]
        record = {
            "action": "delete",
            "ids_count": len(ids_list),
            "ids_preview": ids_list[:3],
            "timestamp": time.time(),
            "collection": self._collection_name,
        }
        logger.warning("破坏性操作: %s", record)
        _log_audit(record)
        self._col_call("delete", ids=ids)

    # 通过 get 计数时的最大加载量（限制全量加载 OOM）
    _COUNT_MAX_LOAD = 5000

    def count(self, where=None) -> int:
        if where is None:
            return self._col_call("count")

        # v0.7.1-P3: SQLite COUNT(*) 精确计数，失败时回退到 ChromaDB 近似值
        import sqlite3

        db_path = Path(config.chroma.path) / "chroma.sqlite3"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                try:
                    row = conn.execute(
                        "SELECT s.id FROM segments s "
                        "JOIN collections c ON c.id = s.collection "
                        "LEFT JOIN embeddings e ON e.segment_id = s.id "
                        "WHERE c.name = ? "
                        "GROUP BY s.id "
                        "ORDER BY COUNT(e.id) DESC LIMIT 1",
                        (self._collection_name,),
                    ).fetchone()
                    if row:
                        segment_id = row[0]
                        sql = "SELECT COUNT(*) FROM embeddings e WHERE e.segment_id = ?"
                        params: list = [segment_id]
                        where_sql, where_params = _where_to_sql(where)
                        if where_sql:
                            sql += f" AND ({where_sql})"
                            params.extend(where_params)
                        return conn.execute(sql, params).fetchone()[0]
                finally:
                    conn.close()
            except Exception as e:
                logger.debug("SQLite COUNT 失败 (%s)，回退到 ChromaDB 近似计数", e)

        # Fallback: ChromaDB get() 近似计数
        result = self._col_call("get", where=where, include=[], limit=self._COUNT_MAX_LOAD)
        count = len(result["ids"])
        if count >= self._COUNT_MAX_LOAD:
            logger.warning("count(where=...) 结果超限(%d)，返回近似值", count)
        return count

    def get_ids_by_time(self, where: dict = None, limit: int = None, offset: int = 0) -> list[str]:
        """按 timestamp DESC 排序返回 embedding_id 列表。

        直接查询 ChromaDB 底层 SQLite 的 embedding_metadata 表，
        利用 (key, float_value) 索引实现 O(log N + K) 时间序分页。
        失败时静默回退到父类的内存排序方案。
        """
        import sqlite3

        if not hasattr(self, "_collection_name") or not self._collection_name:
            return super().get_ids_by_time(where=where, limit=limit, offset=offset)

        db_path = Path(config.chroma.path) / "chroma.sqlite3"
        if not db_path.exists():
            return super().get_ids_by_time(where=where, limit=limit, offset=offset)

        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
        except Exception as e:
            logger.warning("SQLite 连接失败 (%s)，回退到内存排序", e)
            return super().get_ids_by_time(where=where, limit=limit, offset=offset)

        try:
            # 1. 获取当前集合的 active segment_id（ChromaDB 可能有多段，选有数据的）
            row = conn.execute(
                """
                SELECT s.id
                FROM segments s
                JOIN collections c ON c.id = s.collection
                LEFT JOIN embeddings e ON e.segment_id = s.id
                WHERE c.name = ?
                GROUP BY s.id
                ORDER BY COUNT(e.id) DESC
                LIMIT 1
                """,
                (self._collection_name,),
            ).fetchone()
            if not row:
                logger.debug("未找到集合 '%s' 的 segment，回退", self._collection_name)
                return super().get_ids_by_time(where=where, limit=limit, offset=offset)
            segment_id = row[0]

            # 2. 构造基础 SQL：LEFT JOIN timestamp 元数据用于排序
            sql = """
                SELECT e.embedding_id
                FROM embeddings e
                LEFT JOIN embedding_metadata tm
                    ON tm.id = e.id AND tm.key = 'timestamp'
                WHERE e.segment_id = ?
            """
            params: list = [segment_id]

            # 3. 翻译 ChromaDB where 条件为 SQL EXISTS 子句
            if where:
                where_sql, where_params = _where_to_sql(where)
                if where_sql:
                    sql += f" AND ({where_sql})"
                    params.extend(where_params)

            # 4. ORDER BY timestamp DESC + LIMIT/OFFSET 分页
            sql += " ORDER BY COALESCE(tm.float_value, 0) DESC"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            if offset:
                sql += " OFFSET ?"
                params.append(offset)

            # 5. 执行查询
            rows = conn.execute(sql, params).fetchall()
            return [r["embedding_id"] for r in rows]

        except Exception as e:
            logger.warning("SQLite 时间序查询失败 (%s)，回退到内存排序", e)
            return super().get_ids_by_time(where=where, limit=limit, offset=offset)
        finally:
            conn.close()

    def vacuum(self) -> bool:
        """对底层 SQLite 执行 VACUUM 回收已删除文档的磁盘空间"""
        import sqlite3

        db_path = Path(config.chroma.path) / "chroma.sqlite3"
        """对底层 SQLite 执行 VACUUM 回收已删除文档的磁盘空间"""

        db_path = Path(config.chroma.path) / "chroma.sqlite3"
        if not db_path.exists():
            return False
        try:
            before = db_path.stat().st_size
            conn = sqlite3.connect(str(db_path))
            conn.execute("VACUUM")
            conn.close()
            after = db_path.stat().st_size
            logger.info("VACUUM 完成: %s → %s (回收 %.1fMB)", before, after, (before - after) / 1024 / 1024)
            return True
        except Exception as e:
            logger.error("VACUUM 失败: %s", e)
            return False

    def reindex(self) -> dict:
        """重建向量索引：分批导出全量数据、删除并重建 collection、重新导入。
        重建后 HNSW 索引从零构建，可修复索引损坏导致的 Error finding id 类错误。

        分批导出（每批 500 条）避免 ChromaDB get() 在大数据量+embedding 时静默截断。
        返回 {"status": "ok"|"empty"|"error", "count": int, "error": str}
        """
        # 分批获取全部数据（ChromaDB get() 在含 embedding 时大数据量可能截断）
        batch_size = 500
        all_ids, all_docs, all_metas, all_embs = [], [], [], []
        offset = 0
        while True:
            try:
                batch = self._col_call(
                    "get",
                    limit=batch_size,
                    offset=offset,
                    include=["documents", "metadatas", "embeddings"],
                )
            except ChromaDBError as e:
                return {"status": "error", "count": 0, "error": str(e)}
            ids = batch.get("ids", [])
            if not ids:
                break
            all_ids.extend(ids)
            all_docs.extend(batch.get("documents", []))
            all_metas.extend(batch.get("metadatas", []))
            all_embs.extend(batch.get("embeddings", []))
            offset += len(ids)

        total = len(all_ids)
        if total == 0:
            return {"status": "empty", "count": 0}

        # 删除旧 collection
        try:
            self._client.delete_collection(self._collection_name)
        except Exception as e:
            return {"status": "error", "count": 0, "error": f"删除集合失败: {e}"}

        # 重建 collection
        try:
            self._col = self._client.create_collection(self._collection_name)
        except Exception as e:
            return {"status": "error", "count": 0, "error": f"重建集合失败: {e}"}

        # 分批重新导入
        imported = 0
        for i in range(0, total, batch_size):
            batch_end = min(i + batch_size, total)
            try:
                self._col_call(
                    "add",
                    documents=all_docs[i:batch_end],
                    embeddings=all_embs[i:batch_end],
                    metadatas=all_metas[i:batch_end],
                    ids=all_ids[i:batch_end],
                )
                imported += batch_end - i
            except ChromaDBError as e:
                logger.error("reindex 导入批次 %d-%d 失败: %s", i, batch_end, e)
                continue

        return {"status": "ok" if imported == total else "partial", "count": imported, "total": total}


class ChromaDBHttpStore(VectorStore):
    # ChromaDB 内部异常关键词，匹配到任一即翻译为 ChromaDBError
    _CHROMA_INTERNAL_ERRORS = ("error finding id", "could not find", "index already exists", "illegal instruction")

    def __init__(self, collection_name: str = None):
        c = config.chroma
        self._collection_name = collection_name or c.collection_name
        client = chromadb.HttpClient(host=c.host, port=c.port)
        self._client = client
        self._col = client.get_or_create_collection(self._collection_name)

        # HTTP 模式：SQLite 在服务端管理，无需客户端设置 WAL
        self._enable_wal()

    def _enable_wal(self):
        """HTTP 模式无需客户端设置 WAL（SQLite 在服务端管理）。"""
        pass

    def _col_call(self, method_name, *args, **kwargs):
        """调用 ChromaDB collection 方法，内部异常统一翻译为 ChromaDBError。

        v0.4.7: 添加指数退避重试（0.2s/0.5s），应对 SQLite 并发冲突导致的
        ChromaDB 内部索引不一致。最大重试 2 次，仅对 ChromaDBError 类型重试。
        """
        import time as _time

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                return getattr(self._col, method_name)(*args, **kwargs)
            except ChromaDBError:
                if attempt < max_retries:
                    logger.warning("ChromaDB 操作重试(%s) %d/%d", method_name, attempt + 1, max_retries)
                    _time.sleep(0.2 * (attempt + 1))
                    continue
                raise
            except Exception as e:
                msg = str(e).lower()
                for pattern in self._CHROMA_INTERNAL_ERRORS:
                    if pattern in msg:
                        raise ChromaDBError(f"ChromaDB 内部异常({method_name}): {e}") from e
                raise

    def query(self, query_embeddings, n_results, where=None, include=None) -> dict:
        return self._col_call(
            "query", query_embeddings=query_embeddings, n_results=n_results, where=where, include=include
        )

    def get(self, where=None, limit=None, offset=None, include=None, ids=None) -> dict:
        kwargs = {
            k: v
            for k, v in [("where", where), ("limit", limit), ("offset", offset), ("include", include), ("ids", ids)]
            if v is not None
        }
        return self._col_call("get", **kwargs)

    def add(self, documents, embeddings, metadatas, ids) -> None:
        self._col_call("add", documents=documents, embeddings=embeddings, metadatas=metadatas, ids=ids)

    def update(self, ids, metadatas=None, documents=None, embeddings=None) -> None:
        if documents is not None or metadatas is not None:
            ids_list = ids if isinstance(ids, list) else [ids]
            record = {
                "action": "update",
                "ids_count": len(ids_list),
                "ids_preview": ids_list[:3],
                "timestamp": time.time(),
                "collection": self._collection_name,
            }
            logger.warning("修改操作: %s", record)
            _log_audit(record)
        kwargs = {"ids": ids}
        if metadatas is not None:
            kwargs["metadatas"] = metadatas
        if documents is not None:
            kwargs["documents"] = documents
        if embeddings is not None:
            kwargs["embeddings"] = embeddings
        self._col_call("update", **kwargs)

    def delete(self, ids) -> None:
        ids_list = ids if isinstance(ids, list) else [ids]
        record = {
            "action": "delete",
            "ids_count": len(ids_list),
            "ids_preview": ids_list[:3],
            "timestamp": time.time(),
            "collection": self._collection_name,
        }
        logger.warning("破坏性操作: %s", record)
        _log_audit(record)
        self._col_call("delete", ids=ids)

    # 通过 get 计数时的最大加载量（限制全量加载 OOM）
    _COUNT_MAX_LOAD = 5000

    def count(self, where=None) -> int:
        if where is not None:
            result = self._col_call("get", where=where, include=[], limit=self._COUNT_MAX_LOAD)
            count = len(result["ids"])
            if count >= self._COUNT_MAX_LOAD:
                logger.warning("count(where=...) 结果超限(%d)，返回近似值", count)
            return count
        return self._col_call("count")

    def vacuum(self) -> bool:
        """HTTP 模式不支持本地 VACUUM（需在 ChromaDB Server 端执行）"""
        logger.warning("HTTP 模式不支持客户端 VACUUM，请在 ChromaDB Server 上执行")
        return False

    def reindex(self) -> dict:
        """重建向量索引（HTTP 模式）。分批导出避免数据截断。"""
        batch_size = 500
        all_ids, all_docs, all_metas, all_embs = [], [], [], []
        offset = 0
        while True:
            try:
                batch = self._col_call(
                    "get",
                    limit=batch_size,
                    offset=offset,
                    include=["documents", "metadatas", "embeddings"],
                )
            except ChromaDBError as e:
                return {"status": "error", "count": 0, "error": str(e)}
            ids = batch.get("ids", [])
            if not ids:
                break
            all_ids.extend(ids)
            all_docs.extend(batch.get("documents", []))
            all_metas.extend(batch.get("metadatas", []))
            all_embs.extend(batch.get("embeddings", []))
            offset += len(ids)

        total = len(all_ids)
        if total == 0:
            return {"status": "empty", "count": 0}

        try:
            self._client.delete_collection(self._collection_name)
        except Exception as e:
            return {"status": "error", "count": 0, "error": f"删除集合失败: {e}"}

        try:
            self._col = self._client.create_collection(self._collection_name)
        except Exception as e:
            return {"status": "error", "count": 0, "error": f"重建集合失败: {e}"}

        imported = 0
        for i in range(0, total, batch_size):
            batch_end = min(i + batch_size, total)
            try:
                self._col_call(
                    "add",
                    documents=all_docs[i:batch_end],
                    embeddings=all_embs[i:batch_end],
                    metadatas=all_metas[i:batch_end],
                    ids=all_ids[i:batch_end],
                )
                imported += batch_end - i
            except ChromaDBError as e:
                logger.error("reindex 导入批次 %d-%d 失败: %s", i, batch_end, e)
                continue

        return {"status": "ok" if imported == total else "partial", "count": imported, "total": total}


def create_store(collection_name: str = None) -> VectorStore:
    return ChromaDBPersistentStore(collection_name)
