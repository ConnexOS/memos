import time
import json
import threading
from unittest import mock

import pytest
from memos.engine.memory import ContextMemory, SIMILARITY_THRESHOLD
from memos.engine.extractor import MemoryExtractor, _estimate_tokens
from memos.server.mcp import (
    remember as mcp_remember,
    recall as mcp_recall,
    list_memories as mcp_list_memories,
    set_project_id as mcp_set_project_id,
    _detect_project_id as mcp_detect_project_id,
    _get_project_id as mcp_get_project_id,
    _get_memory as mcp_memory,
    _reset_for_test as mcp_reset_for_test,
)

from tests.conftest import clean_collection, mock_llm

_FAKE_LLM_RESPONSE = json.dumps(
    [
        {"content": "团队决定使用FastAPI框架", "type": "decision"},
        {"content": "数据库选用PostgreSQL", "type": "decision"},
        {"content": "每天早上10点开站会", "type": "fact"},
    ]
)

COLLECTION_A = "intg_a_crud"
COLLECTION_B = "intg_b_buffer"
COLLECTION_C = "intg_c_mcp"
COLLECTION_D = "intg_d_isolation"
COLLECTION_E = "intg_e_hybrid"
COLLECTION_F = "intg_f_session"
COLLECTION_G = "intg_g_exceptions"
COLLECTION_H = "intg_h_injection"


class TestGroupA_CRUD:
    COLLECTION = COLLECTION_A

    def setup_method(self):
        self.mem = ContextMemory(self.COLLECTION)
        clean_collection(self.mem)

    def test_a1_store_and_get(self):
        mem_id = self.mem.remember("用FastAPI框架")
        mem = self.mem.get_memory(mem_id)
        assert mem["id"] == mem_id
        assert mem["document"] == "用FastAPI框架"
        assert isinstance(mem["metadata"], dict)

    def test_a2_update_content(self):
        mem_id = self.mem.remember("用FastAPI框架")
        self.mem.update_memory(mem_id, "改用Django")
        mem = self.mem.get_memory(mem_id)
        assert mem["document"] == "改用Django"

    def test_a3_update_preserves_metadata(self):
        mem_id = self.mem.remember("用FastAPI", {"type": "decision", "project_id": "projX"})
        self.mem.update_memory(mem_id, "改用Django")
        mem = self.mem.get_memory(mem_id)
        assert mem["metadata"]["type"] == "decision"
        assert mem["metadata"]["project_id"] == "projX"

    def test_a4_delete_makes_unreachable(self):
        mem_id = self.mem.remember("待删除")
        self.mem.delete_memory(mem_id)
        assert self.mem.get_memory(mem_id) is None

    def test_a5_recall_excludes_deleted(self):
        mem_id = self.mem.remember("唯一条目")
        self.mem.delete_memory(mem_id)
        results = self.mem.recall("唯一条目", top_k=5)
        assert "唯一条目" not in results

    def test_a6_get_nonexistent(self):
        assert self.mem.get_memory("bad_id") is None

    def test_a7_update_nonexistent_raises(self):
        from memos.errors import ChromaDBError

        with pytest.raises(ChromaDBError):
            self.mem.update_memory("bad_id", "新内容")

    def test_a8_delete_nonexistent_raises(self):
        from memos.errors import ChromaDBError

        with pytest.raises(ChromaDBError):
            self.mem.delete_memory("bad_id")

    def test_a9_list_pagination(self):
        for i in range(5):
            self.mem.remember(f"记忆第{i + 1}条")
        page1 = self.mem.list_memories(limit=2, offset=0)
        assert len(page1) == 2
        page2 = self.mem.list_memories(limit=2, offset=2)
        assert len(page2) == 2
        assert page1[0]["document"] != page2[0]["document"]

    def test_a10_list_type_filter(self):
        self.mem.remember("决策A", {"type": "decision"})
        self.mem.remember("事实B", {"type": "fact"})
        items = self.mem.list_memories(type_filter="decision")
        docs = [i["document"] for i in items]
        assert "决策A" in docs
        assert "事实B" not in docs

    def test_a11_archive_excludes_from_recall(self):
        mem_id = self.mem.remember("待归档记忆")
        self.mem.archive_memory(mem_id)
        results = self.mem.recall("待归档记忆", top_k=5)
        assert "待归档记忆" not in results

    def test_a12_restore_after_archive(self):
        mem_id = self.mem.remember("归档再恢复")
        self.mem.archive_memory(mem_id)
        self.mem.restore_memory(mem_id)
        results = self.mem.recall("归档再恢复", top_k=5)
        assert "归档再恢复" in results


