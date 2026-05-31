from abc import ABC, abstractmethod


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
