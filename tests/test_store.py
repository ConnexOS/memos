from unittest import mock
from memos.engine.extractor import MemoryExtractor


class TestStoreMemories:
    def setup_method(self):
        self.ext = MemoryExtractor()

    def test_no_memory_system(self):
        self.ext.memory = None
        memories = [{"content": "用FastAPI", "type": "preference"}, {"content": "Python", "type": "fact"}]
        count = self.ext.store_memories(memories)
        assert count == 2

    def test_normal_storage(self):
        fake_memory = mock.Mock()
        fake_memory.recall_with_scores.return_value = []
        self.ext.memory = fake_memory
        memories = [{"content": "用FastAPI", "type": "decision"}, {"content": "写测试", "type": "todo"}]
        count = self.ext.store_memories(memories)
        assert count == 2
        assert fake_memory.remember.call_count == 2

    def test_duplicate_exact_match(self):
        fake_memory = mock.Mock()
        fake_memory.recall_with_scores.return_value = [
            {"id": "m1", "document": "用FastAPI", "distance": 0.0, "metadata": {}}
        ]
        self.ext.memory = fake_memory
        memories = [{"content": "用FastAPI", "type": "preference"}]
        count = self.ext.store_memories(memories)
        assert count == 0
        fake_memory.remember.assert_not_called()

    def test_high_similarity_skip(self):
        fake_memory = mock.Mock()
        fake_memory.recall_with_scores.return_value = [
            {"id": "m2", "document": "用FastAPI框架", "distance": 0.3, "metadata": {}}
        ]
        self.ext.memory = fake_memory
        memories = [{"content": "用FastAPI", "type": "preference"}]
        count = self.ext.store_memories(memories)
        assert count == 0
        fake_memory.remember.assert_not_called()

    def test_low_similarity_store(self):
        fake_memory = mock.Mock()
        fake_memory.recall_with_scores.return_value = [
            {"id": "m3", "document": "喜欢喝咖啡", "distance": 0.95, "metadata": {}}
        ]
        self.ext.memory = fake_memory
        memories = [{"content": "用FastAPI", "type": "preference"}]
        count = self.ext.store_memories(memories)
        assert count == 1
        fake_memory.remember.assert_called_once()

    def test_empty_content_skip(self):
        fake_memory = mock.Mock()
        self.ext.memory = fake_memory
        memories = [{"content": "", "type": "fact"}]
        count = self.ext.store_memories(memories)
        assert count == 0
        fake_memory.remember.assert_not_called()

    def test_missing_type_field(self):
        fake_memory = mock.Mock()
        fake_memory.recall_with_scores.return_value = []
        self.ext.memory = fake_memory
        memories = [{"content": "hello"}]
        count = self.ext.store_memories(memories)
        assert count == 1
        call_args = fake_memory.remember.call_args
        assert call_args[1]["metadata"]["type"] == "fact"

    def test_empty_memories_list(self):
        fake_memory = mock.Mock()
        self.ext.memory = fake_memory
        count = self.ext.store_memories([])
        assert count == 0
        fake_memory.remember.assert_not_called()
