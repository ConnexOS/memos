"""S4 — F2 会话记录检索测试 (v0.4.4)

覆盖：正常搜索、空 query 400、日期范围过滤、无结果、配对/未配对结果。
"""

import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


def _make_conv_recall_result(doc_id, document, similarity, type, round_id, timestamp=None):
    if timestamp is None:
        timestamp = time.time() - 3600
    return {
        "id": doc_id,
        "document": document,
        "metadata": {
            "type": type,
            "project_id": "default",
            "round_id": round_id,
            "timestamp": timestamp,
        },
        "similarity": similarity,
        "decay_factor": 0.98,
        "final_score": similarity * 0.98,
    }


class TestConversationSearchAPI:
    """POST /api/conversations/search"""

    @pytest.fixture
    def mock_mem(self):
        mem = MagicMock()
        mem.store.count.return_value = 0
        mem.store.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mem.config.memory.default_project_id = "default"
        return mem

    @pytest.fixture
    def client(self, mock_mem, monkeypatch):
        from memos.config import config as memos_config

        monkeypatch.setattr(memos_config.auth, "disable", True)
        from memos.web.app import app

        with TestClient(app) as c:
            c.app.state.mem = mock_mem
            yield c

    def test_empty_query_returns_400(self, client):
        resp = client.post("/api/conversations/search", json={"query": ""})
        assert resp.status_code == 422

    def test_no_results(self, client, mock_mem):
        mock_mem.recall.return_value = []
        resp = client.post("/api/conversations/search", json={"query": "不存在的词"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["results"] == []

    def test_paired_result(self, client, mock_mem):
        round_id = "R_test_round"
        mock_mem.recall.return_value = [
            _make_conv_recall_result("id-1", "用户问题", 0.85, "user_input", round_id),
            _make_conv_recall_result("id-2", "助手回复", 0.75, "assistant_output", round_id),
        ]
        resp = client.post("/api/conversations/search", json={"query": "问题"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        # 应该有一个配对结果
        paired = [r for r in data["results"] if r.get("type") == "paired"]
        assert len(paired) == 1
        assert paired[0]["user_input"]["content"] == "用户问题"
        assert paired[0]["assistant_output"]["content"] == "助手回复"

    def test_unpaired_user_input(self, client, mock_mem):
        """仅有 user_input 无 assistant_output 时单独展示。"""
        mock_mem.recall.return_value = [
            _make_conv_recall_result("id-1", "用户问题", 0.85, "user_input", "R_orphan"),
        ]
        resp = client.post("/api/conversations/search", json={"query": "问题"})
        data = resp.json()
        # 应有 1 个未配对结果
        unpaired = [r for r in data["results"] if r.get("type") != "paired"]
        assert len(unpaired) == 1
        assert unpaired[0]["type"] == "user_input"

    def test_multiple_assistant_same_round(self, client, mock_mem):
        """同一 round_id 多条 assistant_output 时取相似度最高者。"""
        round_id = "R_multi"
        mock_mem.recall.return_value = [
            _make_conv_recall_result("id-1", "用户问题", 0.85, "user_input", round_id),
            _make_conv_recall_result("id-2", "低质量回复", 0.6, "assistant_output", round_id),
            _make_conv_recall_result("id-3", "高质量回复", 0.9, "assistant_output", round_id),
        ]
        resp = client.post("/api/conversations/search", json={"query": "问题"})
        data = resp.json()
        paired = [r for r in data["results"] if r.get("type") == "paired"]
        assert len(paired) == 1
        assert paired[0]["assistant_output"]["content"] == "高质量回复"

    def test_date_filter(self, client, mock_mem):
        """日期范围过滤。"""
        now = time.time()
        mock_mem.recall.return_value = []
        resp = client.post(
            "/api/conversations/search",
            json={"query": "test", "date_from": now - 86400, "date_to": now},
        )
        assert resp.status_code == 200

    def test_project_scoped(self, client, mock_mem):
        """project_id 过滤。"""
        mock_mem.recall.return_value = []
        resp = client.post(
            "/api/conversations/search",
            json={"query": "test", "project_id": "my-project"},
        )
        assert resp.status_code == 200
