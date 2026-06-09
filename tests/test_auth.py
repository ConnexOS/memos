"""测试 F1 - Dashboard 登录认证（auth.py + dashboard auth 中间件 + CLI token 生成）"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from memos.web.auth import (
    create_session_token,
    generate_secret_key,
    generate_token,
    hash_token,
    verify_token,
    verify_session_token,
)


class TestAuthUtils:
    """auth.py 工具函数"""

    def test_hash_token_is_deterministic(self):
        """bcrypt 默认不同 salt → 两次 hash_token 结果不同，这是预期行为。"""
        t1 = hash_token("my-test-token")
        t2 = hash_token("my-test-token")
        assert t1 != t2  # bcrypt 每次产生不同 hash

    def test_hash_token_different_tokens_different_hash(self):
        assert hash_token("token-a") != hash_token("token-b")

    def test_verify_token_works(self):
        token = "test-token-123"
        h = hash_token(token)
        assert verify_token(token, h) is True
        assert verify_token("wrong-token", h) is False

    def test_create_and_verify_session(self):
        secret = "test-secret-key"
        token_hash = hash_token("test-token")
        session = create_session_token(token_hash, secret, 3600)
        payload = verify_session_token(session, secret)
        assert payload is not None
        assert payload["token_hash"] == token_hash

    def test_verify_rejects_bad_signature(self):
        secret = "test-secret-key"
        session = create_session_token(hash_token("test"), secret, 3600)
        bad = session[:-4] + "xxxx"
        assert verify_session_token(bad, secret) is None

    def test_verify_rejects_expired(self):
        secret = "test-secret-key"
        session = create_session_token(hash_token("test"), secret, -60)
        assert verify_session_token(session, secret) is None

    def test_verify_rejects_wrong_secret(self):
        session = create_session_token(hash_token("test"), "secret-a", 3600)
        assert verify_session_token(session, "secret-b") is None

    def test_verify_rejects_malformed_token(self):
        assert verify_session_token("not-a-jwt", "secret") is None
        assert verify_session_token("a.b.c.d", "secret") is None

    def test_generate_token_is_random(self):
        t1 = generate_token()
        t2 = generate_token()
        assert t1 != t2
        assert t1.startswith("mtok_")
        assert len(t1) == 25  # "mtok_" + 20 hex chars

    def test_generate_secret_key_is_random(self):
        s1 = generate_secret_key()
        s2 = generate_secret_key()
        assert s1 != s2
        assert len(s1) == 128  # 64 bytes hex


class TestDashboardAuth:
    """Dashboard 登录 API 和中间件（使用 mocked ContextMemory）"""

    @pytest.fixture
    def auth_client(self):
        token = "mtok_b945ce0a170d757c1cd2"
        token_hash = hash_token(token)
        secret = "e7227707a298bb548f65ddb49616c6709c052813009652f58d38fdbb99f2b369067caab40e8137e34bd078f8058bb30d5c3062d44e381742e8427f7092eea81f"

        mock_mem = MagicMock()
        mock_mem.store.get.return_value = {"ids": [], "metadatas": []}
        mock_mem.store.count.return_value = 0
        mock_mem.list_memories.return_value = []
        mock_mem.recall.return_value = []

        with (
            patch("memos.server.app.ContextMemory", return_value=mock_mem),
            patch("memos.config.config.auth.secret_key", secret),
            patch("memos.config.config.auth.disable", False),
            patch("memos.config.loader.config.auth.secret_key", secret),
            patch("memos.config.loader.config.auth.disable", False),
            patch("memos.web.auth.verify_token_against_users",
                   side_effect=lambda t: {"creator_id": "admin", "role": "admin", "name": "admin"}
                   if t == token else None),
        ):
            from memos.web.app import app

            with TestClient(app) as c:
                yield c, token, token_hash, secret

    def test_login_page_returns_html(self, auth_client):
        c, *_ = auth_client
        resp = c.get("/login")
        assert resp.status_code == 200
        assert "登录" in resp.text

    def test_login_success_with_correct_token(self, auth_client):
        c, token, *_ = auth_client
        resp = c.post("/api/auth/login", json={"token": token})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["creator_id"] == "admin"
        assert data["role"] == "admin"

    def test_login_fails_with_wrong_token(self, auth_client):
        c, *_ = auth_client
        resp = c.post("/api/auth/login", json={"token": "wrong-token"})
        assert resp.status_code == 401

    def test_login_fails_with_empty_token(self, auth_client):
        c, *_ = auth_client
        resp = c.post("/api/auth/login", json={"token": ""})
        assert resp.status_code == 422

    def test_api_rejects_without_auth(self, auth_client):
        c, *_ = auth_client
        resp = c.get("/api/memories")
        assert resp.status_code == 401

    def test_api_allows_with_auth_cookie(self, auth_client):
        c, _, token_hash, secret = auth_client
        session = create_session_token(token_hash, secret, 3600)
        c.cookies.set("memos_session", session)
        resp = c.get("/api/status")
        assert resp.status_code == 200

    def test_api_allows_with_auth_header(self, auth_client):
        c, _, token_hash, secret = auth_client
        session = create_session_token(token_hash, secret, 3600)
        resp = c.get("/api/status", headers={"Authorization": f"Bearer {session}"})
        assert resp.status_code == 200

    def test_api_rejects_expired_session(self, auth_client):
        c, _, token_hash, secret = auth_client
        session = create_session_token(token_hash, secret, -60)
        c.cookies.set("memos_session", session)
        resp = c.get("/api/status")
        assert resp.status_code == 401

    def test_index_redirects_to_login_without_auth(self, auth_client):
        c, *_ = auth_client
        resp = c.get("/", follow_redirects=False)
        assert resp.status_code == 307

    def test_index_shows_dashboard_with_auth(self, auth_client):
        c, _, token_hash, secret = auth_client
        session = create_session_token(token_hash, secret, 3600)
        c.cookies.set("memos_session", session)
        resp = c.get("/")
        assert resp.status_code == 200
        assert "仪表板" in resp.text or "MEMOS" in resp.text

    def test_login_shows_error_for_unconfigured_auth(self):
        mock_mem = MagicMock()
        with (
            patch("memos.server.app.ContextMemory", return_value=mock_mem),
            patch("memos.config.config.auth.token_hash", ""),
            patch("memos.config.config.auth.secret_key", ""),
            patch("memos.config.loader.config.auth.token_hash", ""),
            patch("memos.config.loader.config.auth.secret_key", ""),
        ):
            from memos.web.app import app

            with TestClient(app) as c:
                resp = c.post("/api/auth/login", json={"token": "anything"})
                assert resp.status_code == 401


class TestCLIAuth:
    """CLI init 中认证 Token 生成"""

    def test_init_generates_token_on_first_run(self, monkeypatch, capsys):
        tmp = tempfile.mkdtemp(prefix="memos-auth-test-")
        try:
            home_path = Path(tmp) / ".memos"
            monkeypatch.setenv("MEMOS_HOME", str(home_path))
            monkeypatch.setattr("memos.storage.embeddings.model_exists", lambda p: True)
            monkeypatch.setattr("memos.storage.embeddings.download_model", lambda p: None)
            monkeypatch.setattr("builtins.input", lambda _: "")
            monkeypatch.setattr("memos.cli.dispatch._verify_components", lambda cfg, home: None)
            monkeypatch.setattr("memos.cli.dispatch._configure_llm_interactive", lambda cfg: None)

            from memos.cli.dispatch import cmd_init
            import argparse

            model_path = str(Path(__file__).resolve().parent.parent / "model" / "bge-large-zh-v1.5")
            args = argparse.Namespace(model_path=model_path, force=False, migrate_from=None)
            cmd_init(args)

            captured = capsys.readouterr()
            assert "Dashboard 访问 Token" in captured.out
            import json

            # v0.5.0: Token 通过 save_user 写入 users.json
            users_file = home_path / "etc" / "users.json"
            assert users_file.exists()
            users = json.loads(users_file.read_text(encoding="utf-8"))
            assert any(u["name"] == "admin" and u["token_hash"] for u in users)
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
            import memos.config.loader as _loader_mod

            _loader_mod.config = _loader_mod.MemoConfig.load()

    def test_status_shows_auth_status(self, monkeypatch, capsys):
        from memos.cli.dispatch import cmd_status
        import argparse

        args = argparse.Namespace()
        cmd_status(args)
        captured = capsys.readouterr()
        assert "认证" in captured.out
