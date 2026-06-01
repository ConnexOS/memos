import time
from unittest import mock
from memos.engine.memory import ContextMemory

from tests.conftest import clean_collection

COLLECTION = "test_filter"


class TestRecallFilterEmpty:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_recall_with_type_filter_empty(self):
        results = self.mem.recall("test", where={"type": "decision"})
        assert results == []

    def test_recall_with_days_limit_empty(self):
        results = self.mem.recall("test", days_limit=7)
        assert results == []


class TestRecallTypeFilter:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)
        self.mem.remember("决定使用FastAPI", {"type": "decision"})
        self.mem.remember("喜欢Python语言", {"type": "preference"})

    def test_type_filter_decision(self):
        results = self.mem.recall("FastAPI", top_k=5, where={"type": "decision"})
        assert "决定使用FastAPI" in results
        assert "喜欢Python语言" not in results

    def test_type_filter_preference(self):
        results = self.mem.recall("Python", top_k=5, where={"type": "preference"})
        assert "喜欢Python语言" in results
        assert "决定使用FastAPI" not in results

    def test_no_filter_returns_all(self):
        results = self.mem.recall("语言", top_k=5)
        combined = " ".join(results)
        assert "决定使用FastAPI" in combined
        assert "喜欢Python语言" in combined

    def test_recall_with_scores_type_filter(self):
        results = self.mem.recall_with_scores("FastAPI", top_k=5, where={"type": "decision"})
        docs = [r["document"] for r in results]
        assert "决定使用FastAPI" in docs
        assert "喜欢Python语言" not in docs


class TestRecallDaysLimit:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)
        self.mem.remember("今天是新决策", {"type": "decision"})
        old_ts = time.time() - 30 * 86400
        with mock.patch("memos.engine.memory.time.time", return_value=old_ts):
            self.mem.remember("三十天前的旧决策", {"type": "decision"})

    def test_days_limit_7_excludes_old(self):
        results = self.mem.recall("决策", top_k=5, days_limit=7)
        combined = " ".join(results)
        assert "今天是新决策" in combined
        assert "三十天前的旧决策" not in combined

    def test_days_limit_60_includes_old(self):
        results = self.mem.recall("决策", top_k=5, days_limit=60)
        combined = " ".join(results)
        assert "今天是新决策" in combined
        assert "三十天前的旧决策" in combined

    def test_no_days_limit_includes_all(self):
        results = self.mem.recall("决策", top_k=5)
        combined = " ".join(results)
        assert "今天是新决策" in combined
        assert "三十天前的旧决策" in combined


class TestRecallProjectId:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)
        self.mem.remember("项目A的决策", {"type": "decision", "project_id": "proj_a"})
        self.mem.remember("项目B的决策", {"type": "decision", "project_id": "proj_b"})

    def test_project_isolation(self):
        results_a = self.mem.recall("决策", top_k=5, project_id="proj_a")
        combined_a = " ".join(results_a)
        assert "项目A的决策" in combined_a
        assert "项目B的决策" not in combined_a

        results_b = self.mem.recall("决策", top_k=5, project_id="proj_b")
        combined_b = " ".join(results_b)
        assert "项目B的决策" in combined_b
        assert "项目A的决策" not in combined_b

    def test_no_project_id_finds_none(self):
        results = self.mem.recall("决策", top_k=5, project_id="nonexistent")
        assert results == []