class TestGroupB_BufferExtract:
    COLLECTION = COLLECTION_B

    def setup_method(self):
        mem = ContextMemory(self.COLLECTION)
        clean_collection(mem)
        self.mem = mem
        self.ext = MemoryExtractor(memory_system=mem)

    def test_b1_append_accumulates(self):
        for i in range(4):
            self.ext.append_conversation("user", f"消息{i + 1}")
        assert len(self.ext.conversation_buffer) == 4

    def test_b2_auto_extract_trigger(self, monkeypatch):
        mock_llm(monkeypatch)
        self.ext._last_extract_time = 0
        for i in range(6):
            self.ext.append_conversation("user", f"消息{i + 1}")
        # 5条触发提炼，第6条留在缓冲区
        assert len(self.ext.conversation_buffer) == 1

    def test_b3_force_extract(self, monkeypatch):
        mock_llm(monkeypatch)
        self.ext.append_conversation("user", "消息A")
        self.ext.append_conversation("user", "消息B")
        self.ext.append_conversation("user", "消息C")
        count = self.ext.force_extract()
        assert count > 0
        assert len(self.ext.conversation_buffer) == 0

    def test_b4_force_extract_empty(self):
        self.ext.conversation_buffer.clear()
        count = self.ext.force_extract()
        assert count == 0

    def test_b5_rate_limit_blocks_auto(self):
        self.ext._last_extract_time = time.time()
        for i in range(6):
            self.ext.append_conversation("user", f"消息{i + 1}")
        assert len(self.ext.conversation_buffer) > 0

    def test_b6_extracted_memories_in_chromadb(self, monkeypatch):
        mock_llm(monkeypatch)
        self.ext._async_mode = False  # 同步模式确保结果立即可见
        self.ext._last_extract_time = 0
        for i in range(6):
            self.ext.append_conversation("user", f"技术讨论{i + 1}")
        results = self.mem.recall("FastAPI", top_k=5)
        assert any("FastAPI" in r for r in results)

    def test_b7_exact_dedup(self):
        self.ext.store_memories([{"content": "用FastAPI", "type": "decision"}])
        count = self.ext.store_memories([{"content": "用FastAPI", "type": "decision"}])
        assert count == 0

    def test_b8_semantic_dedup(self):
        self.ext.store_memories([{"content": "用FastAPI框架", "type": "decision"}])
        count = self.ext.store_memories([{"content": "用FastAPI作为后端", "type": "decision"}])
        assert count == 0

    def test_b9_buffer_truncation(self):
        long_content = "x" * 2500
        self.ext.append_conversation("user", long_content)
        self.ext.append_conversation("user", "保留的关键内容")
        merged = "\n".join(self.ext.conversation_buffer)
        assert "保留的关键内容" in merged


