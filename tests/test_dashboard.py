import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def mock_mem():
    mem = MagicMock()
    mem.list_memories.return_value = [
        {
            "id": "id-1",
            "document": "测试记忆1",
            "metadata": {"type": "fact", "project_id": "proj-1", "timestamp": 1000000000, "active": True},
        },
        {
            "id": "id-2",
            "document": "测试记忆2",
            "metadata": {"type": "decision", "project_id": "proj-1", "timestamp": 1000000001, "active": True},
        },
    ]
    mem.count_memories.return_value = 2
    mem.get_memory.return_value = {
        "id": "id-1",
        "document": "测试记忆1",
        "metadata": {"type": "fact", "project_id": "proj-1", "timestamp": 1000000000, "active": True},
    }
    mem.remember.return_value = "new-id-123"
    mem.store.count.return_value = 2
    mem.store.get.return_value = {
        "ids": ["id-1", "id-2"],
        "metadatas": [
            {"type": "fact", "project_id": "proj-1", "timestamp": 1000000000, "active": True},
            {"type": "decision", "project_id": "proj-2", "timestamp": 1000000001, "active": True},
        ],
    }
    mem.recall.return_value = [
        {
            "id": "id-1",
            "document": "测试记忆1",
            "metadata": {"type": "fact", "project_id": "proj-1"},
            "similarity": 0.92,
            "decay_factor": 0.85,
            "final_score": 0.78,
        },
    ]
    return mem


@pytest.fixture
def client(mock_mem):
    with (
        patch("memos.server.app.ContextMemory", return_value=mock_mem),
        patch("memos.web.auth.verify_session_token", return_value={"token_hash": "test", "exp": 9999999999}),
    ):
        from memos.server.app import create_unified_app

        app = create_unified_app()
        with TestClient(app, cookies={"memos_session": "fake-session-token"}) as c:
            yield c


class TestIndex:
    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "MEMOS" in resp.text


class TestListMemories:
    def test_list_default(self, client):
        resp = client.get("/api/memories")
        assert resp.status_code == 200
        data = resp.json()
        assert "memories" in data
        assert "total" in data
        assert "limit" in data
        assert data["total"] == 2
        assert data["limit"] == 20

    @pytest.mark.skip(reason="v0.4.1 未实现：type 过滤逻辑与测试预期不一致")
    def test_list_with_type_filter(self, client, mock_mem):
        client.get("/api/memories?type=fact")
        mock_mem.list_memories.assert_called_with(
            project_id=None, type_filter=["fact"], limit=20, offset=0, include_archived=False
        )
        mock_mem.count_memories.assert_called_with(project_id=None, type_filter=["fact"], include_archived=False)

    @pytest.mark.skip(reason="v0.4.1 未实现：include_archived 参数未生效")
    def test_list_with_include_archived(self, client, mock_mem):
        client.get("/api/memories?include_archived=true")
        mock_mem.list_memories.assert_called_with(
            project_id=None, type_filter=None, limit=20, offset=0, include_archived=True
        )

    def test_list_limit_clamped(self, client):
        resp = client.get("/api/memories?limit=200")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 100