class TestApplyFeedbackToSource:
    """反馈反哺端到端验证（验收 1、2、4）"""
    COLLECTION = "test_feedback_source"

    def setup_method(self):
        self.mem = ContextMemory(self.COLLECTION)
        clean_collection(self.mem)

    def test_useful_increments_counter(self):
        """标记有用 → reuse_count +1（v0.4.6 重构后统一为 reuse_count）"""
        mem_id = self.mem.remember("测试源记忆", {"type": "fact"})
        self.mem._apply_feedback_to_source(mem_id, "useful")
        updated = self.mem.get_memory(mem_id)
        assert updated["metadata"].get("reuse_count") == 1
        assert updated["metadata"].get("last_feedback_at") is not None

    def test_not_useful_increments_counter(self):
        """标记无用 → reuse_count -1（最低 0）"""
        mem_id = self.mem.remember("测试源记忆", {"type": "fact"})
        self.mem._apply_feedback_to_source(mem_id, "not_useful")
        updated = self.mem.get_memory(mem_id)
        assert updated["metadata"].get("reuse_count", 0) == 0
        assert updated["metadata"].get("last_feedback_at") is not None

    def test_multiple_feedback_accumulates(self):
        """多次反馈累加 reuse_count"""
        mem_id = self.mem.remember("测试源记忆", {"type": "fact"})
        self.mem._apply_feedback_to_source(mem_id, "useful")
        self.mem._apply_feedback_to_source(mem_id, "useful")
        self.mem._apply_feedback_to_source(mem_id, "not_useful")
        updated = self.mem.get_memory(mem_id)
        assert updated["metadata"].get("reuse_count") == 1

    def test_empty_source_id_skips(self):
        """空 source_memory_id → 跳过"""
        self.mem._apply_feedback_to_source("", "useful")  # 不应抛异常

    def test_nonexistent_source_skips(self):
        """源记忆不存在 → 跳过"""
        self.mem._apply_feedback_to_source("nonexistent_id", "useful")  # 不应抛异常


class TestRecallCombinedFilter:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)
        old_ts = time.time() - 30 * 86400
        with mock.patch("memos.engine.memory.time.time", return_value=old_ts):
            self.mem.remember("旧项目A决策", {"type": "decision", "project_id": "proj_a"})
        self.mem.remember("新项目A决策", {"type": "decision", "project_id": "proj_a"})
        self.mem.remember("新项目A偏好", {"type": "preference", "project_id": "proj_a"})

    def test_project_plus_type(self):
        results = self.mem.recall("决策", top_k=5, where={"type": "decision"}, project_id="proj_a")
        combined = " ".join(results)
        assert "新项目A决策" in combined
        assert "新项目A偏好" not in combined

    def test_project_plus_days(self):
        results = self.mem.recall("决策", top_k=5, days_limit=7, project_id="proj_a")
        combined = " ".join(results)
        assert "新项目A决策" in combined
        assert "旧项目A决策" not in combined

    def test_three_way_filter(self):
        results = self.mem.recall("决策", top_k=5, where={"type": "decision"}, days_limit=7, project_id="proj_a")
        assert "新项目A决策" in results
        assert "旧项目A决策" not in results
        assert "新项目A偏好" not in results


class TestListMemories:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)
        self.mem.remember("记忆A", {"type": "decision", "project_id": "proj_x"})
        self.mem.remember("记忆B", {"type": "preference", "project_id": "proj_x"})
        self.mem.remember("记忆C", {"type": "fact", "project_id": "proj_x"})
        self.mem.remember("记忆D", {"type": "todo", "project_id": "proj_y"})

    def test_list_all_by_project(self):
        items = self.mem.list_memories(project_id="proj_x")
        assert len(items) == 3
        docs = [i["document"] for i in items]
        assert "记忆A" in docs
        assert "记忆B" in docs
        assert "记忆C" in docs
        assert "记忆D" not in docs

    def test_list_with_type_filter(self):
        items = self.mem.list_memories(project_id="proj_x", type_filter="decision")
        assert len(items) == 1
        assert items[0]["document"] == "记忆A"

    def test_list_pagination(self):
        items = self.mem.list_memories(project_id="proj_x", limit=2, offset=0)
        assert len(items) == 2

    def test_list_offset(self):
        items = self.mem.list_memories(project_id="proj_x", limit=10, offset=10)
        assert items == []

    def test_list_empty_project(self):
        items = self.mem.list_memories(project_id="nonexistent")
        assert items == []