class TestGroupC_MCP:
    COLLECTION = COLLECTION_C

    def setup_method(self):
        mcp_reset_for_test(self.COLLECTION)
        # 清空测试集合确保隔离
        mem = mcp_memory()
        all_ids = mem.store.get()["ids"]
        if all_ids:
            mem.store.delete(ids=all_ids)
        mcp_set_project_id("test_mcp")

    def test_c1_mcp_remember(self):
        result = mcp_remember("MCP测试记忆")
        assert "已追加" in result or "已触发自动提炼" in result

    def test_c2_mcp_recall(self):
        mcp_memory().remember("Python使用FastAPI框架", metadata={"project_id": "test_mcp"})
        result = mcp_recall("FastAPI", project_id_override="test_mcp")
        assert "FastAPI" in result
        assert "未找到" not in result

    def test_c3_mcp_recall_hybrid(self):
        mcp_memory().remember("使用PostgreSQL数据库", metadata={"project_id": "test_mcp"})
        result = mcp_recall("PostgreSQL", project_id_override="test_mcp", hybrid=True)
        assert "PostgreSQL" in result
        assert "未找到" not in result

    def test_c4_mcp_recall_empty(self):
        result = mcp_recall("不存在的记忆XXX", project_id_override="test_mcp")
        assert "未找到" in result

    def test_c5_mcp_list_memories(self):
        mcp_memory().remember("列表项A", metadata={"project_id": "test_mcp"})
        mcp_memory().remember("列表项B", metadata={"project_id": "test_mcp"})
        result = mcp_list_memories(project_id_override="test_mcp")
        assert "列表项A" in result
        assert "列表项B" in result

    def test_c6_mcp_update_memory(self):
        mid = mcp_memory().remember("旧内容", {"type": "decision", "project_id": "test_mcp"})
        mcp_memory().update_memory(mid, "新内容")
        assert mcp_memory().get_memory(mid)["document"] == "新内容"

    def test_c7_mcp_delete_memory(self):
        mid = mcp_memory().remember("待删除", {"project_id": "test_mcp"})
        mcp_memory().delete_memory(mid)
        assert mcp_memory().get_memory(mid) is None

    def test_c8_mcp_log_complete_turn(self):
        from memos.server.mcp import log_complete_turn as mcp_log_turn

        result = mcp_log_turn("用户消息", "助手回复")
        assert "已记录" in result

    def test_c9_mcp_save_knowledge(self):
        from memos.server.mcp import save_knowledge as mcp_save_knowledge

        result = mcp_save_knowledge("测试知识", type="fact")
        assert "已直接保存" in result

    def test_c10_mcp_set_project_id(self):
        result = mcp_set_project_id("custom_proj")
        assert "已设置" in result
        assert mcp_get_project_id() == "custom_proj"


class TestGroupD_ProjectIsolation:
    COLLECTION = COLLECTION_D

    def setup_method(self):
        self.mem = ContextMemory(self.COLLECTION)
        clean_collection(self.mem)

    def test_d1_projects_isolated(self):
        self.mem.remember("项目A使用FastAPI", {"project_id": "proj_A"})
        self.mem.remember("项目B使用Django", {"project_id": "proj_B"})
        r_a = self.mem.recall("框架", project_id="proj_A")
        r_b = self.mem.recall("框架", project_id="proj_B")
        assert any("FastAPI" in r for r in r_a)
        assert any("Django" in r for r in r_b)
        assert not any("Django" in r for r in r_a)
        assert not any("FastAPI" in r for r in r_b)

    def test_d2_nonexistent_project_returns_empty(self):
        self.mem.remember("测试内容", {"project_id": "proj_A"})
        results = self.mem.recall("测试", project_id="nonexistent")
        assert results == []

    def test_d3_dedup_per_project(self):
        self.mem.remember("使用FastAPI作为Web框架", {"project_id": "proj_A"})
        self.mem.remember("使用PostgreSQL作为数据库", {"project_id": "proj_B"}, dedup_strategy="skip")
        items_a = self.mem.list_memories(project_id="proj_A")
        items_b = self.mem.list_memories(project_id="proj_B")
        assert len(items_a) == 1
        assert len(items_b) == 1

    def test_d4_list_by_project(self):
        for i in range(3):
            self.mem.remember(f"proj_A 第{i + 1}条", {"project_id": "proj_A"})
        for i in range(2):
            self.mem.remember(f"proj_B 第{i + 1}条", {"project_id": "proj_B"})
        items = self.mem.list_memories(project_id="proj_B")
        assert len(items) == 2

    def test_d5_mcp_project_switch(self):
        original_pid = mcp_get_project_id()
        mcp_set_project_id("isolation_p1")
        mcp_memory().remember("p1 的记忆", {"project_id": "isolation_p1"})
        mcp_set_project_id("isolation_p2")
        mcp_memory().remember("p2 的记忆", {"project_id": "isolation_p2"})
        r1 = mcp_recall("记忆", project_id_override="isolation_p1")
        assert "p1 的记忆" in r1
        assert "p2 的记忆" not in r1
        mcp_set_project_id(original_pid)

    def test_d6_detect_project_id(self):
        pid = mcp_detect_project_id()
        assert isinstance(pid, str)
        assert len(pid) == 8
        assert all(c in "0123456789abcdef" for c in pid)


