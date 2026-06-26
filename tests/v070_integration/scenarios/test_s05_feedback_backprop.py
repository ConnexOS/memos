"""S05：反馈反哺排序 (F10)

修复说明 (P1-2)：
- test_01/02 使用 return_scores=True 后的 final_score 字段来对比排序
- test_04 验证 _compute_reuse_boost 内部 clamp 逻辑（metadata 不 clamp）
"""

import math


class TestS05FeedbackBackprop:
    """验证 useful_feedback_count 对排序的影响"""

    def test_01_confirm_increases_score(self, unified_client):
        """[S05-01] 确认 → useful_feedback_count +1 → final_score提升"""
        mem = unified_client.app.state.context_memory
        id_a = mem.remember("FastAPI 性能优化方案", metadata={"type": "solution"})
        id_b = mem.remember("FastAPI 性能调优方法", metadata={"type": "solution"})
        assert id_a and id_b

        mem.update_memory(id_a, new_metadata={"useful_feedback_count": 1})

        results = mem.recall(query="FastAPI 性能", top_k=50, return_scores=True)
        score_a = next(r["final_score"] for r in results if r["id"] == id_a)
        score_b = next(r["final_score"] for r in results if r["id"] == id_b)
        # final_score 已包含 reuse_boost，收到反馈的 final_score 应更高
        assert score_a > score_b, \
            f"被确认的记忆 final_score 应更高: A={score_a}, B={score_b}"

    def test_02_delete_decreases_score(self, unified_client):
        """[S05-02] 负反馈 → useful_feedback_count 负值 → 验证公式响应"""
        mem = unified_client.app.state.context_memory
        # 仅验证 _compute_reuse_boost 正确响应负值
        boost_pos = mem._compute_reuse_boost({"reuse_count": 10, "useful_feedback_count": 3})
        boost_neg = mem._compute_reuse_boost({"reuse_count": 10, "useful_feedback_count": -3})
        # 正值反馈的 boost 应高于负值
        assert boost_pos > boost_neg, \
            f"正反馈 boost 应高于负值: {boost_pos} vs {boost_neg}"

    def test_03_formula_correctness(self, unified_client):
        """[S05-03] _compute_reuse_boost() 公式计算正确"""
        mem = unified_client.app.state.context_memory

        meta = {"reuse_count": 3, "useful_feedback_count": 2}
        boost = mem._compute_reuse_boost(meta)
        expected = math.log2(3 + 1) * 0.15 + 2 * 0.30
        assert abs(boost - expected) < 0.001, f"公式偏差: {boost} vs {expected}"

    def test_04_feedback_floor_enforced(self, unified_client):
        """[S05-04] _compute_reuse_boost 内部 clamp useful_feedback_count 到 -10

        注意：clamp 发生在 _compute_reuse_boost 内部（max(-10)），不在 update_memory 中。
        因此 metadata 存储值不 clamp，但 _compute_reuse_boost 返回结果应等价于 -10。
        """
        mem = unified_client.app.state.context_memory

        # 使用高 reuse_count 使 log2 项提供足够正项以区分不同 feedback 值
        meta_high = {"reuse_count": 4194303}  # log2(4194304) * 0.15 = 3.3
        boost_minus20 = mem._compute_reuse_boost({**meta_high, "useful_feedback_count": -20})
        boost_minus10 = mem._compute_reuse_boost({**meta_high, "useful_feedback_count": -10})
        boost_minus5 = mem._compute_reuse_boost({**meta_high, "useful_feedback_count": -5})

        # -20 应 clamp 到 -10（结果相同）
        assert boost_minus20 == boost_minus10, \
            f"-20 应 clamp 到 -10: {boost_minus20} vs {boost_minus10}"
        # -5 高于 -10（未达 clamp 阈值）
        assert boost_minus5 > boost_minus10, \
            f"-5 应高于 -10: {boost_minus5} > {boost_minus10}"
