"""测试 F5 — 主动建议 API 6 端点全生命周期 (v0.4.4)

覆盖范围：列表/计数/单条关闭/批量关闭/反馈提交/免打扰状态。
每个测试独立创建 mock TestClient，避免跨测试状态污染。
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_suggestion(
    doc_id="sug-1",
    content="这是一个测试建议",
    status="pending",
    similarity=0.85,
    query="测试查询",
    timestamp=None,
    expires_at=None,
    source_memory_id="mem-1",
):
    """构造模拟 suggestion ChromaDB 记录。"""
    if timestamp is None:
        timestamp = time.time()
    if expires_at is None:
        expires_at = time.time() + 86400 * 7
    return {
        "id": doc_id,
        "document": content,
        "metadata": {
            "type": "suggestion",
            "project_id": "default",
            "source_memory_id": source_memory_id,
            "similarity": similarity,
            "query": query,
            "status": status,
            "suggestion_type": "active_push",
            "source_date": "",
            "source_type": "fact",
            "timestamp": timestamp,
            "expires_at": expires_at,
        },
    }


def _make_mem():
    """创建一个预配置默认值的 mock ContextMemory。"""
    mem = MagicMock()
    mem.store.count.return_value = 0
    mem.store.get.return_value = {"ids": [], "documents": [], "metadatas": []}
    return mem


def _make_client(mem, auth_disabled=True):
    """创建 TestClient，用 patch 确保 lifespan 的 ContextMemory 返回 mock。"""
    from memos.config import config as memos_config

    old_disable = memos_config.auth.disable
    memos_config.auth.disable = auth_disabled
    from memos.web.app import app

    with patch("memos.web.app.ContextMemory", return_value=mem):
        with TestClient(app) as c:
            yield c
    memos_config.auth.disable = old_disable


class TestSuggestionsAPI:
    """主动建议 API 全生命周期测试。"""

    # --- GET /api/suggestions ---

    def test_list_empty(self):
        for c in _make_client(_make_mem()):
            resp = c.get("/api/suggestions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_with_data(self):
        mem = _make_mem()
        sug = _make_suggestion()
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }
        mem.store.count.return_value = 1

        for c in _make_client(mem):
            resp = c.get("/api/suggestions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == "sug-1"
        assert data["items"][0]["content"] == "这是一个测试建议"
        assert data["items"][0]["status"] == "pending"

    def test_list_status_filter(self):
        mem = _make_mem()
        sug = _make_suggestion(status="dismissed")
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }
        mem.store.count.return_value = 1

        for c in _make_client(mem):
            resp = c.get("/api/suggestions?status=dismissed")
        data = resp.json()
        assert data["items"][0]["status"] == "dismissed"

    def test_list_pagination(self):
        mem = _make_mem()
        mem.store.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mem.store.count.return_value = 10

        for c in _make_client(mem):
            resp = c.get("/api/suggestions?limit=2&offset=1")
        data = resp.json()
        assert data["limit"] == 2
        assert data["offset"] == 1
        assert data["total"] == 10

    # --- GET /api/suggestions/count ---

    def test_count_pending(self):
        mem = _make_mem()
        mem.store.count.return_value = 3
        for c in _make_client(mem):
            resp = c.get("/api/suggestions/count")
        assert resp.json()["count"] == 3

    def test_count_zero(self):
        mem = _make_mem()
        mem.store.count.return_value = 0
        for c in _make_client(mem):
            resp = c.get("/api/suggestions/count")
        assert resp.json()["count"] == 0

    # --- POST /api/suggestions/{id}/dismiss ---

    def test_dismiss_suggestion(self):
        mem = _make_mem()
        sug = _make_suggestion()
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }

        for c in _make_client(mem):
            resp = c.post(f"/api/suggestions/{sug['id']}/dismiss")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_dismiss_nonexistent(self):
        mem = _make_mem()
        for c in _make_client(mem):
            resp = c.post("/api/suggestions/nonexistent/dismiss")
        assert resp.status_code == 404

    # --- POST /api/suggestions/dismiss-all ---

    def test_dismiss_all(self):
        mem = _make_mem()
        mem.store.get.return_value = {
            "ids": ["sug-1", "sug-2"],
            "metadatas": [
                {"status": "pending", "type": "suggestion", "project_id": "default"},
                {"status": "pending", "type": "suggestion", "project_id": "default"},
            ],
        }
        for c in _make_client(mem):
            resp = c.post("/api/suggestions/dismiss-all")
        assert resp.status_code == 200
        assert resp.json()["dismissed"] == 2

    def test_dismiss_all_empty(self):
        mem = _make_mem()
        for c in _make_client(mem):
            resp = c.post("/api/suggestions/dismiss-all")
        assert resp.status_code == 200
        assert resp.json()["dismissed"] == 0

    # --- POST /api/suggestions/{id}/feedback ---

    def test_feedback_useful(self):
        mem = _make_mem()
        sug = _make_suggestion()
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }

        for c in _make_client(mem):
            resp = c.post(f"/api/suggestions/{sug['id']}/feedback", json={"feedback": "useful"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_feedback_not_useful(self):
        mem = _make_mem()
        sug = _make_suggestion()
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }

        for c in _make_client(mem):
            resp = c.post(f"/api/suggestions/{sug['id']}/feedback", json={"feedback": "not_useful"})
        assert resp.status_code == 200

    def test_feedback_invalid_value(self):
        for c in _make_client(_make_mem()):
            resp = c.post("/api/suggestions/sug-1/feedback", json={"feedback": "invalid"})
        assert resp.status_code == 422

    def test_feedback_nonexistent(self):
        mem = _make_mem()
        for c in _make_client(mem):
            resp = c.post("/api/suggestions/nonexistent/feedback", json={"feedback": "useful"})
        assert resp.status_code == 404

    def test_feedback_remember_failure_does_not_block(self):
        mem = _make_mem()
        sug = _make_suggestion()
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }
        mem.remember.side_effect = Exception("写入失败")

        for c in _make_client(mem):
            resp = c.post(f"/api/suggestions/{sug['id']}/feedback", json={"feedback": "useful"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    # --- GET /api/suggestions/no-suggestions-status ---

    def test_no_suggestions_not_exists(self):
        for c in _make_client(_make_mem()):
            resp = c.get("/api/suggestions/no-suggestions-status")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_no_suggestions_exists(self, monkeypatch, tmp_path):
        no_sug_path = tmp_path / ".claude" / "no_suggestions"
        no_sug_path.parent.mkdir(parents=True)
        no_sug_path.write_text('{"reason": "测试免打扰"}', encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        for c in _make_client(_make_mem()):
            resp = c.get("/api/suggestions/no-suggestions-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["content"]["reason"] == "测试免打扰"

    def test_no_suggestions_bad_json(self, monkeypatch, tmp_path):
        no_sug_path = tmp_path / ".claude" / "no_suggestions"
        no_sug_path.parent.mkdir(parents=True)
        no_sug_path.write_text("invalid json content", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        for c in _make_client(_make_mem()):
            resp = c.get("/api/suggestions/no-suggestions-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert "_error" in data["content"]

    # --- 反馈反哺（Sprint 1） ---

    def test_feedback_backfeed_useful(self):
        """标记有用 → 源记忆 useful_feedback_count +1"""
        mem = _make_mem()
        sug = _make_suggestion(source_memory_id="mem-source-1")
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }

        for c in _make_client(mem):
            resp = c.post(f"/api/suggestions/{sug['id']}/feedback", json={"feedback": "useful"})
        assert resp.status_code == 200
        # 验证 _apply_feedback_to_source 被正确调用
        mem._apply_feedback_to_source.assert_called_once_with(
            source_memory_id="mem-source-1",
            feedback="useful",
        )

    def test_feedback_backfeed_not_useful(self):
        """标记无用 → 源记忆 not_useful_feedback_count +1"""
        mem = _make_mem()
        sug = _make_suggestion(source_memory_id="mem-source-1")
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }

        for c in _make_client(mem):
            resp = c.post(f"/api/suggestions/{sug['id']}/feedback", json={"feedback": "not_useful"})
        assert resp.status_code == 200
        mem._apply_feedback_to_source.assert_called_once_with(
            source_memory_id="mem-source-1",
            feedback="not_useful",
        )

    def test_feedback_idempotent(self):
        """重复反馈同一 suggestion → 反哺仅执行一次（幂等）。"""
        mem = _make_mem()
        sug = _make_suggestion(source_memory_id="mem-source-1")
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }

        for c in _make_client(mem):
            # 第一次提交
            resp1 = c.post(f"/api/suggestions/{sug['id']}/feedback", json={"feedback": "useful"})
            assert resp1.status_code == 200

        # 第二次：suggestion 已是 reacted 状态
        reacted_meta = dict(sug["metadata"])
        reacted_meta["status"] = "reacted"
        reacted_meta["feedback"] = "useful"
        reacted_meta["feedback_time"] = time.time()
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [reacted_meta],
        }

        # 重置调用计数
        mem._apply_feedback_to_source.reset_mock()

        for c in _make_client(mem):
            resp2 = c.post(f"/api/suggestions/{sug['id']}/feedback", json={"feedback": "useful"})
        assert resp2.status_code == 200
        # 第二次不应调用反哺
        mem._apply_feedback_to_source.assert_not_called()

    def test_feedback_source_memory_missing(self):
        """源记忆已删除 → 静默跳过，不阻断主流程。"""
        mem = _make_mem()
        sug = _make_suggestion(source_memory_id="deleted-mem")
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }
        # 模拟 _apply_feedback_to_source 内部检查到源记忆不存在，正常返回
        mem._apply_feedback_to_source.return_value = None

        for c in _make_client(mem):
            resp = c.post(f"/api/suggestions/{sug['id']}/feedback", json={"feedback": "useful"})
        assert resp.status_code == 200

    def test_feedback_backfeed_raises_does_not_block(self):
        """_apply_feedback_to_source 抛出异常 → 不影响主流程返回 200，且 suggestion 状态已更新。"""
        mem = _make_mem()
        sug = _make_suggestion(source_memory_id="mem-source-1")
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }
        mem._apply_feedback_to_source.side_effect = RuntimeError("ChromaDB 超时")

        for c in _make_client(mem):
            resp = c.post(f"/api/suggestions/{sug['id']}/feedback", json={"feedback": "useful"})
        assert resp.status_code == 200

        # 验证 suggestion status 已更新为 reacted（在异常之前已写入）
        assert mem.store.update.called
        call_meta = mem.store.update.call_args[1]["metadatas"][0]
        assert call_meta["status"] == "reacted"
        assert call_meta["feedback"] == "useful"

    def test_feedback_no_source_memory_id(self):
        """suggestion 没有 source_memory_id → 跳过反哺。"""
        mem = _make_mem()
        sug = _make_suggestion(source_memory_id="")
        mem.store.get.return_value = {
            "ids": [sug["id"]],
            "documents": [sug["document"]],
            "metadatas": [sug["metadata"]],
        }

        for c in _make_client(mem):
            resp = c.post(f"/api/suggestions/{sug['id']}/feedback", json={"feedback": "useful"})
        assert resp.status_code == 200
        mem._apply_feedback_to_source.assert_called_once_with(
            source_memory_id="",
            feedback="useful",
        )

    # --- JWT 认证 ---

    def test_requires_auth_when_enabled(self):
        mem = _make_mem()
        for c in _make_client(mem, auth_disabled=False):
            resp = c.get("/api/suggestions")
            assert resp.status_code == 401