class TestGroupE_HybridDecay:
    COLLECTION = COLLECTION_E

    def setup_method(self):
        self.mem = ContextMemory(self.COLLECTION)
        clean_collection(self.mem)

    def test_e1_hybrid_basic(self):
        self.mem.remember("Python后端使用FastAPI框架")
        self.mem.remember("数据库使用PostgreSQL")
        results = self.mem.recall("FastAPI", top_k=5, hybrid=True)
        assert len(results) > 0

    def test_e2_hybrid_empty_corpus(self):
        results = self.mem.recall("anything", top_k=3, hybrid=True)
        assert results == []

    def test_e3_hybrid_with_decay(self):
        self.mem.remember("旧版FastAPI项目", {"type": "fact"})
        old_ts = time.time() - 30 * 86400
        with mock.patch("memos.engine.memory.time.time", return_value=old_ts):
            self.mem.remember("三十天前的旧项目", {"type": "fact"})
        results = self.mem.recall("项目", top_k=5, hybrid=True, decay_lambda=0.02)
        assert len(results) >= 2
        assert "旧版FastAPI项目" in results
        assert "三十天前的旧项目" in results

    def test_e4_hybrid_with_type_filter(self):
        self.mem.remember("决策:用FastAPI", {"type": "decision"})
        self.mem.remember("事实:FastAPI很快", {"type": "fact"})
        results = self.mem.recall("FastAPI", top_k=5, hybrid=True, where={"type": "decision"})
        assert "决策:用FastAPI" in results
        assert "事实:FastAPI很快" not in results

    def test_e5_hybrid_with_project_filter(self):
        self.mem.remember("项目A的FastAPI", {"project_id": "proj_a"})
        self.mem.remember("项目B的FastAPI", {"project_id": "proj_b"})
        results = self.mem.recall("FastAPI", top_k=5, hybrid=True, project_id="proj_a")
        assert "项目A的FastAPI" in results
        assert "项目B的FastAPI" not in results


class TestGroupF_CrossSession:
    COLLECTION = COLLECTION_F

    def setup_method(self):
        mem = ContextMemory(self.COLLECTION)
        clean_collection(mem)

    def test_f1_data_survives_reinit(self):
        m1 = ContextMemory(self.COLLECTION)
        mem_id = m1.remember("持久化测试内容")
        del m1
        m2 = ContextMemory(self.COLLECTION)
        mem = m2.get_memory(mem_id)
        assert mem is not None
        assert mem["document"] == "持久化测试内容"

    def test_f2_immediately_visible_in_new_session(self):
        m1 = ContextMemory(self.COLLECTION)
        m1.remember("快速可见测试")
        m2 = ContextMemory(self.COLLECTION)
        results = m2.recall("快速可见测试", top_k=5)
        assert any("快速可见测试" in r for r in results)

    def test_f3_collections_isolated(self):
        m_a = ContextMemory("intg_coll_a")
        m_b = ContextMemory("intg_coll_b")
        clean_collection(m_a)
        clean_collection(m_b)
        m_a.remember("collection A 数据")
        m_b.remember("collection B 数据")
        r_a = m_a.recall("数据", top_k=5)
        r_b = m_b.recall("数据", top_k=5)
        assert any("A" in r for r in r_a)
        assert any("B" in r for r in r_b)

    def test_f4_archive_status_survives_reinit(self):
        m1 = ContextMemory(self.COLLECTION)
        mem_id = m1.remember("跨 session 归档")
        m1.archive_memory(mem_id)
        del m1
        m2 = ContextMemory(self.COLLECTION)
        mem = m2.get_memory(mem_id)
        assert mem["metadata"]["active"] is False
        results = m2.recall("跨 session 归档", top_k=5)
        assert "跨 session 归档" not in results
        results_archived = m2.recall("跨 session 归档", top_k=5, include_archived=True)
        assert "跨 session 归档" in results_archived


