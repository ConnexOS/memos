"""Phase 5: 今日回顾 API 测试"""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from memos.web.app import app


@pytest.fixture
def api_client(monkeypatch):
    """隔离环境 + TestClient + Auth mock"""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        os.environ["MEMOS_HOME"] = str(home)
        (home / "etc").mkdir(parents=True)
        (home / "memdb").mkdir(parents=True)
        (home / "etc" / "prompts").mkdir(parents=True, exist_ok=True)

        config_data = {
            "llm": {
                "endpoints": [
                    {"name": "deepseek-ai", "api_base": "http://ds/v1", "model": "d"},
                    {"name": "local-LLM", "api_base": "http://local/v1", "model": "l"},
                ],
                "active": "deepseek-ai",
            }
        }
        with open(home / "etc" / "config.json", "w") as f:
            json.dump(config_data, f)

        mock_mem = mock.Mock()
        mock_mem.store.get.return_value = {"ids": [], "documents": [], "metadatas": []}

        with (
            patch("memos.web.app.verify_session_token", return_value={"token_hash": "test", "exp": 9999999999}),
        ):
            app.state.mem = mock_mem
            client = TestClient(app, cookies={"memos_session": "test-session"})
            yield client


def _make_conversation_data(ids, docs, metas):
    return {
        "ids": ids,
        "documents": docs,
        "metadatas": metas,
    }


class TestDailyReview:
    """每日回顾 API"""

    def test_empty_date(self, api_client):
        """当天没有对话记录"""
        resp = api_client.post(
            "/api/conversations/daily-review",
            json={
                "date": "2026-01-15",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["report"] is None
        assert data["conversation_count"] == 0
        assert "对话记录" in data["message"] or "日报未生成" in data["message"]

    def test_invalid_date(self, api_client):
        """无效日期格式返回 400"""
        resp = api_client.post(
            "/api/conversations/daily-review",
            json={
                "date": "not-a-date",
            },
        )
        assert resp.status_code == 400

    def test_generate_report(self, api_client, monkeypatch):
        """正常生成日报"""
        api_client.app.state.mem.store.get.return_value = _make_conversation_data(
            ids=["c1", "c2"],
            docs=["User: 帮我添加登录页面", "Assistant: 好的，我在 auth.py 中添加了登录端点"],
            metas=[
                {"type": "user_input", "timestamp": 1704067200.0, "active": True, "project_id": "proj-1"},
                {"type": "assistant_output", "timestamp": 1704067260.0, "active": True, "project_id": "proj-1"},
            ],
        )

        llm_resp = mock.Mock()
        llm_resp.status_code = 200
        llm_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "# 2026-01-15 开发日报\n\n## 今日概要\n添加了登录功能。\n\n## 已完成工作\n- **添加登录页面**: 在 auth.py 中添加了新端点"
                    }
                }
            ]
        }

        with mock.patch("memos.engine.extractor.requests.post", return_value=llm_resp):
            resp = api_client.post(
                "/api/conversations/daily-review",
                json={
                    "date": "2026-01-15",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["report"] is not None
            assert "# 2026-01-15 开发日报" in data["report"]
            assert "添加了登录功能" in data["report"]
            assert data["conversation_count"] == 2
            assert data["llm_endpoint"] == "deepseek-ai"
            assert data["message"] == "日报生成成功"

    def test_save_as_memory(self, api_client, monkeypatch):
        """save_as_memory=True 时保存日报"""
        api_client.app.state.mem.store.get.return_value = _make_conversation_data(
            ids=["c1"],
            docs=["User: 测试消息"],
            metas=[
                {"type": "user_input", "timestamp": 1704067200.0, "active": True, "project_id": "proj-1"},
            ],
        )
        api_client.app.state.mem.remember.return_value = "saved-id-123"

        llm_resp = mock.Mock()
        llm_resp.status_code = 200
        llm_resp.json.return_value = {"choices": [{"message": {"content": "# 测试日报\n\n内容"}}]}

        with mock.patch("memos.engine.extractor.requests.post", return_value=llm_resp):
            resp = api_client.post(
                "/api/conversations/daily-review",
                json={
                    "date": "2026-01-15",
                    "save_as_memory": True,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["saved_id"] == "saved-id-123"
            assert data["report"] is not None

    def test_preview(self, api_client):
        """预览端点返回请求内容但不调用 LLM"""
        api_client.app.state.mem.store.get.return_value = _make_conversation_data(
            ids=["c1"],
            docs=["User: 测试消息"],
            metas=[
                {"type": "user_input", "timestamp": 1704067200.0, "active": True, "project_id": "proj-1"},
            ],
        )

        resp = api_client.post(
            "/api/conversations/daily-review/preview",
            json={
                "date": "2026-01-15",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert "payload" in data
        assert data["conversation_count"] == 1
        assert data["date"] == "2026-01-15"
        assert data["llm_endpoint"] == "deepseek-ai"

    def test_preview_no_conversations(self, api_client):
        """预览端点：无对话记录"""
        resp = api_client.post(
            "/api/conversations/daily-review/preview",
            json={
                "date": "2026-01-15",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation_count"] == 0
        assert data["messages"] == []

    def test_nonexistent_llm_endpoint(self, api_client):
        """LLM 端点不存在返回 502（LLMUnreachableError）"""
        api_client.app.state.mem.store.get.return_value = _make_conversation_data(
            ids=["c1"],
            docs=["User: hi"],
            metas=[
                {"type": "user_input", "timestamp": 1704067200.0, "active": True, "project_id": "p1"},
            ],
        )
        resp = api_client.post(
            "/api/conversations/daily-review",
            json={
                "date": "2026-01-15",
                "llm_endpoint": "nonexistent-ep",
            },
        )
        assert resp.status_code == 502
        data = resp.json()
        assert data["code"] == "MEM_002"
        assert "不存在" in data["message"]

    def test_missing_date_defaults_to_today(self, api_client, monkeypatch):
        """未提供 date 时默认使用今天"""
        api_client.app.state.mem.store.get.return_value = _make_conversation_data(
            ids=["c1"],
            docs=["User: hi"],
            metas=[
                {"type": "user_input", "timestamp": 1704067200.0, "active": True, "project_id": "p1"},
            ],
        )

        llm_resp = mock.Mock()
        llm_resp.status_code = 200
        llm_resp.json.return_value = {"choices": [{"message": {"content": "# 日报\n\n内容"}}]}

        with mock.patch("memos.engine.extractor.requests.post", return_value=llm_resp):
            resp = api_client.post("/api/conversations/daily-review", json={})
            assert resp.status_code == 200
            data = resp.json()
            # date 应为今天的日期
            from datetime import datetime

            today = datetime.now().strftime("%Y-%m-%d")
            assert data["date"] == today
