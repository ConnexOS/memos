import json
from unittest import mock
from memos.engine.extractor import MemoryExtractor


class TestExtractAndStore:
    def setup_method(self):
        self.ext = MemoryExtractor()

    def test_full_flow(self):
        fake_memory = mock.Mock()
        fake_memory.recall_with_scores.return_value = []
        self.ext.memory = fake_memory

        expected_memories = [{"content": "用FastAPI", "type": "preference"}]
        with mock.patch("memos.engine.extractor.requests.post") as mock_post:
            mock_resp = mock.Mock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"content": json.dumps(expected_memories)}
            mock_resp.text = json.dumps(expected_memories)
            mock_post.return_value = mock_resp

            count = self.ext.extract_and_store("我们用FastAPI还是Django？")
            assert count == 1
            fake_memory.remember.assert_called_once()

    def test_extract_returns_empty(self):
        self.ext.memory = None
        with mock.patch("memos.engine.extractor.requests.post") as mock_post:
            mock_resp = mock.Mock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"content": "[]"}
            mock_resp.text = "[]"
            mock_post.return_value = mock_resp

            count = self.ext.extract_and_store("test conversation")
            assert count == 0