class TestGroupG_Exceptions:
    COLLECTION = COLLECTION_A

    def setup_method(self):
        self.mem = ContextMemory(self.COLLECTION)
        clean_collection(self.mem)

    def test_g1_llm_service_down(self):
        ext = MemoryExtractor()
        with mock.patch("memos.engine.extractor.requests.post", side_effect=Exception("Connection refused")):
            result = ext.extract("test conversation")
        assert result == []

    def test_g2_llm_non_json(self):
        ext = MemoryExtractor()
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {"content": "我不确定"}
        resp.text = "我不确定"
        with mock.patch("memos.engine.extractor.requests.post", return_value=resp):
            result = ext.extract("test conversation")
        assert result == []

    def test_g3_llm_empty_array(self, monkeypatch):
        mock_llm(monkeypatch, "[]")
        ext = MemoryExtractor(memory_system=self.mem)
        count = ext.extract_and_store("test conversation")
        assert count == 0

    def test_g4_empty_text_remember(self):
        mem_id = self.mem.remember("")
        assert mem_id is not None
        mem = self.mem.get_memory(mem_id)
        assert mem["document"] == ""

    def test_g5_long_text_recall(self):
        self.mem.remember("正常短文本")
        long_query = "x" * 10000
        results = self.mem.recall(long_query, top_k=3)
        assert isinstance(results, list)

    def test_g6_concurrent_append(self):
        ext = MemoryExtractor(memory_system=None, project_id=None)
        errors = []

        def append_thread():
            try:
                for _ in range(50):
                    ext.append_conversation("user", "并发测试")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=append_thread) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0


