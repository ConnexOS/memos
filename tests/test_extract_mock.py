import json
from unittest import mock
from memos.engine.extractor import MemoryExtractor


class TestExtractMock:
    def setup_method(self):
        self.ext = MemoryExtractor()

    def _mock_response(self, content_text, status_code=200):
        m = mock.Mock()
        m.status_code = status_code
        m.json.return_value = {"content": content_text}
        m.text = content_text
        return m

    def test_valid_json_array(self):
        expected = [{"content": "用FastAPI", "type": "preference"}]
        with mock.patch("memos.engine.extractor.requests.post", return_value=self._mock_response(json.dumps(expected))):
            result = self.ext.extract("test conversation")
        assert result == expected

    def test_empty_array(self):
        with mock.patch("memos.engine.extractor.requests.post", return_value=self._mock_response("[]")):
            result = self.ext.extract("test conversation")
        assert result == []

    def test_mixed_types(self):
        expected = [
            {"content": "使用FastAPI", "type": "decision"},
            {"content": "喜欢Python", "type": "preference"},
            {"content": "写测试", "type": "todo"},
            {"content": "Python是动态语言", "type": "fact"},
        ]
        with mock.patch("memos.engine.extractor.requests.post", return_value=self._mock_response(json.dumps(expected))):
            result = self.ext.extract("test conversation")
        assert result == expected

    def test_markdown_wrapped_json(self):
        content = '```json\n[{"content":"用FastAPI","type":"preference"}]\n```'
        with mock.patch("memos.engine.extractor.requests.post", return_value=self._mock_response(content)):
            result = self.ext.extract("test conversation")
        assert result == [{"content": "用FastAPI", "type": "preference"}]

    def test_non_json_response(self):
        with mock.patch("memos.engine.extractor.requests.post", return_value=self._mock_response("我不确定")):
            result = self.ext.extract("test conversation")
        assert result == []

    def test_llm_service_down(self):
        with mock.patch("memos.engine.extractor.requests.post", side_effect=Exception("Connection refused")):
            result = self.ext.extract("test conversation")
        assert result == []

    def test_http_500(self):
        with mock.patch(
            "memos.engine.extractor.requests.post", return_value=self._mock_response("Internal Error", status_code=500)
        ):
            result = self.ext.extract("test conversation")
        assert result == []

    def test_null_content(self):
        with mock.patch("memos.engine.extractor.requests.post", return_value=self._mock_response("null")):
            result = self.ext.extract("test conversation")
        assert result == []

    def test_extra_text_around_json(self):
        with mock.patch(
            "memos.engine.extractor.requests.post",
            return_value=self._mock_response('好的，以下是记忆：[{"content":"用FastAPI","type":"preference"}]'),
        ):
            result = self.ext.extract("test conversation")
        assert result == [{"content": "用FastAPI", "type": "preference"}]

    def test_single_object_not_array(self):
        with mock.patch(
            "memos.engine.extractor.requests.post", return_value=self._mock_response('{"content":"...","type":"fact"}')
        ):
            result = self.ext.extract("test conversation")
        assert len(result) == 1
        assert result[0]["content"] == "..."
        assert result[0]["type"] == "fact"

    # --- Sprint 3 T6: quality_score ---

    def test_quality_score_preserved(self):
        """LLM 返回含 quality_score 时，提取结果中保留该字段。"""
        expected = [{"content": "用FastAPI", "type": "decision", "quality_score": 0.85, "quality_reason": "信息完整"}]
        with mock.patch("memos.engine.extractor.requests.post", return_value=self._mock_response(json.dumps(expected))):
            result = self.ext.extract("test conversation")
        assert result == expected
        assert result[0]["quality_score"] == 0.85

    def test_quality_score_missing_default(self):
        """LLM 未返回 quality_score 时，extract 不添加默认值（解析层透传）。"""
        expected = [{"content": "用FastAPI", "type": "decision"}]
        with mock.patch("memos.engine.extractor.requests.post", return_value=self._mock_response(json.dumps(expected))):
            result = self.ext.extract("test conversation")
        assert "quality_score" not in result[0]

    def test_quality_score_in_store(self, fake_memory):
        """store_memories 处理 quality_score：缺失时默认 0.5。"""
        ext = MemoryExtractor(memory_system=fake_memory)
        memories = [{"content": "测试记忆", "type": "fact"}]
        ext.store_memories(memories)
        call_meta = fake_memory.remember.call_args[1]["metadata"]
        assert call_meta.get("quality_score") == 0.5

    def test_quality_score_passed_to_store(self, fake_memory):
        """store_memories 透传 LLM 返回的 quality_score。"""
        ext = MemoryExtractor(memory_system=fake_memory)
        memories = [{"content": "测试记忆", "type": "fact", "quality_score": 0.9, "quality_reason": "高质量"}]
        ext.store_memories(memories)
        call_meta = fake_memory.remember.call_args[1]["metadata"]
        assert call_meta.get("quality_score") == 0.9
        assert call_meta.get("quality_reason") == "高质量"
