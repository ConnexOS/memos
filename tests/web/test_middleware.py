"""测试 ProjectContextMiddleware 的 project_id 提取逻辑。

覆盖 5 个场景：
1. 查询参数传递 → 端点正常响应
2. 无参数 → CWD 兜底（与 detect_project_id() 一致）
3. 跨项目写操作 → 404
4. 项目列表豁免查询 → 正常加载所有项目
5. 中间件注册 → 在 app.user_middleware 中确认
"""
import pytest
from unittest.mock import MagicMock, patch
from starlette.testclient import TestClient

from memos.web.utils import detect_project_id


_MOCK = MagicMock()
_MOCK.store.get.return_value = {"ids": [], "metadatas": []}
_MOCK.count_memories.return_value = 0
_MOCK.list_memories.return_value = []
_MOCK.remember.return_value = "new-id"
_MOCK.get_memory.return_value = None


@pytest.fixture
def client():
    """TestClient with mocked ContextMemory — 避免 ChromaDB 连接。"""
    with patch("memos.server.app.ContextMemory", return_value=_MOCK):
        from memos.server.app import create_unified_app

        app = create_unified_app()
        app.state.context_memory = _MOCK
        with TestClient(app) as c:
            yield c


class TestProjectContextMiddleware:

    def test_query_param_注入(self, client):
        """?project_id=xxx → 中间件注入后端点正常响应"""
        for pid in ["test-pid", "project-abc", "123"]:
            resp = client.get(f"/api/projects?project_id={pid}")
            assert resp.status_code == 200, f"请求 /api/projects?project_id={pid} 失败"

    def test_无参数_CWD兜底(self, client):
        """无 project_id 参数 → 使用 CWD 计算的默认值"""
        default_pid = detect_project_id()
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        assert resp.json().get("current_project") == default_pid

    def test_跨项目写拒绝_todos(self):
        """A 项目操作 B 项目的数据 → 404"""
        mock_mem = MagicMock()
        mock_mem.get_memory.return_value = {
            "id": "fake-id",
            "document": "",
            "metadata": {"type": "todo", "project_id": "other-project"},
        }
        mock_mem.store.get.return_value = {"ids": [], "metadatas": []}

        with patch("memos.server.app.ContextMemory", return_value=mock_mem):
            from memos.server.app import create_unified_app

            app = create_unified_app()
            app.state.context_memory = mock_mem
            with TestClient(app) as c:
                resp = c.post(
                    "/api/todos/fake-id/status",
                    json={"todo_status": "completed"},
                    params={"project_id": "current-project"},
                )
                assert resp.status_code == 404
                assert "未找到" in resp.json()["detail"]

    def test_跨项目写拒绝_manual_suggestions(self):
        """A 项目删除 B 项目的用户建议 → 404"""
        mock_mem = MagicMock()
        mock_mem.store.get.return_value = {
            "ids": ["sug-id"],
            "documents": ["test suggestion"],
            "metadatas": [{"project_id": "other-project", "type": "manual_suggestion", "status": "pending"}],
        }

        with patch("memos.server.app.ContextMemory", return_value=mock_mem):
            from memos.server.app import create_unified_app

            app = create_unified_app()
            app.state.context_memory = mock_mem
            with TestClient(app) as c:
                resp = c.delete(
                    "/api/manual-suggestions/sug-id",
                    params={"project_id": "current-project"},
                )
                assert resp.status_code == 404
                assert "用户建议不存在" in resp.json()["detail"]

    def test_项目列表跨项目加载(self, client):
        """_get_projects_from_db 豁免：仍可加载所有项目"""
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
        assert "current_project" in data

    def test_中间件已注册(self, client):
        """ProjectContextMiddleware 在 app.user_middleware 中"""
        names = [m.cls.__name__ for m in client.app.user_middleware]
        assert "ProjectContextMiddleware" in names
        assert "AuthASGIMiddleware" in names
