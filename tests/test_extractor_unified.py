"""Phase 2: extractor.py 接入 PromptManager 单元测试"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from memos.config import PromptManager, PromptTemplate
from memos.engine.extractor import MemoryExtractor, _extract_llm_content, _strip_think_block


@pytest.fixture
def isolated_env(monkeypatch):
    """隔离环境：临时 MEMOS_HOME + 端点专属提示词"""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "etc").mkdir(parents=True, exist_ok=True)
        (home / "memdb").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("MEMOS_HOME", str(home))

        config_data = {
            "chroma": {
                "mode": "persistent",
                "path": str(home / "memdb"),
                "collection_name": "test",
                "host": "localhost",
                "port": 8001,
                "timeout": 30,
            },
            "model": {"path": str(home / "model"), "vector_dim": 1024},
            "llm": {
                "endpoints": [
                    {"name": "deepseek-ai", "api_base": "http://ds/v1", "model": "deepseek-chat"},
                ],
                "active": "deepseek-ai",
                "temperature": 0.3,
                "max_tokens": 2048,
            },
            "memory": {},
            "buffer": {},
            "dashboard": {},
            "server": {},
            "prompt": {},
            "auth": {},
        }
        with open(home / "etc" / "config.json", "w") as f:
            json.dump(config_data, f)

        # 创建端点专属提示词模板（命名约定: endpoint@type）
        mgr = PromptManager(active_id="deepseek-ai")
        t = PromptTemplate(id="deepseek-ai@extract", template_type="extract")
        t._sync_from_legacy()
        t.save_draft(
            system_prompt="[提取知识] 从对话中提取技术要点，输出JSON数组",
            parameters={"temperature": 0.1},
        )
        mgr.upsert(t)
        mgr.save()

        # 重新加载配置
        from memos.config import MemoConfig

        cfg = MemoConfig.load()
        monkeypatch.setattr("memos.config.config", cfg)
        monkeypatch.setattr("memos.engine.extractor.config", cfg)
        monkeypatch.setattr("memos.engine.extractor._buf", cfg.buffer)
        monkeypatch.setattr("memos.engine.extractor._llm", cfg.llm)
        monkeypatch.setattr("memos.engine.extractor._mem", cfg.memory)
        yield home, cfg


class TestExtractorUsesPromptManager:
    """验证 extractor 从 PromptManager 获取提示词"""

    def test_build_payload_uses_endpoint_prompt(self, isolated_env):
        home, cfg = isolated_env
        ext = MemoryExtractor(llm_url="http://ds/v1/chat/completions")
        payload = ext._build_extract_payload("测试对话")
        msgs = payload["messages"]
        # 应有 system message 含端点专属提示词
        contents = " ".join(m.get("content", "") for m in msgs)
        assert "提取知识" in contents

    def test_build_payload_includes_template_parameters(self, isolated_env):
        home, cfg = isolated_env
        ext = MemoryExtractor(llm_url="http://ds/v1/chat/completions")
        payload = ext._build_extract_payload("测试对话")
        # 模板参数 temperature=0.1 应包含在 payload 中
        assert payload.get("temperature") == 0.1

    def test_extract_returns_parsed_json(self, isolated_env):
        home, cfg = isolated_env
        ext = MemoryExtractor(llm_url="http://ds/v1/chat/completions")
        # Mock LLM 返回有效 JSON
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {
            "content": json.dumps(
                [
                    {"content": "重要决策", "type": "decision"},
                    {"content": "用户偏好", "type": "preference"},
                ]
            )
        }
        with mock.patch("memos.engine.extractor.requests.post", return_value=resp):
            results = ext.extract("对话内容")
        assert len(results) == 2
        assert results[0]["type"] == "decision"

    def test_extract_with_prompt_version_param(self, isolated_env):
        """指定 prompt_version 参数应查找对应版本"""
        home, cfg = isolated_env
        # 先升级到 2.0.0
        t = cfg.prompt.get_for_endpoint("deepseek-ai")
        t._sync_from_legacy()
        t.save_draft(system_prompt="v2版本提示词[升级版]")
        t.upgrade("2.0.0", "升级")
        cfg.prompt.save()

        ext = MemoryExtractor(llm_url="http://ds/v1/chat/completions")
        payload = ext._build_extract_payload("测试", prompt_version="2.0.0")
        contents = " ".join(m.get("content", "") for m in payload["messages"])
        assert "升级版" in contents

    def test_fallback_when_prompt_manager_fails(self, isolated_env):
        home, cfg = isolated_env
        # 删除模板让 PromptManager 找不到
        cfg.prompt.templates.clear()
        ext = MemoryExtractor(llm_url="http://ds/v1/chat/completions")
        prompt = ext._get_prompt("deepseek-ai")
        # 应 fallback 到内置提示词
        assert "senior technical analyst" in prompt.system_prompt_text

    def test_no_hardcoded_prompt_in_extract(self, isolated_env):
        """验证 extractor 确实在使用 PromptManager，而非硬编码提示词"""
        home, cfg = isolated_env
        ext = MemoryExtractor(llm_url="http://ds/v1/chat/completions")
        payload = ext._build_extract_payload("测试")

        contents = " ".join(m.get("content", "") for m in payload["messages"])
        # 不应包含旧硬编码提示词的关键词
        assert "Read the conversation and extract important facts" not in contents
        # 应包含 PromptManager 提供的提示词
        assert "提取知识" in contents


class TestExtractHelperFunctions:
    """验证辅助函数未受影响"""

    def test_extract_llm_content_chat_format(self):
        resp = {"choices": [{"message": {"content": "测试响应"}}]}
        assert _extract_llm_content(resp) == "测试响应"

    def test_extract_llm_content_completion_format(self):
        resp = {"content": "测试响应"}
        assert _extract_llm_content(resp) == "测试响应"

    def test_extract_llm_content_empty(self):
        assert _extract_llm_content(None) == ""
        assert _extract_llm_content({}) == ""

    def test_strip_think_block_closed(self):
        text = '<think>推理中...</think>["valid"]'
        result = _strip_think_block(text)
        assert "<think>" not in result
        assert '["valid"]' in result

    def test_strip_think_block_unclosed(self):
        text = "<think>推理中..."
        result = _strip_think_block(text)
        assert result == ""

    def test_strip_think_block_none(self):
        text = "plain text without think block"
        result = _strip_think_block(text)
        assert result == text

    def test_strip_gemma_channel_closed(self):
        text = '<|channel|>Gemma scratchpad</channel|>["valid"]'
        result = _strip_think_block(text)
        assert "<|channel|>" not in result
        assert '["valid"]' in result

    def test_strip_gemma_channel_unclosed(self):
        text = "<|channel|>Gemma scratchpad no close"
        result = _strip_think_block(text)
        assert result == ""

    def test_strip_gemma_channel_variant_closed(self):
        text = '<|channel>Gemma reasoning<channel|>{"key": "value"}'
        result = _strip_think_block(text)
        assert "<|channel>" not in result
        assert '{"key": "value"}' in result

    def test_strip_think_block_mixed_think_and_channel(self):
        text = '<think>DeepSeek</think>middle<|channel|>Gemma</channel|>end'
        result = _strip_think_block(text)
        assert "<think>" not in result
        assert "<|channel|>" not in result
        assert result == "middleend"
