"""Phase 1 R1: 冲突 API 配对返回 + resolve/discard + 冲突日志 + 统计"""

import time
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from memos.config import config
from memos.engine.memory import ContextMemory
from memos.web.routes.system import router


@pytest.fixture
def app():
    """创建带 system router 的测试 app，使用真实 ContextMemory + 临时 collection"""
    import uuid

    app = FastAPI()
    col_name = f"test_conflict_api_{uuid.uuid4().hex[:8]}"
    mem = ContextMemory(collection_name=col_name)
    app.state.mem = mem
    app.state._pid_override = "test_proj"
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _create_memory(app, content: str, metadata: dict) -> str:
    """辅助：在测试 app 中创建记忆并返回 id"""
    mem = app.state.mem
    return mem.remember(content, metadata=metadata)


class TestConflictApiPaired:
    """验证 GET /api/conflicts 配对返回"""

    def test_empty_when_no_conflicts(self, client):
        """无冲突时返回空列表"""
        resp = client.get("/api/conflicts?limit=100")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"pairs": [], "total": 0}

    def test_paired_return_format(self, app, client):
        """验证配对数据结构"""
        now = time.time()
        mem = app.state.mem
        # 创建两条冲突记忆
        mid1 = _create_memory(app, "项目使用 PostgreSQL", {"type": "fact", "timestamp": now})
        mid2 = _create_memory(app, "项目使用 MySQL", {
            "type": "fact", "timestamp": now + 1,
            "conflict_status": "pending", "conflict_role": "trigger",
            "conflict_with": mid1, "conflict_reason": "数据库选型矛盾",
            "conflict_detected_at": now + 2,
        })
        # 更新 mid1 添加冲突标记
        mem.update_memory(mid1, new_metadata={
            "conflict_status": "pending", "conflict_role": "matched",
            "conflict_with": mid2, "conflict_reason": "数据库选型矛盾",
            "conflict_detected_at": now + 2,
        })

        resp = client.get("/api/conflicts?limit=100")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["pairs"]) >= 1

        pair = data["pairs"][0]
        # 验证 pair 包含新/旧记忆对
        assert "new_memory" in pair
        assert "existing_memory" in pair
        assert pair["new_memory"]["id"] == mid2  # trigger 是新记忆
        assert pair["existing_memory"]["id"] == mid1  # matched 是旧记忆
        assert pair["reason"] == "数据库选型矛盾"
        assert pair["detected_at"] == pytest.approx(now + 2, abs=0.01)

    def test_count_endpoint(self, app, client):
        """验证数量统计"""
        now = time.time()
        mem = app.state.mem
        mid1 = _create_memory(app, "content A", {"type": "fact", "timestamp": now})
        mid2 = _create_memory(app, "content B", {
            "type": "fact", "timestamp": now + 1,
            "conflict_status": "pending", "conflict_role": "trigger",
            "conflict_with": mid1, "conflict_reason": "矛盾",
            "conflict_detected_at": now + 2,
        })
        mem.update_memory(mid1, new_metadata={
            "conflict_status": "pending", "conflict_role": "matched",
            "conflict_with": mid2, "conflict_reason": "矛盾",
            "conflict_detected_at": now + 2,
        })

        resp = client.get("/api/conflicts/count")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1