class TestGroupH_InjectionStats:
    """S5 注入监控 — injection-stats API 集成测试"""

    COLLECTION = COLLECTION_H

    def setup_method(self):
        self.mem = ContextMemory(self.COLLECTION)
        clean_collection(self.mem)

    def _make_suggestion(self, content: str, suggestion_type: str = "active_push", **overrides):
        """辅助：向 ChromaDB 写入一条 type=suggestion 记录。注意：不设置 None 值字段（ChromaDB 不支持）。"""
        meta = {
            "type": "suggestion",
            "project_id": "test_proj",
            "suggestion_type": suggestion_type,
            "status": "pending",
            "similarity": 0.8,
            "timestamp": time.time(),
            "expires_at": time.time() + 86400,
            "source_memory_id": "",
            "source_type": "decision",
            "source_date": "2026-05-26",
            "event_type": "",
            "trigger_keywords": "[]",
            "hit_count": 0,
        }
        # feedback: 仅在测试需要时通过 overrides 传入
        meta.update(overrides)
        return self.mem.remember(content, metadata=meta)

    def _make_manual_suggestion(self, content: str, **overrides):
        """辅助：向 ChromaDB 写入一条 type=manual_suggestion 记录。"""
        meta = {
            "type": "manual_suggestion",
            "project_id": "test_proj",
            "source": "test",
            "trigger_keywords": json.dumps(["测试"]),
            "trigger_mode": "keyword",
            "priority": "medium",
            "cooldown_minutes": 60,
            "validity_minutes": 0,
            "expires_at": 0,
            "disabled": False,
            "hit_count": 3,
            "last_triggered": time.time(),
            "created_by": "user",
            "timestamp": time.time(),
        }
        meta.update(overrides)
        return self.mem.remember(content, metadata=meta)

    def test_h1_structure_completeness(self):
        """验证返回结构包含所有必需字段。"""
        # 使用 mocking 方式测试端点逻辑
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from memos.web.routes.suggestions import router

        app = FastAPI()
        app.state.mem = self.mem
        app.state._pid_override = "test_proj"
        app.include_router(router)

        # mock _detect_project_id
        import memos.web.routes.suggestions as sug_mod
        original_detect = sug_mod._detect_project_id
        sug_mod._detect_project_id = lambda: "test_proj"

        try:
            with TestClient(app) as client:
                resp = client.get("/api/suggestions/injection-stats?days=3&window_hours=24")
                assert resp.status_code == 200
                data = resp.json()

                # 验证顶层结构
                assert "pipelines" in data
                assert "quality" in data
                assert "top_source_memories" in data
                assert "manual_suggestions" in data
                assert "session_injections" in data
                assert "recent_injections" in data
                assert "active_manual_suggestions" in data

                # 验证三管道完整
                for pt in ["active_push", "manual_trigger", "system_alert"]:
                    assert pt in data["pipelines"]
                    for field in ["total", "unique", "pending", "reacted", "dismissed"]:
                        assert field in data["pipelines"][pt]

                # 验证 quality 字段
                assert "useful_rate" in data["quality"]
                assert "trend" in data["quality"]

                # 验证 manual_suggestions
                for field in ["total", "active", "disabled", "total_hits", "top_triggered"]:
                    assert field in data["manual_suggestions"]
        finally:
            sug_mod._detect_project_id = original_detect

    def test_h2_dedup_active_push(self):
        """验证 active_push 按 source_memory_id 去重合并。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from memos.web.routes.suggestions import router

        app = FastAPI()
        app.state.mem = self.mem
        app.state._pid_override = "test_proj"
        app.include_router(router)

        import memos.web.routes.suggestions as sug_mod
        original_detect = sug_mod._detect_project_id
        sug_mod._detect_project_id = lambda: "test_proj"

        try:
            # 写入 3 条同源记忆的 suggestion（同 source_memory_id）
            sim = 0.8
            self._make_suggestion(
                "团队决定使用FastAPI框架",
                source_memory_id="src_001", similarity=0.82,
                timestamp=time.time() - 200,
            )
            self._make_suggestion(
                "团队决定使用FastAPI框架",
                source_memory_id="src_001", similarity=0.85,
                timestamp=time.time() - 100, status="reacted",
                feedback="useful",
            )
            self._make_suggestion(
                "团队决定使用FastAPI框架",
                source_memory_id="src_001", similarity=0.78,
                timestamp=time.time(),
            )

            with TestClient(app) as client:
                resp = client.get("/api/suggestions/injection-stats?window_hours=48")
                data = resp.json()

                # 验证去重：3 条合并为 1 条
                assert len(data["recent_injections"]) == 1
                item = data["recent_injections"][0]
                assert item["inject_count"] == 3
                assert item["best_similarity"] == 0.85  # 取最高
                assert item["feedback"] == "useful"  # 任一 useful → useful
                assert "pending" in item["statuses"]
                assert "reacted" in item["statuses"]

                # 验证 pipelines.unique = 1
                assert data["pipelines"]["active_push"]["unique"] == 1
                assert data["pipelines"]["active_push"]["total"] == 3
        finally:
            sug_mod._detect_project_id = original_detect

    def test_h3_dedup_manual_trigger(self):
        """验证 manual_trigger 按内容 MD5 去重合并。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from memos.web.routes.suggestions import router

        app = FastAPI()
        app.state.mem = self.mem
        app.state._pid_override = "test_proj"
        app.include_router(router)

        import memos.web.routes.suggestions as sug_mod
        original_detect = sug_mod._detect_project_id
        sug_mod._detect_project_id = lambda: "test_proj"

        try:
            self._make_suggestion(
                "当用户提到部署时，提醒检查配置文件",
                suggestion_type="manual_trigger",
                timestamp=time.time() - 100,
            )
            self._make_suggestion(
                "当用户提到部署时，提醒检查配置文件",
                suggestion_type="manual_trigger",
                timestamp=time.time(),
                status="reacted", feedback="useful",
            )

            with TestClient(app) as client:
                resp = client.get("/api/suggestions/injection-stats?window_hours=48")
                data = resp.json()

                # 过滤 manual_trigger 条目
                mt_items = [i for i in data["recent_injections"] if i["suggestion_type"] == "manual_trigger"]
                assert len(mt_items) == 1, f"expected 1 merged, got {len(mt_items)}"
                assert mt_items[0]["inject_count"] == 2
        finally:
            sug_mod._detect_project_id = original_detect

    def test_h4_manual_suggestion_aggregation(self):
        """验证 manual_suggestion 聚合统计。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from memos.web.routes.suggestions import router

        app = FastAPI()
        app.state.mem = self.mem
        app.state._pid_override = "test_proj"
        app.include_router(router)

        import memos.web.routes.suggestions as sug_mod
        original_detect = sug_mod._detect_project_id
        sug_mod._detect_project_id = lambda: "test_proj"

        try:
            self._make_manual_suggestion("建议一", priority="high", hit_count=10)
            self._make_manual_suggestion("建议二", priority="medium", hit_count=5)
            self._make_manual_suggestion("建议三（已禁用）", disabled=True, hit_count=0)

            with TestClient(app) as client:
                resp = client.get("/api/suggestions/injection-stats")
                data = resp.json()

                ms = data["manual_suggestions"]
                assert ms["total"] == 3
                assert ms["active"] == 2
                assert ms["disabled"] == 1
                assert ms["total_hits"] == 15

                # 活跃人工建议列表
                assert len(data["active_manual_suggestions"]) == 2
        finally:
            sug_mod._detect_project_id = original_detect

    def test_h5_empty_data(self):
        """验证空数据边缘情况 — 各字段应为 0 或空列表。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from memos.web.routes.suggestions import router

        app = FastAPI()
        app.state.mem = self.mem
        app.state._pid_override = "test_proj"
        app.include_router(router)

        import memos.web.routes.suggestions as sug_mod
        original_detect = sug_mod._detect_project_id
        sug_mod._detect_project_id = lambda: "test_proj"

        try:
            with TestClient(app) as client:
                resp = client.get("/api/suggestions/injection-stats?days=3")
                data = resp.json()

                for pt in ["active_push", "manual_trigger", "system_alert"]:
                    assert data["pipelines"][pt]["total"] == 0
                    assert data["pipelines"][pt]["unique"] == 0

                assert data["quality"]["useful_rate"] is None
                # trend 始终生成 days 天记录（即使全 0）
                assert len(data["quality"]["trend"]) == 3
                for day in data["quality"]["trend"]:
                    assert day["total"] == 0
                    assert day["useful"] == 0
                assert data["top_source_memories"] == []
                assert data["recent_injections"] == []
                assert data["active_manual_suggestions"] == []
                assert data["manual_suggestions"]["total"] == 0
        finally:
            sug_mod._detect_project_id = original_detect

    def test_h6_top_source_memories(self):
        """验证源记忆排行按 suggestion_count 降序排列。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from memos.web.routes.suggestions import router

        app = FastAPI()
        app.state.mem = self.mem
        app.state._pid_override = "test_proj"
        app.include_router(router)

        import memos.web.routes.suggestions as sug_mod
        original_detect = sug_mod._detect_project_id
        sug_mod._detect_project_id = lambda: "test_proj"

        try:
            # src_001: 4 次
            for i in range(4):
                self._make_suggestion(f"记忆A 变体{i}", source_memory_id="src_001")
            # src_002: 2 次 (1 有用)
            self._make_suggestion("记忆B 变体0", source_memory_id="src_002", feedback="useful")
            self._make_suggestion("记忆B 变体1", source_memory_id="src_002")
            # src_003: 1 次 (无用)
            self._make_suggestion("记忆C", source_memory_id="src_003", feedback="not_useful")

            with TestClient(app) as client:
                resp = client.get("/api/suggestions/injection-stats")
                data = resp.json()
                tops = data["top_source_memories"]

                assert len(tops) == 3
                # 按 suggestion_count 降序
                assert tops[0]["source_memory_id"] == "src_001"
                assert tops[0]["suggestion_count"] == 4
                assert tops[1]["source_memory_id"] == "src_002"
                assert tops[1]["suggestion_count"] == 2
                assert tops[1]["useful_count"] == 1
                assert tops[2]["source_memory_id"] == "src_003"
                assert tops[2]["suggestion_count"] == 1
        finally:
            sug_mod._detect_project_id = original_detect

    def test_h7_injected_records_cache(self):
        """验证 session_injections 从 _get_injected_records 返回 Layer 1 注入记录。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from memos.web.routes.suggestions import router

        app = FastAPI()
        app.state.mem = self.mem
        app.state._pid_override = "test_proj"
        app.include_router(router)

        import memos.web.routes.suggestions as sug_mod
        origin_detect = sug_mod._detect_project_id
        sug_mod._detect_project_id = lambda: "test_proj"

        try:
            # Mock _get_injected_records 返回模拟的 Layer 1 注入记录
            mock_records = [
                {"id": "mem_1", "content": "被注入的记录1", "similarity": 0.85, "final_score": 0.80,
                 "source_type": "decision", "timestamp": time.time()},
                {"id": "mem_2", "content": "被注入的记录2", "similarity": 0.75, "final_score": 0.72,
                 "source_type": "preference", "timestamp": time.time()},
            ]
            original_fn = sug_mod._get_injected_records
            sug_mod._get_injected_records = lambda pid: mock_records

            try:
                with TestClient(app) as client:
                    resp = client.get("/api/suggestions/injection-stats")
                    data = resp.json()
                    items = data["session_injections"]

                    assert len(items) == 2, f"期望 2 条，实际 {len(items)}"
                    assert items[0]["content"] == "被注入的记录1"
                    assert items[1]["content"] == "被注入的记录2"
                    assert items[0]["source_type"] == "decision"
            finally:
                sug_mod._get_injected_records = original_fn
        finally:
            sug_mod._detect_project_id = origin_detect

    def test_h8_injected_records_file_cleanup(self):
        """验证 _save_injected_records 空记录时删除旧文件。"""
        import tempfile
        from pathlib import Path
        from memos.hooks.prompt import _save_injected_records

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pid = "test_cleanup_proj"
            expected_path = tmp / "etc" / f".injected_records_{pid}.json"

            with mock.patch("memos.config.models.get_memos_home", return_value=tmp):
                # 1. 写入非空记录 → 文件应存在
                records = [
                    {"id": "mem_1", "document": "测试注入内容", "similarity": 0.85,
                     "final_score": 0.80, "metadata": {"type": "decision"}},
                ]
                _save_injected_records(pid, records)
                assert expected_path.exists(), "非空记录后文件应存在"
                content = json.loads(expected_path.read_text(encoding="utf-8"))
                assert content["count"] == 1
                assert content["records"][0]["content"] == "测试注入内容"

                # 2. 再次写入空记录 → 文件应被删除
                _save_injected_records(pid, [])
                assert not expected_path.exists(), "空记录后文件应被删除"

                # 3. 空记录时文件已不存在 → 不应报错
                _save_injected_records(pid, [])
