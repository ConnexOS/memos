"""MEMOS 存储抽象层 —— VectorStore ABC + ChromaDB 实现 + 嵌入模型管理。

v0.4.3 架构重整 Phase 8。
"""

from memos.storage.base import VectorStore
from memos.storage.chroma import (
    ChromaDBPersistentStore,
    create_store,
)
from memos.storage.embeddings import (
    download_model,
    get_download_progress,
    get_model_path,
    model_exists,
)

__all__ = [
    "VectorStore",
    "ChromaDBPersistentStore",
    "create_store",
    "download_model",
    "get_model_path",
    "model_exists",
    "get_download_progress",
]