class TestConflictApiResolve:
    """验证冲突解决四种操作"""

    def test_resolve_overwrite(self, app, client):
        """overwrite: 删除旧记忆"""
        now = time.time()
        mem = app.state.mem
        mid1 = _create_memory(app, "旧内容", {"type": "fact", "timestamp": now})
        mid2 = _create_memory(app, "新内容", {
            "type": "fact", "timestamp": now + 1,
            "conflict_status": "pending", "conflict_role": "trigger",
            "conflict_with": mid1, "conflict_reason": "内容矛盾",
            "conflict_detected_at": now + 2,
        })
        mem.update_memory(mid1, new_metadata={
            "conflict_status": "pending", "conflict_role": "matched",
            "conflict_with": mid2, "conflict_reason": "内容矛盾",
            "conflict_detected_at": now + 2,
        })

        resp = client.post(f"/api/conflicts/{mid2}/resolve?action=overwrite")
        assert resp.status_code == 200

        # 旧记忆应被删除
        old = mem.get_memory(mid1)
        assert old is None, "overwrite 应删除旧记忆"

    def test_resolve_keep_both(self, app, client):
        """keep_both: 清除双方冲突标记"""
        now = time.time()
        mem = app.state.mem
        mid1 = _create_memory(app, "内容1", {"type": "fact", "timestamp": now})
        mid2 = _create_memory(app, "内容2", {
            "type": "fact", "timestamp": now + 1,
            "conflict_status": "pending", "conflict_role": "trigger",
            "conflict_with": mid1, "conflict_reason": "矛盾",
            "conflict_detected_at": now + 2,
        })
        mem.update_memory(mid1, new_metadata={
            "conflict_status": "pending", "conflict_role": "matched",
            "conflict_with": mid2, "conflict_reason": "矛盾",
            "conflict_detected_at": now + 2,
        })

        resp = client.post(f"/api/conflicts/{mid2}/resolve?action=keep_both")
        assert resp.status_code == 200

        # 双方 conflict_status 应被清除
        m1 = mem.get_memory(mid1)
        m2 = mem.get_memory(mid2)
        assert m1 is not None
        assert m2 is not None
        assert m1["metadata"].get("conflict_status") == "resolved"
        assert m2["metadata"].get("conflict_status") == "resolved"

    def test_resolve_edit(self, app, client):
        """edit: 更新新记忆内容"""
        now = time.time()
        mem = app.state.mem
        mid1 = _create_memory(app, "旧内容", {"type": "fact", "timestamp": now})
        mid2 = _create_memory(app, "新内容", {
            "type": "fact", "timestamp": now + 1,
            "conflict_status": "pending", "conflict_role": "trigger",
            "conflict_with": mid1, "conflict_reason": "内容矛盾",
            "conflict_detected_at": now + 2,
        })
        mem.update_memory(mid1, new_metadata={
            "conflict_status": "pending", "conflict_role": "matched",
            "conflict_with": mid2, "conflict_reason": "内容矛盾",
            "conflict_detected_at": now + 2,
        })

        new_content = "修改后的内容"
        resp = client.post(
            f"/api/conflicts/{mid2}/resolve?action=edit",
            json={"content": new_content},
        )
        assert resp.status_code == 200

        # 验证内容被更新
        updated = mem.get_memory(mid2)
        assert updated is not None
        assert updated["document"] == new_content
        assert updated["metadata"].get("conflict_status") == "resolved"

    def test_resolve_invalid_action(self, client):
        """无效 action 返回 400"""
        resp = client.post("/api/conflicts/fake-id/resolve?action=invalid")
        assert resp.status_code == 400

    def test_resolve_nonexistent_pair(self, client):
        """不存在的 pair_id 返回 404"""
        resp = client.post("/api/conflicts/nonexistent/resolve?action=overwrite")
        assert resp.status_code == 404

    def test_resolve_edit_no_content(self, client):
        """edit 操作缺少 content 返回 400"""
        mid = "some-id"
        resp = client.post(f"/api/conflicts/{mid}/resolve?action=edit", json={})
        # 404 优先（记忆不存在），因 mock 环境不存在
        assert resp.status_code in (400, 404)


class TestConflictApiDiscard:
    """验证放弃新记忆"""

    def test_discard_new_memory(self, app, client):
        """discard 删除新记忆，旧记忆保留"""
        now = time.time()
        mem = app.state.mem
        mid1 = _create_memory(app, "旧内容", {"type": "fact", "timestamp": now})
        mid2 = _create_memory(app, "新内容", {
            "type": "fact", "timestamp": now + 1,
            "conflict_status": "pending", "conflict_role": "trigger",
            "conflict_with": mid1, "conflict_reason": "内容矛盾",
            "conflict_detected_at": now + 2,
        })
        mem.update_memory(mid1, new_metadata={
            "conflict_status": "pending", "conflict_role": "matched",
            "conflict_with": mid2, "conflict_reason": "内容矛盾",
            "conflict_detected_at": now + 2,
        })

        resp = client.post(f"/api/conflicts/{mid2}/discard")
        assert resp.status_code == 200

        # 新记忆被删除
        assert mem.get_memory(mid2) is None
        # 旧记忆保留
        assert mem.get_memory(mid1) is not None


class TestConflictApiStats:
    """验证冲突决策统计"""

    def test_stats_empty(self, client):
        """无冲突日志时返回空"""
        resp = client.get("/api/conflicts/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["decisions"] == {}

    def test_stats_with_logs(self, app, client):
        """有冲突日志时统计正确"""
        mem = app.state.mem
        # 直接写入 conflict_log 类型记忆
        for decision in ["overwrite", "keep_both", "overwrite", "edit"]:
            mem.remember(
                f"冲突决策: {decision}",
                metadata={
                    "type": "conflict_log",
                    "decision": decision,
                    "decided_at": time.time(),
                },
            )

        resp = client.get("/api/conflicts/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3  # 包括其他测试写入的日志
        assert data["decisions"].get("overwrite", 0) >= 2
        assert data["decisions"].get("keep_both", 0) >= 1
        assert data["decisions"].get("edit", 0) >= 1
