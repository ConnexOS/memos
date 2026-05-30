"""S3 — F3 Hook 分层检索单元测试 (v0.4.4)

覆盖：分层筛选、阈值边界、格式模板、冷却期、免打扰文件、expiry_days=0。

注：_build_layered_context 不再格式化 context_str，统一由 main() 排序截断后格式化。
    测试对 3rd 返回值（injected_items）做断言。
"""

import time
from unittest.mock import MagicMock, patch

import pytest


def _make_recall_result(doc_id, document, similarity, type="fact", timestamp=None,
                        feedback=None, final_score=None):
    """构造模拟 recall 返回结果项。"""
    if timestamp is None:
        timestamp = time.time() - 3600  # 1 小时前
    meta = {
        "type": type,
        "project_id": "default",
        "timestamp": timestamp,
    }
    if feedback is not None:
        meta["feedback"] = feedback
    if final_score is None:
        final_score = similarity * 0.98
    return {
        "id": doc_id,
        "document": document,
        "metadata": meta,
        "similarity": similarity,
        "decay_factor": 0.98,
        "final_score": final_score,
    }


class TestBuildLayeredContext:
    """_build_layered_context 分层筛选逻辑。"""

    @pytest.fixture
    def mock_mem(self):
        mem = MagicMock()
        mem.config.memory.default_project_id = "default"
        return mem

    @pytest.fixture
    def mock_config(self, monkeypatch):
        """Mock SuggestionConfig 的阈值和开关。"""
        from memos.config.models import SuggestionConfig

        cfg = SuggestionConfig(
            context_injection_threshold=0.55,
            active_suggestion_threshold=0.75,
            context_max_items=3,
            enable_active_suggestions=True,
        )
        monkeypatch.setattr("memos.hooks.prompt._get_suggestion_config", lambda: cfg)
        return cfg

    def _import_func(self):
        from memos.hooks.prompt import _build_layered_context

        return _build_layered_context

    def test_empty_query_returns_empty(self, mock_mem, mock_config):
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "", "default")
        assert ctx == ""
        assert sug == []
        assert len(injected) == 0

    def test_no_results_returns_empty(self, mock_mem, mock_config):
        mock_mem.recall.return_value = []
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test query", "default")
        assert ctx == ""
        assert sug == []
        assert len(injected) == 0

    def test_low_similarity_skipped(self, mock_mem, mock_config):
        """相似度低于 context_injection_threshold 的结果全部跳过。"""
        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "低质量结果", similarity=0.3),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        assert ctx == ""
        assert sug == []
        assert len(injected) == 0

    def test_layer1_only(self, mock_mem, mock_config):
        """中等相似度 (0.55-0.75) 进 Layer 1，不进 Layer 2。"""
        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "不错的结果", similarity=0.6),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        assert len(sug) == 0
        assert len(injected) == 1
        assert injected[0]["document"] == "不错的结果"

    def test_layer2_candidate(self, mock_mem, mock_config):
        """高相似度 (≥0.75) 同时进入 Layer 1 和 Layer 2。"""
        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "高质量结果", similarity=0.85),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        assert len(sug) == 1
        assert sug[0]["id"] == "id-1"
        assert len(injected) == 1

    def test_context_max_items_limit(self, mock_mem, mock_config):
        """Layer 1 不超过 context_max_items 条，由多样性采样控制。"""
        mock_mem.recall.return_value = [
            _make_recall_result(f"id-{i}", f"结果{i}", similarity=0.6 + i * 0.05, type=t)
            for i, t in enumerate(["decision", "fact", "preference", "todo", "bug_fix"])
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        # 5 个不同 type，全部 ≥ threshold=0.55 → 多样性允许全部进入
        assert len(injected) == 5

    def test_disable_active_suggestions(self, mock_mem, mock_config):
        """全局开关关闭时 Layer 2 不产生建议。"""
        mock_config.enable_active_suggestions = False
        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "高质量", similarity=0.85),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        assert len(sug) == 0
        assert len(injected) == 1  # Layer 1 仍工作

    def test_no_suggestions_file_blocks_layer2(self, mock_mem, mock_config, tmp_path):
        """免打扰文件存在时 Layer 2 被阻断。"""
        no_sug = tmp_path / ".claude" / "no_suggestions"
        no_sug.parent.mkdir(parents=True)
        no_sug.write_text("{}", encoding="utf-8")

        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "高质量", similarity=0.85),
        ]

        with patch("memos.hooks.prompt.NO_SUGGESTIONS_FILE", no_sug):
            func = self._import_func()
            ctx, sug, injected = func(mock_mem, "test", "default")
        assert len(sug) == 0
        assert len(injected) == 1  # Layer 1 仍工作

    def test_retrieval_failure_returns_empty(self, mock_mem, mock_config):
        """检索异常时静默降级。"""
        mock_mem.recall.side_effect = Exception("DB error")
        func = self._import_func()
        ctx, sug, _ = func(mock_mem, "test", "default")
        assert ctx == ""
        assert sug == []

    # --- 基于阈值的筛选（替代原自适应注入逻辑）---

    def _make_sprint2_config(self, monkeypatch):
        """Sprint 2 阈值：context_injection=0.50, active_suggestion=0.65。"""
        from memos.config.models import SuggestionConfig
        cfg = SuggestionConfig(
            context_injection_threshold=0.50,
            active_suggestion_threshold=0.65,
            context_max_items=3,
            enable_active_suggestions=True,
        )
        monkeypatch.setattr("memos.hooks.prompt._get_suggestion_config", lambda: cfg)
        return cfg

    def test_final_score_sorting(self, mock_mem, monkeypatch):
        """验收 1 — Layer 1 按 final_score（含 feedback_boost）降序排列。"""
        self._make_sprint2_config(monkeypatch)
        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "低分结果", similarity=0.70, final_score=0.60, type="todo"),
            _make_recall_result("id-2", "高分结果", similarity=0.72, final_score=0.75, type="decision"),
            _make_recall_result("id-3", "中分结果", similarity=0.71, final_score=0.68, type="fact"),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        # Layer 1 按 final_score 降序排列
        assert injected[0]["document"] == "高分结果"  # final_score=0.75
        assert injected[1]["document"] == "中分结果"  # final_score=0.68
        assert injected[2]["document"] == "低分结果"  # final_score=0.60

    def test_expanded_retrieval_pool(self, mock_mem, monkeypatch):
        """验收 2 — 扩展检索池含 type=suggestion, feedback=useful。
        v0.4.4 P1-2: 拆分后两次 recall 调用，分别查询知识库类型 + 有用建议。"""
        self._make_sprint2_config(monkeypatch)
        mock_mem.recall.return_value = []
        func = self._import_func()
        func(mock_mem, "test", "default")
        assert mock_mem.recall.call_count == 2
        call1_where = mock_mem.recall.call_args_list[0][1]["where"]
        call2_where = mock_mem.recall.call_args_list[1][1]["where"]
        assert "type" in call1_where and "$in" in call1_where["type"]
        assert "fact" in call1_where["type"]["$in"]
        assert call2_where == {"$and": [{"type": "suggestion"}, {"feedback": "useful"}]}

    def test_expanded_retrieval_returns_useful_suggestion(self, mock_mem, monkeypatch):
        """扩展检索能返回 type=suggestion, feedback=useful 的记忆。"""
        self._make_sprint2_config(monkeypatch)
        mock_mem.recall.return_value = [
            _make_recall_result("sug-1", "之前有用的建议", similarity=0.80,
                                type="suggestion", feedback="useful"),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        assert len(sug) == 0
        assert len(injected) == 1
        assert injected[0]["document"] == "之前有用的建议"

    def test_threshold_below_fifty(self, mock_mem, monkeypatch):
        """相似度 < 0.50 → 不注入。"""
        self._make_sprint2_config(monkeypatch)
        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "边界结果", similarity=0.49),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        assert len(injected) == 0

    def test_threshold_at_fifty(self, mock_mem, monkeypatch):
        """相似度 = 0.50 → 注入（≥ threshold）。"""
        self._make_sprint2_config(monkeypatch)
        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "边界结果", similarity=0.50, type="decision"),
            _make_recall_result("id-2", "额外结果", similarity=0.48, type="fact"),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        assert len(injected) == 1
        assert injected[0]["id"] == "id-1"

    def test_threshold_at_fiftyfive(self, mock_mem, monkeypatch):
        """同类通过阈值筛选 3 进 2（多样性同 type 限 2 条）。"""
        self._make_sprint2_config(monkeypatch)
        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "结果A", similarity=0.55, type="decision"),
            _make_recall_result("id-2", "结果B", similarity=0.52, type="fact"),
            _make_recall_result("id-3", "结果C", similarity=0.50, type="preference"),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        assert len(injected) == 3  # 三个不同 type，全部通过

    def test_diversity_sampling(self, mock_mem, monkeypatch):
        """验收 6 — 同 type 候选池，前 2 条可同 type，后续必须不同。"""
        self._make_sprint2_config(monkeypatch)
        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "同类型1", similarity=0.70, type="decision"),
            _make_recall_result("id-2", "同类型2", similarity=0.68, type="decision"),
            _make_recall_result("id-3", "同类型3", similarity=0.66, type="decision"),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        # 全部同 type → 最多进 2 条
        assert len(injected) == 2, "同 type 最多进 2 条"

    def test_diversity_sampling_mixed_types(self, mock_mem, monkeypatch):
        """多样性采样：混合 type 时全部进入。"""
        self._make_sprint2_config(monkeypatch)
        mock_mem.recall.return_value = [
            _make_recall_result("id-1", "决策结果", similarity=0.72, type="decision"),
            _make_recall_result("id-2", "事实结果", similarity=0.68, type="fact"),
            _make_recall_result("id-3", "偏好结果", similarity=0.62, type="preference"),
        ]
        func = self._import_func()
        ctx, sug, injected = func(mock_mem, "test", "default")
        assert len(injected) == 3  # 3 个不同 type


