"""项目删除端点单元测试。依赖 TestClient + mock store。"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_mem():
    """mock app.state.context_memory 的 store 层"""
    mem = MagicMock()
    mem.store.get.return_value = {
        "ids": ["id1", "id2", "id3"],
        "metadatas": [
            {"type": "fact", "project_id": "test-pid"},
            {"type": "decision", "project_id": "test-pid"},
            {"type": "todo", "project_id": "test-pid"},
        ],
    }
    return mem


@pytest.fixture
def client_and_inv(mock_mem):
    """返回 (TestClient, mock_invalidate_cache) 元组"""
    with patch("memos.web.routes.system._invalidate_projects_cache") as mock_inv:
        with patch("memos.server.app.ContextMemory", return_value=mock_mem):
            from memos.web.app import app

            app.state.context_memory = mock_mem
            client = TestClient(app)
            yield client, mock_inv


def test_get_project_stats_with_data(client_and_inv):
    c, _ = client_and_inv
    resp = c.get("/api/projects/test-pid/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["by_type"]["fact"] == 1
    assert data["by_type"]["decision"] == 1
    assert data["by_type"]["todo"] == 1


def test_get_project_stats_empty():
    mem = MagicMock()
    mem.store.get.return_value = {"ids": [], "metadatas": []}
    with patch("memos.server.app.ContextMemory", return_value=mem):
        from memos.web.app import app

        app.state.context_memory = mem
        c2 = TestClient(app)
        resp = c2.get("/api/projects/empty-pid/stats")
        assert resp.status_code == 200
        assert resp.json() == {"total": 0, "by_type": {}}


def test_get_project_stats_store_error():
    mem = MagicMock()
    mem.store.get.side_effect = Exception("DB error")
    with patch("memos.server.app.ContextMemory", return_value=mem):
        from memos.web.app import app

        app.state.context_memory = mem
        c2 = TestClient(app)
        resp = c2.get("/api/projects/broken-pid/stats")
        assert resp.status_code == 200
        assert resp.json() == {"total": 0, "by_type": {}}


def test_delete_project_with_data(client_and_inv):
    c, mock_inv = client_and_inv
    resp = c.delete("/api/projects/test-pid")
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] is True
    assert data["count"] == 3
    mock_inv.assert_called_once()


def test_delete_project_empty():
    mem = MagicMock()
    mem.store.get.return_value = {"ids": [], "metadatas": []}
    with patch("memos.server.app.ContextMemory", return_value=mem):
        from memos.web.app import app

        app.state.context_memory = mem
        c2 = TestClient(app)
        with patch("memos.web.routes.system._invalidate_projects_cache") as mock_inv:
            resp = c2.delete("/api/projects/empty-pid")
            assert resp.status_code == 200
            assert resp.json() == {"deleted": True, "count": 0}
            mock_inv.assert_called_once()


def test_delete_project_nonexistent():
    mem = MagicMock()
    mem.store.get.return_value = {"ids": [], "metadatas": []}
    with patch("memos.server.app.ContextMemory", return_value=mem):
        from memos.web.app import app

        app.state.context_memory = mem
        c2 = TestClient(app)
        with patch("memos.web.routes.system._invalidate_projects_cache") as mock_inv:
            resp = c2.delete("/api/projects/no-such-project")
            assert resp.status_code == 200
            assert resp.json() == {"deleted": True, "count": 0}
            mock_inv.assert_called_once()
