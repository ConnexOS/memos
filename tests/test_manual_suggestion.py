"""管道三手工建议 —— 12+ 测试用例 (v0.4.4 增强版 Phase 3)"""

import json
import time
from unittest import mock

from memos.hooks.prompt import _match_manual_suggestions


def _make_manual_suggestion(
    doc_id="sug_1",
    content="试试用新框架重构",
    keywords=None,
    trigger_mode="keyword",
    cooldown_minutes=60,
    expires_at=0,
    hit_count=0,
    last_triggered=0,
    priority="medium",
    created_by="user",
):
    """创建一条手工建议的 mock 存储数据。"""
    if keywords is None:
        keywords = ["重构", "新框架"]
    return {
        "id": doc_id,
        "document": content,
        "metadata": {
            "type": "manual_suggestion",
            "project_id": "test_pid",
            "trigger_keywords": json.dumps(keywords),
            "trigger_mode": trigger_mode,
            "cooldown_minutes": cooldown_minutes,
            "expires_at": expires_at,
            "hit_count": hit_count,
            "last_triggered": last_triggered,
            "priority": priority,
            "created_by": created_by,
            "timestamp": time.time(),
        },
    }


def _make_mem_with_suggestions(suggestions):
    """创建包含手工建议的 mock memory。"""
    mem = mock.Mock()
    store = mock.Mock()
    ids = [s["id"] for s in suggestions]
    docs = [s["document"] for s in suggestions]
    metas = [s["metadata"] for s in suggestions]
    store.get.return_value = {"ids": ids, "documents": docs, "metadatas": metas}
    store.update.return_value = None
    mem.store = store
    return mem