class TestRecallBackwardCompatExtended:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)
        self.mem.remember("决定使用Redis缓存", {"type": "decision"})

    def test_old_recall_still_works(self):
        results = self.mem.recall("Redis")
        assert "决定使用Redis缓存" in results
        for r in results:
            assert isinstance(r, str)

    def test_old_recall_with_scores_still_works(self):
        results = self.mem.recall_with_scores("Redis")
        assert len(results) > 0
        r = results[0]
        assert "Redis" in r["document"]
        assert isinstance(r["distance"], float)
        assert "id" in r


class TestProjectIdAutoDetect:
    def test_detect_project_id_format(self):
        from memos.server.mcp import _detect_project_id

        pid = _detect_project_id()
        assert isinstance(pid, str)
        assert len(pid) == 8


class TestQualityScoreFilter:
    """quality_score < 0.30 前置过滤（Sprint 3 T6）"""
    COLLECTION = "test_quality_score"

    def setup_method(self):
        self.mem = ContextMemory(self.COLLECTION)
        clean_collection(self.mem)

    def _remember_with_quality(self, text: str, score: float):
        """以指定 quality_score 写入记忆。"""
        self.mem.remember(text, {"type": "fact", "quality_score": score})

    def test_low_quality_filtered(self):
        """quality_score < 0.30 的记忆在 recall 中被跳过。"""
        self._remember_with_quality("低质量记忆内容", 0.20)
        results = self.mem.recall("记忆", top_k=5)
        assert "低质量记忆内容" not in results

    def test_normal_quality_included(self):
        """quality_score >= 0.30 的记忆正常返回。"""
        self._remember_with_quality("正常质量记忆", 0.50)
        self._remember_with_quality("高质量记忆", 0.90)
        results = self.mem.recall("记忆", top_k=5)
        assert "正常质量记忆" in results
        assert "高质量记忆" in results

    def test_old_memory_no_quality_default(self):
        """旧记忆无 quality_score 字段时默认 0.5，不被过滤。"""
        self.mem.remember("旧格式记忆（无quality_score）", {"type": "fact"})
        results = self.mem.recall("记忆", top_k=5)
        assert "旧格式记忆（无quality_score）" in results

    def test_rerank_compensation(self):
        """过滤补偿：前 10 条含 2 条低质量时实际参与排序 ≥ 8 条（rerank_multiplier 自动补偿）。"""
        # 写 6 条高质量 + 2 条低质量 = 8 条
        for i in range(6):
            self._remember_with_quality(f"高质量记忆_{i}", 0.80)
        self._remember_with_quality("低质量_A", 0.20)
        self._remember_with_quality("低质量_B", 0.15)

        # top_k=5, rerank_mult=3 → ChromaDB 查询 15 条，过滤后仍有 ≥5 条可用
        results = self.mem.recall("记忆", top_k=5)
        assert len(results) == 5, f"应有 5 条结果，实际 {len(results)}"
        # 低质量记忆不应出现在结果中
        combined = " ".join(results)
        assert "低质量_A" not in combined
        assert "低质量_B" not in combined


class TestExtractorProjectId:
    def test_store_memories_injects_project_id(self, fake_memory):
        from memos.engine.extractor import MemoryExtractor

        ext = MemoryExtractor(memory_system=fake_memory, project_id="test_proj")
        memories = [{"content": "用FastAPI", "type": "decision"}]
        count = ext.store_memories(memories)
        assert count == 1
        call_kwargs = fake_memory.remember.call_args[1]
        assert call_kwargs["metadata"]["project_id"] == "test_proj"

    def test_store_memories_uses_project_in_dedup(self, fake_memory):
        from memos.engine.extractor import MemoryExtractor

        ext = MemoryExtractor(memory_system=fake_memory, project_id="proj_x")
        ext.store_memories([{"content": "test", "type": "fact"}])
        call_kwargs = fake_memory.recall_with_scores.call_args[1]
        assert call_kwargs.get("project_id") == "proj_x"

    def test_no_project_id_works(self, fake_memory):
        from memos.engine.extractor import MemoryExtractor

        ext = MemoryExtractor(memory_system=fake_memory)
        ext.store_memories([{"content": "test", "type": "fact"}])
        call_kwargs = fake_memory.remember.call_args[1]
        assert "project_id" not in call_kwargs["metadata"]
