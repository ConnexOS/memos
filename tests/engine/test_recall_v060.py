"""F9: L3 注入适配测试。"""


class TestV060Types:
    """V060_KNOWLEDGE_TYPES 常量验证。"""

    def test_v060_types_contain_new_types(self):
        from memos.engine.memory import V060_KNOWLEDGE_TYPES

        assert "task" in V060_KNOWLEDGE_TYPES
        assert "briefing" in V060_KNOWLEDGE_TYPES
        assert "solution" in V060_KNOWLEDGE_TYPES
        assert "decision" in V060_KNOWLEDGE_TYPES
        assert "lesson" in V060_KNOWLEDGE_TYPES
        assert "process" in V060_KNOWLEDGE_TYPES
        assert len(V060_KNOWLEDGE_TYPES) == 6

    def test_all_recall_types_includes_old_types(self):
        from memos.engine.memory import ALL_RECALL_TYPES

        assert "fact" in ALL_RECALL_TYPES
        assert "preference" in ALL_RECALL_TYPES
        assert "bug_fix" in ALL_RECALL_TYPES
        assert "feature_design" in ALL_RECALL_TYPES
        assert "code_optimize" in ALL_RECALL_TYPES
        assert "tech_knowledge" in ALL_RECALL_TYPES

    def test_watchlist_excluded(self):
        from memos.engine.memory import ALL_RECALL_TYPES

        assert "watchlist" not in ALL_RECALL_TYPES

    def test_v060_is_subset_of_all(self):
        from memos.engine.memory import ALL_RECALL_TYPES, V060_KNOWLEDGE_TYPES

        assert V060_KNOWLEDGE_TYPES.issubset(ALL_RECALL_TYPES)


class TestPromptTypes:
    """prompt.py 类型列表验证。"""

    def test_knowledge_types_for_matching_has_new_types(self):
        from memos.hooks.prompt import _KNOWLEDGE_TYPES_FOR_MATCHING

        assert "task" not in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert "briefing" not in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert "solution" in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert "decision" in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert "lesson" in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert "process" in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert len(_KNOWLEDGE_TYPES_FOR_MATCHING) == 4

    def test_matching_types_excludes_old_types(self):
        from memos.hooks.prompt import _KNOWLEDGE_TYPES_FOR_MATCHING

        assert "fact" not in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert "preference" not in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert "bug_fix" not in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert "feature_design" not in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert "code_optimize" not in _KNOWLEDGE_TYPES_FOR_MATCHING
        assert "tech_knowledge" not in _KNOWLEDGE_TYPES_FOR_MATCHING
