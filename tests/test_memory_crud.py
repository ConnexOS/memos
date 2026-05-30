import uuid
from memos.engine.memory import ContextMemory

from tests.conftest import clean_collection

COLLECTION = "test_crud"


class TestRemember:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_returns_uuid_string(self):
        mem_id = self.mem.remember("用FastAPI", {"type": "decision"})
        assert isinstance(mem_id, str)
        assert len(mem_id) == 32
        assert uuid.UUID(mem_id)

    def test_default_metadata(self):
        mem_id = self.mem.remember("hello world")
        mem = self.mem.get_memory(mem_id)
        assert mem["metadata"]["type"] == "fact"
        assert len(mem["metadata"]["project_id"]) == 8  # MD5 前 8 位
        assert mem["metadata"]["active"] is True
        assert "timestamp" in mem["metadata"]

    def test_custom_metadata(self):
        mem_id = self.mem.remember("用FastAPI", {"type": "decision", "project_id": "proj1"})
        mem = self.mem.get_memory(mem_id)
        assert mem["metadata"]["type"] == "decision"
        assert mem["metadata"]["project_id"] == "proj1"

    def test_partial_metadata_preserves_defaults(self):
        mem_id = self.mem.remember("test", {"source": "auto_extract"})
        mem = self.mem.get_memory(mem_id)
        assert mem["metadata"]["source"] == "auto_extract"
        assert mem["metadata"]["type"] == "fact"
        assert mem["metadata"]["active"] is True


class TestGetMemory:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_exists(self):
        mem_id = self.mem.remember("test content")
        mem = self.mem.get_memory(mem_id)
        assert mem["id"] == mem_id
        assert mem["document"] == "test content"
        assert isinstance(mem["metadata"], dict)

    def test_not_found(self):
        assert self.mem.get_memory("nonexistent") is None


class TestUpdateMemory:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_content_updated(self):
        mem_id = self.mem.remember("旧内容", {"type": "decision"})
        self.mem.update_memory(mem_id, "新内容")
        mem = self.mem.get_memory(mem_id)
        assert mem["document"] == "新内容"

    def test_metadata_preserved(self):
        mem_id = self.mem.remember("旧内容", {"type": "decision", "project_id": "projX"})
        self.mem.update_memory(mem_id, "新内容")
        mem = self.mem.get_memory(mem_id)
        assert mem["metadata"]["type"] == "decision"
        assert mem["metadata"]["project_id"] == "projX"

    def test_not_found_raises(self):
        import pytest
        from memos.errors import ChromaDBError

        with pytest.raises(ChromaDBError, match="未找到"):
            self.mem.update_memory("bad_id", "new content")


class TestDeleteMemory:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_memory_removed(self):
        mem_id = self.mem.remember("待删除")
        self.mem.delete_memory(mem_id)
        assert self.mem.get_memory(mem_id) is None

    def test_recall_excludes_deleted(self):
        mem_id = self.mem.remember("唯一内容")
        self.mem.delete_memory(mem_id)
        results = self.mem.recall("唯一内容", top_k=5)
        assert "唯一内容" not in results

    def test_not_found_raises(self):
        import pytest
        from memos.errors import ChromaDBError

        with pytest.raises(ChromaDBError, match="未找到"):
            self.mem.delete_memory("bad_id")


class TestRecallBackwardCompat:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_recall_works_as_before(self):
        self.mem.remember("决定使用FastAPI框架")
        results = self.mem.recall("FastAPI", top_k=3)
        assert "决定使用FastAPI框架" in results

    def test_recall_with_scores_works(self):
        self.mem.remember("决定使用PostgreSQL")
        results = self.mem.recall_with_scores("PostgreSQL", top_k=3)
        assert len(results) > 0
        r = results[0]
        assert "PostgreSQL" in r["document"]
        assert isinstance(r["distance"], float)
        assert "id" in r
