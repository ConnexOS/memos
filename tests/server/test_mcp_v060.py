"""Phase 5: MCP 工具集成测试（F3 — 核心路径 + 边界参数）。"""

import json


class TestPhase5MCP:
    """MCP 工具核心路径 + 边界参数测试"""

    def _set_memory(self, unified_client):
        from memos.server.mcp import set_memory
        set_memory(unified_client.app.state.context_memory)

    def test_save_knowledge_then_recall(self, unified_client):
        """F1: save_knowledge 写入后可 recall"""
        self._set_memory(unified_client)
        from memos.server.mcp import save_knowledge, recall
        import uuid

        uid = uuid.uuid4().hex[:8]
        unique_text = f"XKCD-{uid}-treat-引理在范畴论中表述为任意态射的因式分解唯一性"
        result = save_knowledge(unique_text, type="solution")
        # 可能直接保存、覆盖旧知识、或已存在相同知识（去重命中）
        # 三种情况均说明数据已入库，可被 recall 检索
        assert any(kw in result for kw in ("已直接保存", "已覆盖", "已存在相同知识")), f"意外的返回值: {result}"

        recall_result = recall(query=f"XKCD-{uid}", top_k=5, type_filter="solution")
        assert "未找到" not in recall_result

    def test_list_memories_type_filter(self, unified_client):
        """F2: list_memories 类型过滤正确"""
        from memos.server.mcp import list_memories

        result = list_memories(type_filter="solution", limit=5)
        if result != "暂无记忆。":
            assert all("[solution]" in line for line in result.split("\n") if line.strip())

    def test_todo_full_lifecycle(self, unified_client):
        """F3: todo 全生命周期"""
        self._set_memory(unified_client)
        from memos.server.mcp import create_todo, list_todos, update_todo

        created = json.loads(create_todo("测试待办项", priority="high"))
        assert "id" in created
        tid = created["id"]

        listed = json.loads(list_todos(todo_status="pending"))
        assert listed["total"] >= 1
        ids = [t["id"] for t in listed["todos"]]
        assert tid in ids

        result = update_todo(tid, "in_progress")
        assert "变更为" in result
        result2 = update_todo(tid, "completed")
        assert "变更为" in result2

    def test_save_knowledge_invalid_type_briefing(self, unified_client):
        """M1: save_knowledge(type='briefing') 返回参数错误"""
        self._set_memory(unified_client)
        from memos.server.mcp import save_knowledge

        result = save_knowledge("测试简报", type="briefing")
        assert "无效类型" in result
        assert "solution" in result
        assert "decision" in result

    def test_recall_invalid_type_watchlist(self, unified_client):
        """M2: recall(type_filter='watchlist') 返回参数错误"""
        self._set_memory(unified_client)
        from memos.server.mcp import recall

        result = recall(query="测试", type_filter="watchlist")
        assert "无效" in result

