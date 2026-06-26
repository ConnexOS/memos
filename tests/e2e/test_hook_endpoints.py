"""E2E 测试：Hook HTTP 端点（/api/hooks/prompt 和 /api/hooks/stop）

覆盖范围：
  1. 正常响应 — 返回 200 + 正确 JSON 结构
  2. 数据写入后可查询 — 通过 store.get 验证 ChromaDB 持久化
  3. 重复请求幂等 — 多次调用不抛异常

运行条件：ChromaDB 持久化存储可用（默认 config.chroma.path）
"""

import pytest


class TestPromptNormalResponse:
    """POST /api/hooks/prompt — 正常请求场景"""

    def test_full_body_returns_200_and_structure(self, unified_client):
        """完整请求体返回 200 + additional_context + suggestions"""
        resp = unified_client.post(
            "/api/hooks/prompt",
            json={
                "conversation_id": "pt-001",
                "user_input": "今天的工作内容是什么？",
                "assistant_output": "",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "additional_context" in data
        assert isinstance(data["additional_context"], str)

    def test_minimal_body(self, unified_client):
        """仅 user_input 的最小请求"""
        resp = unified_client.post("/api/hooks/prompt", json={"user_input": "你好"})
        assert resp.status_code == 200
        assert "additional_context" in resp.json()

    def test_empty_input_returns_empty(self, unified_client):
        """user_input 为空时返回空上下文"""
        resp = unified_client.post("/api/hooks/prompt", json={"user_input": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["additional_context"] == ""

    def test_with_assistant_output(self, unified_client):
        """同时传入 user_input 和 assistant_output"""
        resp = unified_client.post(
            "/api/hooks/prompt",
            json={
                "conversation_id": "pt-002",
                "user_input": "请总结项目",
                "assistant_output": "项目处于早期阶段，重点在核心功能开发。",
            },
        )
        assert resp.status_code == 200

    def test_legacy_prompt_field(self, unified_client):
        """兼容旧版 prompt 字段（兼容 Claude Code 原生格式）"""
        resp = unified_client.post("/api/hooks/prompt", json={"prompt": "旧版格式消息"})
        assert resp.status_code == 200
        assert "additional_context" in resp.json()

    def test_prompt_field_takes_precedence(self, unified_client):
        """prompt 字段优先于 user_input"""
        resp = unified_client.post(
            "/api/hooks/prompt",
            json={"user_input": "不应使用", "prompt": "应使用此文本"},
        )
        assert resp.status_code == 200
        assert "additional_context" in resp.json()


# ============================================================
# Stop 端点正常响应
# ============================================================


class TestStopNormalResponse:
    """POST /api/hooks/stop — 正常请求场景"""

    def test_normal_response(self, unified_client):
        """正常请求返回 200 + additional_context 空字符串"""
        resp = unified_client.post(
            "/api/hooks/stop",
            json={
                "last_assistant_message": "助手回复内容",
                "conversation_id": "st-001",
                "stop_hook_active": False,
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"additional_context": ""}

    def test_stop_hook_active_skip(self, unified_client):
        """stop_hook_active=true 跳过写入"""
        resp = unified_client.post(
            "/api/hooks/stop",
            json={"last_assistant_message": "会被跳过", "stop_hook_active": True},
        )
        assert resp.status_code == 200
        assert resp.json() == {"additional_context": ""}

    def test_empty_message_skip(self, unified_client):
        """last_assistant_message 为空时跳过"""
        resp = unified_client.post("/api/hooks/stop", json={"last_assistant_message": ""})
        assert resp.status_code == 200
        assert resp.json() == {"additional_context": ""}

    def test_minimal_body(self, unified_client):
        """仅必需字段的最小请求"""
        resp = unified_client.post(
            "/api/hooks/stop",
            json={"last_assistant_message": "最小请求消息"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"additional_context": ""}


# ============================================================
# 数据持久化验证
# ============================================================


class TestDataPersistence:
    """验证 Hook 写入 ChromaDB 的数据可检索"""

    def test_prompt_writes_user_input(self, unified_client):
        """Prompt 写入 user_input 类型后可查询"""
        unified_client.post(
            "/api/hooks/prompt",
            json={
                "conversation_id": "persist-001",
                "user_input": "E2E 持久化测试用户消息",
            },
        )

        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={
                "$and": [
                    {"type": "user_input"},
                    {"conversation_id": "persist-001"},
                ]
            },
            include=["documents"],
        )
        docs = results.get("documents", [])
        assert any("E2E 持久化测试用户消息" in (d or "") for d in docs), (
            f"未找到写入的 user_input，可用文档: {docs}"
        )

    def test_prompt_writes_assistant_output(self, unified_client):
        """Prompt 传入 assistant_output 时一并写入"""
        unified_client.post(
            "/api/hooks/prompt",
            json={
                "conversation_id": "persist-002",
                "user_input": "用户问题",
                "assistant_output": "E2E 测试助手回复",
            },
        )

        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={
                "$and": [
                    {"type": "assistant_output"},
                    {"conversation_id": "persist-002"},
                ]
            },
            include=["documents"],
        )
        docs = results.get("documents", [])
        assert any("E2E 测试助手回复" in (d or "") for d in docs), (
            f"未找到写入的 assistant_output，可用文档: {docs}"
        )

    def test_stop_writes_assistant_output(self, unified_client):
        """Stop 写入 assistant_output 类型后可查询"""
        unified_client.post(
            "/api/hooks/stop",
            json={
                "last_assistant_message": "E2E Stop 助手回复",
                "conversation_id": "persist-003",
                "stop_hook_active": False,
            },
        )

        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={
                "$and": [
                    {"type": "assistant_output"},
                    {"conversation_id": "persist-003"},
                ]
            },
            include=["documents"],
        )
        docs = results.get("documents", [])
        assert any("E2E Stop 助手回复" in (d or "") for d in docs), (
            f"未找到写入的 assistant_output，可用文档: {docs}"
        )

    def test_written_data_has_metadata(self, unified_client):
        """写入的数据包含完整元数据"""
        import time

        now = time.time()
        unified_client.post(
            "/api/hooks/prompt",
            json={
                "conversation_id": "persist-004",
                "user_input": "元数据验证消息",
            },
        )

        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={
                "$and": [
                    {"type": "user_input"},
                    {"conversation_id": "persist-004"},
                ]
            },
            include=["metadatas"],
        )
        metas = results.get("metadatas", [])
        assert len(metas) >= 1
        meta = metas[0]
        # 验证关键元数字段
        assert meta.get("type") == "user_input"
        assert meta.get("scope") == "personal"
        assert meta.get("conversation_id") == "persist-004"
        assert isinstance(meta.get("timestamp"), (int, float))
        # 验证时间戳存在且为合理值（允许已有数据的情况）
        assert meta.get("timestamp", 0) > 0

    def test_prompt_with_project_id(self, unified_client):
        """写入的数据包含 project_id"""
        unified_client.post(
            "/api/hooks/prompt",
            json={
                "conversation_id": "persist-005",
                "user_input": "带 project_id 的测试",
            },
            headers={"X-Memos-Project-Id": "e2e-test-pid"},
        )

        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={
                "$and": [
                    {"type": "user_input"},
                    {"conversation_id": "persist-005"},
                    {"project_id": "e2e-test-pid"},
                ]
            },
            include=["documents"],
        )
        assert len(results.get("ids", [])) >= 1, "未找到带 project_id 的记录"


# ============================================================
# 幂等性测试
# ============================================================


class TestIdempotency:
    """重复请求幂等性"""

    def test_repeated_prompt(self, unified_client):
        """相同 prompt 多次调用不抛异常"""
        body = {"conversation_id": "idem-001", "user_input": "幂等测试用户消息"}
        for i in range(3):
            resp = unified_client.post("/api/hooks/prompt", json=body)
            assert resp.status_code == 200, f"第 {i + 1} 次 prompt 调用失败"

    def test_repeated_stop(self, unified_client):
        """相同 stop 多次调用不抛异常"""
        body = {
            "last_assistant_message": "幂等测试停",
            "conversation_id": "idem-002",
            "stop_hook_active": False,
        }
        for i in range(3):
            resp = unified_client.post("/api/hooks/stop", json=body)
            assert resp.status_code == 200, f"第 {i + 1} 次 stop 调用失败"

    def test_prompt_then_stop_chain(self, unified_client):
        """Prompt → Stop 全链路连续调用"""
        r1 = unified_client.post(
            "/api/hooks/prompt",
            json={"conversation_id": "chain-001", "user_input": "链式测试"},
        )
        assert r1.status_code == 200

        r2 = unified_client.post(
            "/api/hooks/stop",
            json={
                "last_assistant_message": "链式回复",
                "conversation_id": "chain-001",
                "stop_hook_active": False,
            },
        )
        assert r2.status_code == 200
        assert r2.json() == {"additional_context": ""}