class TestFormatContextItem:
    """_format_context_item 格式化。"""

    def _import_func(self):
        from memos.hooks.prompt import _format_context_item

        return _format_context_item

    def test_basic_format(self):
        func = self._import_func()
        r = _make_recall_result("id-1", "这是一条测试记忆内容", similarity=0.88, type="fact")
        result = func(r)
        assert "[历史参考]" in result
        assert "fact" in result
        assert "88%" in result or "0.88" in result
        assert "测试记忆内容" in result

    def test_long_content_truncated(self):
        func = self._import_func()
        long_text = "A" * 200
        r = _make_recall_result("id-1", long_text, similarity=0.8)
        result = func(r)
        # 截断到 150 字符 + "…"
        assert len(long_text) > 150
        assert "A" * 150 in result
        assert "…" in result

    def test_unknown_date(self):
        func = self._import_func()
        r = _make_recall_result("id-1", "内容", similarity=0.8, timestamp=0)
        result = func(r)
        assert "unknown" in result


class TestCheckSuggestionCooldown:
    """_check_suggestion_cooldown 冷却期检查。"""

    def _import_func(self):
        from memos.hooks.prompt import _check_suggestion_cooldown

        return _check_suggestion_cooldown

    def test_within_cooldown(self):
        func = self._import_func()
        mem = MagicMock()
        mem.store.get.return_value = {"ids": ["existing-sug"]}
        result = func(mem, "source-1", "default", 30)
        assert result is True  # 冷却期内 → 跳过

    def test_outside_cooldown(self):
        func = self._import_func()
        mem = MagicMock()
        mem.store.get.return_value = {"ids": []}
        result = func(mem, "source-1", "default", 30)
        assert result is False  # 不在冷却期

    def test_cooldown_zero_disabled(self):
        func = self._import_func()
        mem = MagicMock()
        result = func(mem, "source-1", "default", 0)
        assert result is False  # 冷却期为 0 不检查

    def test_query_failure_does_not_block(self):
        func = self._import_func()
        mem = MagicMock()
        mem.store.get.side_effect = Exception("DB error")
        result = func(mem, "source-1", "default", 30)
        assert result is False  # 失败降级 → 不阻断


