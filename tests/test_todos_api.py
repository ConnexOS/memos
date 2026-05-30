"""Phase 2 R2: 待办 CRUD + 状态流转 API 测试"""

import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from memos.engine.memory import ContextMemory
from memos.web.routes.todos import router


@pytest.fixture
def app():
    app = FastAPI()
    col_name = f"test_todos_api_{uuid.uuid4().hex[:8]}"
    mem = ContextMemory(collection_name=col_name)
    app.state.mem = mem
    app.state._pid_override = "test_proj"
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class TestTodosCreate:
    def test_create_todo(self, client):
        """创建待办"""
        resp = client.post("/api/todos", json={"content": "测试待办", "priority": "high"})
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["message"] == "待办已创建"

    def test_create_todo_empty_content(self, client):
        """内容为空返回 400"""
        resp = client.post("/api/todos", json={"content": ""})
        assert resp.status_code == 400

    def test_create_todo_default_priority(self, app, client):
        """默认优先级为 medium"""
        resp = client.post("/api/todos", json={"content": "默认优先级的待办"})
        assert resp.status_code == 201
        todo_id = resp.json()["id"]
        mem = app.state.mem
        item = mem.get_memory(todo_id)
        assert item["metadata"]["priority"] == "medium"
        assert item["metadata"]["todo_status"] == "pending"
        assert item["metadata"]["type"] == "todo"

    def test_create_todo_invalid_priority(self, client):
        """无效优先级返回 400"""
        resp = client.post("/api/todos", json={"content": "测试", "priority": "urgent"})
        assert resp.status_code == 400


class TestTodosList:
    def test_list_empty(self, client):
        """空列表"""
        resp = client.get("/api/todos")
        assert resp.status_code == 200
        data = resp.json()
        assert data["todos"] == []
        assert data["total"] == 0

    def test_list_with_data(self, client):
        """创建后列表有数据"""
        client.post("/api/todos", json={"content": "待办1", "priority": "high"})
        client.post("/api/todos", json={"content": "待办2", "priority": "low"})

        resp = client.get("/api/todos")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        assert len(data["todos"]) >= 2

    def test_list_filter_by_status(self, app, client):
        """按 todo_status 过滤"""
        # 创建一个 pending
        r1 = client.post("/api/todos", json={"content": "普通待办"})
        tid = r1.json()["id"]
        # 手动改为 in_progress
        client.post(f"/api/todos/{tid}/status", json={"todo_status": "in_progress"})

        resp = client.get("/api/todos?todo_status=in_progress")
        data = resp.json()
        assert all(t["todo_status"] == "in_progress" for t in data["todos"])

    def test_list_stats(self, client):
        """返回 stats 统计"""
        resp = client.get("/api/todos")
        data = resp.json()
        assert "stats" in data
        for s in ("pending", "in_progress", "completed", "cancelled"):
            assert s in data["stats"]


class TestTodosUpdate:
    def test_update_content(self, app, client):
        """编辑内容"""
        r = client.post("/api/todos", json={"content": "原始内容"})
        tid = r.json()["id"]
        resp = client.put(f"/api/todos/{tid}", json={"content": "更新后的内容"})
        assert resp.status_code == 200

        item = app.state.mem.get_memory(tid)
        assert item["document"] == "更新后的内容"

    def test_update_priority(self, app, client):
        """编辑优先级"""
        r = client.post("/api/todos", json={"content": "测试", "priority": "low"})
        tid = r.json()["id"]
        client.put(f"/api/todos/{tid}", json={"priority": "high"})

        item = app.state.mem.get_memory(tid)
        assert item["metadata"]["priority"] == "high"

    def test_update_sort_order(self, app, client):
        """编辑 sort_order"""
        r = client.post("/api/todos", json={"content": "排序测试"})
        tid = r.json()["id"]
        client.put(f"/api/todos/{tid}", json={"sort_order": 500.0})

        item = app.state.mem.get_memory(tid)
        assert item["metadata"]["sort_order"] == 500.0

    def test_update_nonexistent(self, client):
        """不存在的待办返回 404"""
        resp = client.put("/api/todos/nonexistent", json={"content": "内容"})
        assert resp.status_code == 404


class TestTodosStatus:
    def _parse_history(self, meta: dict) -> list:
        """辅助：从 metadata 中解析 status_history JSON 字符串"""
        import json
        raw = meta.get("status_history", "[]")
        return json.loads(raw) if isinstance(raw, str) else list(raw)

    def test_pending_to_in_progress(self, app, client):
        """pending → in_progress"""
        r = client.post("/api/todos", json={"content": "开始执行"})
        tid = r.json()["id"]

        resp = client.post(f"/api/todos/{tid}/status", json={"todo_status": "in_progress"})
        assert resp.status_code == 200

        item = app.state.mem.get_memory(tid)
        assert item["metadata"]["todo_status"] == "in_progress"
        assert item["metadata"]["started_at"] is not None
        assert len(self._parse_history(item["metadata"])) == 1

    def test_pending_to_completed(self, app, client):
        """pending → completed"""
        r = client.post("/api/todos", json={"content": "直接完成"})
        tid = r.json()["id"]

        client.post(f"/api/todos/{tid}/status", json={"todo_status": "completed"})
        item = app.state.mem.get_memory(tid)
        assert item["metadata"]["todo_status"] == "completed"
        assert item["metadata"]["completed_at"] is not None

    def test_in_progress_to_completed(self, app, client):
        """in_progress → completed"""
        r = client.post("/api/todos", json={"content": "进行中完成"})
        tid = r.json()["id"]
        client.post(f"/api/todos/{tid}/status", json={"todo_status": "in_progress"})
        client.post(f"/api/todos/{tid}/status", json={"todo_status": "completed"})

        item = app.state.mem.get_memory(tid)
        assert item["metadata"]["todo_status"] == "completed"
        assert len(self._parse_history(item["metadata"])) == 2

    def test_completed_to_pending(self, app, client):
        """completed → pending（重新打开）"""
        r = client.post("/api/todos", json={"content": "重新打开"})
        tid = r.json()["id"]
        client.post(f"/api/todos/{tid}/status", json={"todo_status": "completed"})
        client.post(f"/api/todos/{tid}/status", json={"todo_status": "pending"})

        item = app.state.mem.get_memory(tid)
        assert item["metadata"]["todo_status"] == "pending"

    def test_invalid_transition(self, client):
        """in_progress → pending 非法"""
        r = client.post("/api/todos", json={"content": "非法转换"})
        tid = r.json()["id"]
        client.post(f"/api/todos/{tid}/status", json={"todo_status": "in_progress"})

        resp = client.post(f"/api/todos/{tid}/status", json={"todo_status": "pending"})
        assert resp.status_code == 422

    def test_invalid_todo_status_value(self, client):
        """无效的 todo_status 值返回 400"""
        r = client.post("/api/todos", json={"content": "测试"})
        tid = r.json()["id"]
        resp = client.post(f"/api/todos/{tid}/status", json={"todo_status": "invalid"})
        assert resp.status_code == 400


class TestTodosDelete:
    def test_delete_todo(self, app, client):
        """删除待办"""
        r = client.post("/api/todos", json={"content": "将被删除"})
        tid = r.json()["id"]

        resp = client.delete(f"/api/todos/{tid}")
        assert resp.status_code == 200

        assert app.state.mem.get_memory(tid) is None

    def test_delete_nonexistent(self, client):
        """不存在的待办返回 404"""
        resp = client.delete("/api/todos/nonexistent")
        assert resp.status_code == 404
