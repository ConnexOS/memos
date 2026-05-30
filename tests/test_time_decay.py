import time
from unittest import mock

import pytest

from memos.engine.memory import ContextMemory
from tests.conftest import clean_collection

COLLECTION = "test_timedecay"


class TestTimeDecay:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)
        self.mem.remember("今天的新决策", {"type": "decision"})
        old_ts = time.time() - 30 * 86400
        with mock.patch("memos.engine.memory.time.time", return_value=old_ts):
            self.mem.remember("三十天前的旧决策", {"type": "decision"})

    def test_decay_ranks_recent_first(self):
        results = self.mem.recall("决策", top_k=5)
        assert results[0] == "今天的新决策"

    def test_decay_lambda_zero_no_effect(self):
        results = self.mem.recall("决策", top_k=5, decay_lambda=0)
        assert "今天的新决策" in results
        assert "三十天前的旧决策" in results

    def test_decay_with_project_filter(self):
        self.mem.remember(
            "另一项目旧决策",
            {"type": "decision", "project_id": "other"},
        )
        results = self.mem.recall("决策", top_k=5, project_id="other")
        assert "另一项目旧决策" in results
        assert "今天的新决策" not in results

    def test_recall_with_scores_no_decay(self):
        results = self.mem.recall_with_scores("决策", top_k=5)
        docs = [r["document"] for r in results]
        assert "今天的新决策" in docs
        assert "三十天前的旧决策" in docs


class TestDedupInRemember:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_skip_duplicate(self):
        id1 = self.mem.remember("用FastAPI", {"type": "decision"})
        id2 = self.mem.remember("用FastAPI", {"type": "decision"}, dedup_strategy="skip")
        assert id2 is None
        assert self.mem.get_memory(id1) is not None

    def test_overwrite_duplicate(self):
        id1 = self.mem.remember("用FastAPI", {"type": "decision"})
        id2 = self.mem.remember("用FastAPI框架", {"type": "decision"}, dedup_strategy="overwrite")
        assert id2 is not None
        assert id2 != id1
        assert self.mem.get_memory(id1) is None

    def test_no_dedup_when_strategy_none(self):
        id1 = self.mem.remember("用FastAPI", {"type": "decision"})
        id2 = self.mem.remember("用FastAPI", {"type": "decision"})
        assert id2 is not None
        assert id2 != id1

    def test_different_content_no_dedup(self):
        id1 = self.mem.remember("使用FastAPI作为Web框架", {"type": "decision"})
        id2 = self.mem.remember("使用PostgreSQL作为数据库", {"type": "decision"}, dedup_strategy="skip")
        assert id2 is not None


class TestArchive:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_archive_and_exclude_from_recall(self):
        mem_id = self.mem.remember("待归档记忆", {"type": "fact"})
        assert self.mem.get_memory(mem_id) is not None

        self.mem.archive_memory(mem_id)
        mem = self.mem.get_memory(mem_id)
        assert mem["metadata"]["active"] is False

        results = self.mem.recall("待归档记忆", top_k=5)
        assert "待归档记忆" not in results

    def test_archive_then_restore(self):
        mem_id = self.mem.remember("归档再恢复", {"type": "fact"})
        self.mem.archive_memory(mem_id)

        results = self.mem.recall("归档再恢复", top_k=5)
        assert "归档再恢复" not in results

        self.mem.restore_memory(mem_id)
        results = self.mem.recall("归档再恢复", top_k=5)
        assert "归档再恢复" in results

    def test_include_archived_param(self):
        mem_id = self.mem.remember("归档可见", {"type": "fact"})
        self.mem.archive_memory(mem_id)

        results = self.mem.recall("归档可见", top_k=5, include_archived=True)
        assert "归档可见" in results

    def test_archive_not_found_raises(self):
        from memos.errors import ChromaDBError

        with pytest.raises(ChromaDBError):
            self.mem.archive_memory("bad_id")

    def test_restore_not_found_raises(self):
        from memos.errors import ChromaDBError

        with pytest.raises(ChromaDBError):
            self.mem.restore_memory("bad_id")

    def test_list_excludes_archived(self):
        mem_id = self.mem.remember("要归档的列表项", {"type": "todo", "project_id": "proj_x"})
        self.mem.remember("正常列表项", {"type": "todo", "project_id": "proj_x"})
        self.mem.archive_memory(mem_id)

        items = self.mem.list_memories(project_id="proj_x")
        docs = [i["document"] for i in items]
        assert "要归档的列表项" not in docs
        assert "正常列表项" in docs

    def test_list_include_archived(self):
        mem_id = self.mem.remember("归档可见列表项", {"type": "todo", "project_id": "proj_x"})
        self.mem.archive_memory(mem_id)

        items = self.mem.list_memories(project_id="proj_x", include_archived=True)
        docs = [i["document"] for i in items]
        assert "归档可见列表项" in docs


class TestArchiveOld:
    def setup_method(self):
        self.mem = ContextMemory(COLLECTION)
        clean_collection(self.mem)

    def test_archive_old_memories(self):
        self.mem.remember("近期记忆", {"type": "fact"})
        old_ts = time.time() - 100 * 86400
        with mock.patch("memos.engine.memory.time.time", return_value=old_ts):
            self.mem.remember("百天前的旧记忆", {"type": "fact"})

        count = self.mem.archive_old_memories(days=90)
        assert count == 1

        results = self.mem.recall("记忆", top_k=5)
        assert "百天前的旧记忆" not in results
        assert "近期记忆" in results

    def test_archive_old_no_candidates(self):
        self.mem.remember("新记忆", {"type": "fact"})
        count = self.mem.archive_old_memories(days=90)
        assert count == 0
