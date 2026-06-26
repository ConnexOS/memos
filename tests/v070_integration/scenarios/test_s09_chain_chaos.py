"""S09：全链路混沌 (跨阶段)

拆分为 3 个独立方法，通过 cls 传递中间状态。
修复说明 (P0-1)：无残留旧代码。
注意：HTTP Handler 不执行 _check_ai_reference，引用检测需通过原生 hook 模块验证。
"""

import json
import time

from memos.config import get_memos_home
from tests.v070_integration.conftest import read_latest_activity_log


class TestS09ChainChaos:
    """全链路混沌场景 — 3 个独立方法"""

    @classmethod
    def setup_class(cls):
        cls._mem = None

    def test_01_hook_inject_and_stop(self, unified_client):
        """[S09-01] Hook 注入 → Stop → 基本验证端点正常"""
        mem = unified_client.app.state.context_memory
        TestS09ChainChaos._mem = mem

        # Prompt Hook 注入
        resp = unified_client.post(
            "/api/hooks/prompt",
            json={
                "conversation_id": "chaos-001",
                "user_input": "我们团队决定使用Elasticsearch做全文搜索",
            },
        )
        assert resp.status_code == 200

        # Stop Hook
        resp = unified_client.post(
            "/api/hooks/stop",
            json={
                "conversation_id": "chaos-001",
                "last_assistant_message": "好的，使用Elasticsearch作为全文搜索引擎是合理的选择。",
            },
        )
        assert resp.status_code == 200

        # 验证 ChromaDB 中已写入
        results = mem.store.get(
            where={"conversation_id": "chaos-001"},
            include=["metadatas"],
        )
        assert len(results["ids"]) >= 1

    def test_02_feedback_and_forget(self, unified_client):
        """[S09-02] 反馈确认 → 遗忘 → recall 过滤"""
        mem = TestS09ChainChaos._mem or unified_client.app.state.context_memory
        TestS09ChainChaos._mem = mem

        # 写入一些测试数据
        target_id = mem.remember("Elasticsearch搜索配置", metadata={"type": "solution"})
        unrelated_id = mem.remember("无关数据: 天气讨论", metadata={"type": "decision"})
        assert target_id and unrelated_id

        # 给 target 加反馈
        mem.update_memory(target_id, new_metadata={"useful_feedback_count": 1})
        # 遗忘无关数据
        mem.forget_memory(unrelated_id)

        # recall 不应包含被遗忘的
        results = mem.recall(query="无关", top_k=5, return_scores=True)
        result_ids = [r["id"] for r in results]
        assert unrelated_id not in result_ids, \
            "被遗忘的记忆不应出现在 recall 结果中"

    def test_03_feedback_boost_formula(self, unified_client):
        """[S09-03] 验证反馈反哺公式可用"""
        mem = TestS09ChainChaos._mem or unified_client.app.state.context_memory
        boost = mem._compute_reuse_boost({"reuse_count": 2, "useful_feedback_count": 1})
        assert boost > 0, f"反馈加分应为正: {boost}"
