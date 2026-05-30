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
# 审计日志路径
_AUDIT_LOG = Path("./memdb/audit.log")


def _log_audit(record: dict):
    """将破坏性操作写入审计日志文件（追加模式）"""
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("写入审计日志失败: %s", e)


class ChromaDBPersistentStore(VectorStore):
    # ChromaDB 内部异常关键词，匹配到任一即翻译为 ChromaDBError
    _CHROMA_INTERNAL_ERRORS = ("error finding id", "could not find", "index", "illegal instruction")

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
        self._col = client.get_or_create_collection(self._collection_name)

    def _col_call(self, method_name, *args, **kwargs):
        """调用 ChromaDB collection 方法，内部异常统一翻译为 ChromaDBError。"""
        try:
            return getattr(self._col, method_name)(*args, **kwargs)
        except ChromaDBError:
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

    def count(self, where=None) -> int:
        if where is not None:
            result = self._col_call("get", where=where, include=[])
            return len(result["ids"])
        return self._col_call("count")

    def vacuum(self) -> bool:
        """对底层 SQLite 执行 VACUUM 回收已删除文档的磁盘空间"""
        import sqlite3

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


class ChromaDBHttpStore(VectorStore):
    # ChromaDB 内部异常关键词，匹配到任一即翻译为 ChromaDBError
    _CHROMA_INTERNAL_ERRORS = ("error finding id", "could not find", "index", "illegal instruction")

    def __init__(self, collection_name: str = None):
        c = config.chroma
        self._collection_name = collection_name or c.collection_name
        client = chromadb.HttpClient(host=c.host, port=c.port)
        self._col = client.get_or_create_collection(self._collection_name)

    def _col_call(self, method_name, *args, **kwargs):
        """调用 ChromaDB collection 方法，内部异常统一翻译为 ChromaDBError。"""
        try:
            return getattr(self._col, method_name)(*args, **kwargs)
        except ChromaDBError:
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

    def count(self, where=None) -> int:
        if where is not None:
            result = self._col_call("get", where=where, include=[])
            return len(result["ids"])
        return self._col_call("count")

    def vacuum(self) -> bool:
        """HTTP 模式不支持本地 VACUUM（需在 ChromaDB Server 端执行）"""
        logger.warning("HTTP 模式不支持客户端 VACUUM，请在 ChromaDB Server 上执行")
        return False


def create_store(collection_name: str = None) -> VectorStore:
    if config.chroma.mode == "http":
        return ChromaDBHttpStore(collection_name)
    return ChromaDBPersistentStore(collection_name)