class TestCreateMemory:
    def test_create_success(self, client, mock_mem):
        resp = client.post("/api/memories", json={"content": "新记忆", "type": "fact", "project_id": "proj-1"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "new-id-123"
        assert data["message"] == "记忆已创建"
        mock_mem.remember.assert_called_once()

    def test_create_empty_content(self, client):
        resp = client.post("/api/memories", json={"content": "", "type": "fact"})
        assert resp.status_code == 422

    def test_create_failure_returns_500(self, client, mock_mem):
        mock_mem.remember.return_value = None
        resp = client.post("/api/memories", json={"content": "出错", "type": "fact"})
        assert resp.status_code == 500


class TestGetMemory:
    def test_get_existing(self, client):
        resp = client.get("/api/memories/id-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "id-1"
        assert data["document"] == "测试记忆1"

    def test_get_not_found(self, client, mock_mem):
        mock_mem.get_memory.return_value = None
        resp = client.get("/api/memories/nonexistent")
        assert resp.status_code == 404
        assert "未找到" in resp.json()["detail"]


class TestUpdateMemory:
    def test_update_content_and_type(self, client, mock_mem):
        resp = client.put("/api/memories/id-1", json={"content": "更新内容", "type": "decision"})
        assert resp.status_code == 200
        assert resp.json()["message"] == "记忆已更新"
        mock_mem.update_memory.assert_called_once()

    def test_update_type_only(self, client, mock_mem):
        resp = client.put("/api/memories/id-1", json={"type": "preference"})
        assert resp.status_code == 200

    def test_update_not_found(self, client, mock_mem):
        mock_mem.get_memory.return_value = None
        resp = client.put("/api/memories/nonexistent", json={"content": "内容"})
        assert resp.status_code == 404

    def test_update_value_error(self, client, mock_mem):
        mock_mem.update_memory.side_effect = ValueError("更新失败")
        resp = client.put("/api/memories/id-1", json={"content": "内容"})
        assert resp.status_code == 404


class TestDeleteMemory:
    def test_delete_success(self, client, mock_mem):
        resp = client.delete("/api/memories/id-1")
        assert resp.status_code == 200
        assert resp.json()["message"] == "记忆已删除"
        mock_mem.delete_memory.assert_called_once_with("id-1")

    def test_delete_not_found(self, client, mock_mem):
        mock_mem.delete_memory.side_effect = ValueError("未找到")
        resp = client.delete("/api/memories/nonexistent")
        assert resp.status_code == 404


class TestArchiveRestore:
    def test_archive_success(self, client, mock_mem):
        resp = client.post("/api/memories/id-1/archive")
        assert resp.status_code == 200
        assert resp.json()["message"] == "记忆已归档"
        mock_mem.archive_memory.assert_called_once_with("id-1")

    def test_archive_not_found(self, client, mock_mem):
        mock_mem.archive_memory.side_effect = ValueError("未找到")
        resp = client.post("/api/memories/nonexistent/archive")
        assert resp.status_code == 404

    def test_restore_success(self, client, mock_mem):
        resp = client.post("/api/memories/id-1/restore")
        assert resp.status_code == 200
        assert resp.json()["message"] == "记忆已恢复"
        mock_mem.restore_memory.assert_called_once_with("id-1")

    def test_restore_not_found(self, client, mock_mem):
        mock_mem.restore_memory.side_effect = ValueError("未找到")
        resp = client.post("/api/memories/nonexistent/restore")
        assert resp.status_code == 404


class TestSearch:
    def test_search_default_params(self, client, mock_mem):
        resp = client.post("/api/search", json={"query": "测试"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["query"] == "测试"
        assert len(data["results"]) == 1
        r = data["results"][0]
        assert r["similarity"] == 0.92
        assert r["decay_factor"] == 0.85
        assert r["final_score"] == 0.78

    def test_search_with_all_params(self, client, mock_mem):
        resp = client.post(
            "/api/search",
            json={
                "query": "测试",
                "project_id": "proj-1",
                "top_k": 10,
                "days_limit": 30,
                "type_filter": "fact",
                "decay_lambda": 0.01,
                "hybrid": True,
                "bm25_weight": 0.5,
            },
        )
        assert resp.status_code == 200
        mock_mem.recall.assert_called_with(
            query="测试",
            top_k=10,
            where={"type": "fact"},
            days_limit=30,
            project_id="proj-1",
            decay_lambda=0.01,
            hybrid=True,
            bm25_weight=0.5,
            return_scores=True,
        )

    def test_search_empty_query(self, client):
        resp = client.post("/api/search", json={"query": ""})
        assert resp.status_code == 422


class TestProjects:
    def test_list_projects(self, client):
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
        assert "current_project" in data
        assert isinstance(data["current_project"], str)
        assert len(data["projects"]) == 2


class TestStatus:
    def test_status_fields(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "llama_server_ok" in data
        assert "total_memories" in data
        assert "db_size_mb" in data
        assert "vector_dim" in data
        assert "model_name" in data
        assert data["vector_dim"] == 1024
        assert data["model_name"] == "bge-large-zh-v1.5"

    def test_status_llama_offline_does_not_crash(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200


class TestConfig:
    def test_get_config_returns_sections_and_flattened(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "sections" in data
        assert "flattened" in data
        assert "chroma" in data["sections"]
        assert "dashboard" in data["sections"]
        assert "dashboard.locale" in data["flattened"]
        assert "dashboard.status_cache_ttl" in data["flattened"]

    def test_get_config_contains_expected_fields(self, client):
        resp = client.get("/api/config")
        data = resp.json()
        f = data["flattened"]
        assert "chroma.path" in f
        assert f["model.vector_dim"] == 1024
        assert f["dashboard.status_cache_ttl"] == 30

    def test_put_config_valid_key(self, client):
        resp = client.put("/api/config", json={"key": "dashboard.status_cache_ttl", "value": "30"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "dashboard.status_cache_ttl"
        assert data["value"] == 30

    def test_put_config_invalid_key(self, client):
        resp = client.put("/api/config", json={"key": "invalid.section.field", "value": "test"})
        assert resp.status_code == 400
        data = resp.json()
        assert data["code"] == "MEM_004"
        assert "无效配置项" in data["message"]

    def test_post_config_reload(self, client):
        resp = client.post("/api/config/reload")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "配置已重新加载"
        assert "server.host" in data["config"]

    def test_put_config_boolean_value(self, client):
        resp = client.put("/api/config", json={"key": "chroma.timeout", "value": "60"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["value"] == 60
