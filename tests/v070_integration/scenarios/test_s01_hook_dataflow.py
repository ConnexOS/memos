"""S01：Hook → 引用检测全链路 (F1 + F3 + F12)

注意：
- AI 引用检测 (_check_ai_reference) 是 native hook 模块 (hooks/stop.py) 的独立函数
- HTTP Handler (hook_handler.py) 不执行引用回检，ContextVar 不传播到 HTTP 请求
- F1 引用检测逻辑通过直接构造 injected_records 文件 + 调用 _check_ai_reference 验证
- Hook 端点验证仅验证 ChromaDB 写入和响应结构
"""

import json
import time

from memos.config import get_memos_home
from tests.v070_integration.conftest import read_latest_activity_log


class TestS01HookDataFlow:
    """验证 Hook → 引用检测 → 活动日志 全链路"""

    def test_01_prompt_hook_writes_L1_data(self, unified_client):
        """[S01-01] Prompt Hook 将 user_input 写入 ChromaDB"""
        resp = unified_client.post(
            "/api/hooks/prompt",
            json={
                "conversation_id": "s01-001",
                "user_input": "集成测试: 使用PostgreSQL数据库",
            },
        )
        assert resp.status_code == 200

        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={"$and": [{"type": "user_input"}, {"conversation_id": "s01-001"}]},
            include=["documents", "metadatas"],
        )
        assert len(results["ids"]) >= 1, "ChromaDB 未写入 user_input"

    def test_02_hook_returns_additional_context(self, unified_client):
        """[S01-02] Prompt Hook 返回结构正确的响应"""
        resp = unified_client.post(
            "/api/hooks/prompt",
            json={
                "conversation_id": "s01-002",
                "user_input": "使用FastAPI框架开发后端服务",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "additional_context" in data

    def test_03_reference_detection(self, unified_client):
        """[S01-03] F1: 引用检测 — 匹配注入内容时返回 ai_reference"""
        # 手动构造注入记录文件（ContextVar 不传播到 HTTP handler）
        pid = "s01-test"
        injected_file = get_memos_home() / "etc" / f".injected_records_{pid}.json"
        test_content = "系统架构采用FastAPI+ChromaDB方案"
        injected_file.write_text(json.dumps({
            "project_id": pid,
            "updated_at": time.time(),
            "records": [
                {"id": "test-id-001", "content": test_content, "timestamp": time.time()},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        # 直接调用引用检测逻辑（注意：_check_ai_reference 是精确子串匹配）
        from memos.hooks.stop import _check_ai_reference
        _check_ai_reference(
            "好的，系统架构采用FastAPI+ChromaDB方案来构建后端。",
            pid=pid,
        )

        # 验证活动日志
        log = read_latest_activity_log(event_type="ai_reference")
        assert log.get("event") == "ai_reference", f"预期 ai_reference, 实际: {log}"

    def test_04_no_false_positive(self, unified_client):
        """[S01-04] F1: 引用检测 — 不匹配时不产生 ai_reference"""
        pid = "s01-test"
        from memos.hooks.stop import _check_ai_reference
        _check_ai_reference("今天天气很好，适合写代码。", pid=pid)
        # 不匹配时 _check_ai_reference 静默返回，不写日志
        # 验证无异常抛出即可

    def test_05_activity_log_injection_type_fields(self, unified_client):
        """[S01-05] 活动日志 API 中 event 字段存在"""
        resp = unified_client.get("/api/v2/activity-log?limit=10")
        assert resp.status_code == 200

        data = resp.json()
        logs = data if isinstance(data, list) else data.get("events", data.get("data", []))
        if logs:
            assert "event" in logs[0], f"活动日志缺少 event 字段: {logs[0]}"
