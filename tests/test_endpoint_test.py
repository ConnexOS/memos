"""测试 F5 - LLM 端点连通性测试 (v0.4.3 适配 TCP 探测)"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """创建 TestClient，注入测试端点避免污染全局 config。"""
    # 强制重载全局 config，擦除前序测试的状态残留
    monkeypatch.setenv("MEMOS_HOME", str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from memos.config import config as memos_config

    # 重设 LLM endpoints 为测试端点
    from memos.config import LLMEndpoint

    test_ep = LLMEndpoint(name="_test_ep", api_base="http://localhost:9999/v1")
    monkeypatch.setattr(memos_config.auth, "disable", True)
    monkeypatch.setattr(memos_config.llm, "endpoints", [test_ep])

    import sys

    monkeypatch.setattr(sys.modules["memos.web.app"], "ContextMemory", MagicMock)
    from memos.web.app import app

    with TestClient(app) as c:
        yield c


class TestLLMTestConnectionAPI:
    """POST /api/llm/test-connection"""

    def test_endpoint_not_found(self, client):
        resp = client.post("/api/llm/test-connection", json={"endpoint_id": "nonexistent"})
        assert resp.status_code == 404

    def test_health_endpoint_success(self, client):
        """v0.4.3: LLM 探活改用 asyncio TCP 探测，mock open_connection 成功"""
        mock_reader = MagicMock()
        mock_writer = MagicMock()

        async def mock_open_conn(host, port):
            return mock_reader, mock_writer

        with patch("asyncio.open_connection", side_effect=mock_open_conn):
            resp = client.post("/api/llm/test-connection", json={"endpoint_id": "_test_ep"})
            data = resp.json()
            assert data["status"] == "ok"
            assert "latency_ms" in data

    def test_connection_timeout_error(self, client):
        """v0.4.3: TCP 连接超时"""

        async def mock_timeout(host, port):
            raise asyncio.TimeoutError()

        with patch("asyncio.open_connection", side_effect=mock_timeout):
            resp = client.post("/api/llm/test-connection", json={"endpoint_id": "_test_ep"})
            data = resp.json()
            assert data["status"] == "error"
            assert "超时" in data["reason"]

    def test_connection_refused_error(self, client):
        """v0.4.3: TCP 连接被拒绝"""

        async def mock_refused(host, port):
            raise ConnectionRefusedError("Connection refused")

        with patch("asyncio.open_connection", side_effect=mock_refused):
            resp = client.post("/api/llm/test-connection", json={"endpoint_id": "_test_ep"})
            data = resp.json()
            assert data["status"] == "error"

    def test_server_error_status(self, client):
        """v0.4.3: OS 错误"""

        async def mock_os_error(host, port):
            raise OSError("Network unreachable")

        with patch("asyncio.open_connection", side_effect=mock_os_error):
            resp = client.post("/api/llm/test-connection", json={"endpoint_id": "_test_ep"})
            data = resp.json()
            assert data["status"] == "error"
