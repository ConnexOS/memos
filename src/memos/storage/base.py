from abc import ABC, abstractmethod
from datetime import datetime


class VectorStore(ABC):
    @abstractmethod
    def query(self, query_embeddings, n_results, where=None, include=None) -> dict: ...

    @abstractmethod
    def get(self, where=None, limit=None, offset=None, include=None, ids=None) -> dict: ...

    @abstractmethod
    def add(self, documents, embeddings, metadatas, ids) -> None: ...

    @abstractmethod
    def update(self, ids, metadatas=None, documents=None, embeddings=None) -> None: ...

    @abstractmethod
    def delete(self, ids) -> None: ...

    @abstractmethod
    def count(self, where=None) -> int: ...

    def get_ids_by_time(self, where: dict = None, limit: int = None, offset: int = 0) -> list[str]:
        """按 timestamp DESC 排序返回 embedding_id 列表。

        默认实现：加载元数据后在内存中排序。
        子类可覆盖为数据库级排序优化（如直接 SQLite ORDER BY）。
        返回的列表中元素按时间倒序排列。
        """
        result = self.get(where=where, include=["metadatas"], limit=5000)
        ids = result.get("ids", [])
        metas = result.get("metadatas", [])
        if not ids:
            return []

        pairs = []
        for i in range(len(ids)):
            ts = (metas[i] or {}).get("timestamp", 0)
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts).timestamp()
                except (ValueError, TypeError):
                    ts = 0.0
            pairs.append((ids[i], float(ts)))

        pairs.sort(key=lambda x: x[1], reverse=True)
        if limit is not None:
            return [p[0] for p in pairs[offset: offset + limit]]
        return [p[0] for p in pairs[offset:]]

    @abstractmethod
    def vacuum(self) -> bool:
        """回收已删除文档占用的磁盘空间。返回是否执行了操作。"""
        ...

    @abstractmethod
    def reindex(self) -> dict:
        """重建向量索引：导出全量数据、删除并重建 collection、重新导入。
        返回 {"status": "ok"|"empty"|"error"|"partial", "count": int, "total": int, "error": str}
        """
        ...

    @property
    def supports_offset(self) -> bool:
        """是否支持 get() 的 offset 参数进行原生分页"""
        return True
