"""Phase 1 烟雾测试 — 清理层 (F3+F1+F5+F13) ~3min"""

import json
import os

from tests.v070_integration.conftest import get_injected_file, PROJECT_ROOT


class TestPhase1Smoke:
    """验证 Phase 1 (F3+F1+F5+F13) 基本可用"""

    def test_f3_no_old_tools(self, unified_client):
        """[Phase1-F3] force_extract 和 log_complete_turn 不可调用"""
        mem = unified_client.app.state.context_memory
        assert not hasattr(mem, "force_extract")
        assert not hasattr(mem, "log_complete_turn")

    def test_f1_injected_records_file(self, unified_client):
        """[Phase1-F1] Hook 端点可访问，ChromaDB 写入 user_input"""
        resp = unified_client.post(
            "/api/hooks/prompt",
            json={"conversation_id": "smoke-f1", "user_input": "烟雾测试: 使用Python开发后端"},
        )
        assert resp.status_code == 200

        # 验证 ChromaDB 写入
        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={"conversation_id": "smoke-f1"},
            include=["documents", "metadatas"],
        )
        assert len(results["ids"]) >= 1, "Hook 应写入 user_input 到 ChromaDB"

    def test_f5_status_tri_state(self, unified_client):
        """[Phase1-F5] 新记忆使用 status=active"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("烟雾测试记忆", metadata={"type": "solution", "source": "manual"})
        assert mid is not None, "remember 返回 None"
        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert meta.get("status") == "active", f"预期 status=active, 实际={meta.get('status')}"
        assert "active" not in meta, "不应包含旧 active 字段"

    def test_f13_behavior_guide(self, unified_client):
        """[Phase1-F13] behavior_guide.json 可读取"""
        resp = unified_client.post(
            "/api/hooks/prompt",
            json={"user_input": "测试 behavior_guide 读取"},
        )
        assert resp.status_code == 200