class TestCheckDailyLimit:
    """_check_daily_limit 每日上限检查。"""

    def _import_func(self):
        from memos.hooks.prompt import _check_daily_limit

        return _check_daily_limit

    def test_below_limit(self):
        func = self._import_func()
        mem = MagicMock()
        mem.store.count.return_value = 3
        result = func(mem, "default", 10)
        assert result is False  # 未达上限

    def test_at_limit(self):
        func = self._import_func()
        mem = MagicMock()
        mem.store.count.return_value = 10
        result = func(mem, "default", 10)
        assert result is True  # 已达上限

    def test_zero_max_blocks(self):
        func = self._import_func()
        mem = MagicMock()
        result = func(mem, "default", 0)
        assert result is True  # max_per_day=0 → 不允许推送

    def test_query_failure_blocks(self):
        func = self._import_func()
        mem = MagicMock()
        mem.store.count.side_effect = Exception("DB error")
        result = func(mem, "default", 10)
        assert result is True  # 异常降级 → 阻断


class TestNoSuggestionsFile:
    """_no_suggestions_file_exists 免打扰文件检测。"""

    def _import_func(self):
        from memos.hooks.prompt import _no_suggestions_file_exists

        return _no_suggestions_file_exists

    def test_no_file(self, tmp_path):
        func = self._import_func()
        no_sug = tmp_path / ".claude" / "no_suggestions"
        with patch("memos.hooks.prompt.NO_SUGGESTIONS_FILE", no_sug):
            result = func()
        assert result is False

    def test_file_exists(self, tmp_path):
        func = self._import_func()
        no_sug = tmp_path / ".claude" / "no_suggestions"
        no_sug.parent.mkdir(parents=True)
        no_sug.write_text("{}", encoding="utf-8")
        with patch("memos.hooks.prompt.NO_SUGGESTIONS_FILE", no_sug):
            result = func()
        assert result is True

    def test_bad_json_still_blocks(self, tmp_path):
        """文件存在但 JSON 解析失败，仍应阻断。"""
        func = self._import_func()
        no_sug = tmp_path / ".claude" / "no_suggestions"
        no_sug.parent.mkdir(parents=True)
        no_sug.write_text("invalid json", encoding="utf-8")
        with patch("memos.hooks.prompt.NO_SUGGESTIONS_FILE", no_sug):
            result = func()
        assert result is True