class TestMatchManualSuggestions:
    def test_keyword_exact_match_triggers(self):
        """关键词精确匹配触发。"""
        sug = _make_manual_suggestion(keywords=["重构"])
        mem = _make_mem_with_suggestions([sug])
        result = _match_manual_suggestions("我们用新框架重构吧", mem, "test_pid")
        assert len(result) == 1
        assert result[0]["suggestion_type"] == "manual_trigger"

    def test_keyword_case_insensitive(self):
        """不区分大小写匹配。"""
        sug = _make_manual_suggestion(keywords=["refactor"])
        mem = _make_mem_with_suggestions([sug])
        result = _match_manual_suggestions("Let's REFACTOR the code", mem, "test_pid")
        assert len(result) == 1

    def test_special_characters_match(self):
        """特殊字符关键词（C++, [bug]）正常匹配。"""
        sug = _make_manual_suggestion(keywords=["C++", "[bug]"])
        mem = _make_mem_with_suggestions([sug])
        result = _match_manual_suggestions("修复了 C++ 模块的 [bug]", mem, "test_pid")
        assert len(result) == 1

    def test_trigger_mode_always_matches(self):
        """trigger_mode=always 始终触发。"""
        sug = _make_manual_suggestion(keywords=["任何关键词"], trigger_mode="always")
        mem = _make_mem_with_suggestions([sug])
        result = _match_manual_suggestions("随便什么消息都会触发", mem, "test_pid")
        assert len(result) == 1
        assert result[0]["similarity"] == 0.85  # always 模式固定 0.85

    def test_cooldown_blocks(self):
        """冷却期内不重复触发。"""
        now = time.time()
        sug = _make_manual_suggestion(
            keywords=["重构"], cooldown_minutes=60,
            last_triggered=now - 300,  # 5 分钟前（冷却期内）
        )
        mem = _make_mem_with_suggestions([sug])
        result = _match_manual_suggestions("我们用新框架重构吧", mem, "test_pid")
        assert len(result) == 0

    def test_cooldown_elapsed_allows(self):
        """冷却期过后可再次触发。"""
        now = time.time()
        sug = _make_manual_suggestion(
            keywords=["重构"], cooldown_minutes=60,
            last_triggered=now - 7200,  # 2 小时前（冷却期已过）
        )
        mem = _make_mem_with_suggestions([sug])
        result = _match_manual_suggestions("我们用新框架重构吧", mem, "test_pid")
        assert len(result) == 1

    def test_expired_suggestion_skipped(self):
        """过期手工建议不触发。"""
        now = time.time()
        sug = _make_manual_suggestion(
            keywords=["重构"], expires_at=now - 86400,  # 1 天前过期
        )
        mem = _make_mem_with_suggestions([sug])
        result = _match_manual_suggestions("我们用新框架重构吧", mem, "test_pid")
        assert len(result) == 0

    def test_not_expired_still_triggers(self):
        """未过期建议正常触发。"""
        now = time.time()
        sug = _make_manual_suggestion(
            keywords=["重构"], expires_at=now + 86400,  # 明天过期
        )
        mem = _make_mem_with_suggestions([sug])
        result = _match_manual_suggestions("我们用新框架重构吧", mem, "test_pid")
        assert len(result) == 1

    def test_hit_count_increments(self):
        """命中后 hit_count 正确累加。"""
        sug = _make_manual_suggestion(
            doc_id="sug_hit", keywords=["重构"], hit_count=5,
        )
        mem = _make_mem_with_suggestions([sug])
        _match_manual_suggestions("我们用新框架重构吧", mem, "test_pid")
        # 验证 store.update 调用中包含 hit_count=6
        call_kwargs = mem.store.update.call_args
        assert call_kwargs is not None
        updated_meta = call_kwargs[1].get("metadatas", [{}])[0]
        assert updated_meta.get("hit_count") == 6

    def test_no_keywords_no_match(self):
        """空关键词列表不触发。"""
        sug = _make_manual_suggestion(keywords=[])
        mem = _make_mem_with_suggestions([sug])
        result = _match_manual_suggestions("随便什么消息", mem, "test_pid")
        assert len(result) == 0

    def test_json_keyword_deserialization(self):
        """trigger_keywords JSON 序列化/反序列化兼容。"""
        sug_data = _make_manual_suggestion(keywords=["Python", "FastAPI"])
        # 模拟 metadata 中的 keywords 是 str（JSON 序列化后的）
        sug_data["metadata"]["trigger_keywords"] = json.dumps(["Python", "FastAPI"])
        mem = _make_mem_with_suggestions([sug_data])
        result = _match_manual_suggestions("Python FastAPI 开发", mem, "test_pid")
        assert len(result) == 1

    def test_ai_cannot_set_always_mode(self):
        """AI 创建建议不能设 trigger_mode=always（MCP 层防守）。"""
        from memos.server.mcp import _save_manual_suggestion
        result = _save_manual_suggestion("test", {
            "trigger_keywords": ["test"],
            "trigger_mode": "always",
        })
        assert "仅限 Dashboard" in result

    def test_no_suggestions_file_blocks(self):
        """免打扰文件阻断管道三。"""
        sug = _make_manual_suggestion(keywords=["重构"])
        mem = _make_mem_with_suggestions([sug])
        with mock.patch("memos.hooks.prompt._no_suggestions_file_exists", return_value=True):
            result = _match_manual_suggestions("我们用新框架重构吧", mem, "test_pid")
            assert len(result) == 0

    def test_empty_message_returns_empty(self):
        """空消息返回空列表。"""
        result = _match_manual_suggestions("", _make_mem_with_suggestions([]), "test_pid")
        assert result == []

    def test_or_logic_multiple_keywords(self):
        """多关键词 OR 逻辑——任一匹配即触发。"""
        sug = _make_manual_suggestion(keywords=["A方案", "B方案", "C方案"])
        mem = _make_mem_with_suggestions([sug])
        # 只匹配 B方案
        result = _match_manual_suggestions("我们试试B方案吧", mem, "test_pid")
        assert len(result) == 1
