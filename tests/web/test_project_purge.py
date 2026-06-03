"""项目删除端点单元测试。依赖 TestClient + mock store。"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_mem():
    """mock app.state.mem 的 store 层"""
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
        from memos.web.app import app

        app.state.mem = mock_mem
        client = TestClient(app)
        yield client, mock_inv


def test_get_project_stats_with_data(client_and_inv):
    """项目有数据时统计正确"""
    c, _ = client_and_inv
    resp = c.get("/api/projects/test-pid/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["by_type"]["fact"] == 1
    assert data["by_type"]["decision"] == 1
    assert data["by_type"]["todo"] == 1


def test_get_project_stats_empty(client_and_inv):
    """项目无数据时返回空统计"""
    from memos.web.app import app
    mem = MagicMock()
    mem.store.get.return_value = {"ids": [], "metadatas": []}
    app.state.mem = mem
    c2 = TestClient(app)
    resp = c2.get("/api/projects/empty-pid/stats")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "by_type": {}}


def test_get_project_stats_store_error(client_and_inv):
    """store.get 抛出异常时容错"""
    from memos.web.app import app
    mem = MagicMock()
    mem.store.get.side_effect = Exception("DB error")
    app.state.mem = mem
    c2 = TestClient(app)
    resp = c2.get("/api/projects/broken-pid/stats")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "by_type": {}}


def test_delete_project_with_data(client_and_inv):
    """删除有数据的项目，应删除所有 ID 并失效缓存"""
    c, mock_inv = client_and_inv
    resp = c.delete("/api/projects/test-pid")
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] is True
    assert data["count"] == 3
    mock_inv.assert_called_once()


def test_delete_project_empty(client_and_inv):
    """删除空项目（无 ID 返回），幂等"""
    from memos.web.app import app
    mem = MagicMock()
    mem.store.get.return_value = {"ids": [], "metadatas": []}
    app.state.mem = mem
    c2 = TestClient(app)
    with patch("memos.web.routes.system._invalidate_projects_cache") as mock_inv:
        resp = c2.delete("/api/projects/empty-pid")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "count": 0}
        mock_inv.assert_called_once()


def test_delete_project_nonexistent(client_and_inv):
    """删除不存在项目，幂等"""
    from memos.web.app import app
    mem = MagicMock()
    mem.store.get.return_value = {"ids": [], "metadatas": []}
    app.state.mem = mem
    c2 = TestClient(app)
    with patch("memos.web.routes.system._invalidate_projects_cache") as mock_inv:
        resp = c2.delete("/api/projects/no-such-project")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "count": 0}
        mock_inv.assert_called_once()
