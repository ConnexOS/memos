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
    verify_session_token,
)


class TestAuthUtils:
    """auth.py 工具函数"""

    def test_hash_token_is_deterministic(self):
        token = "my-test-token"
        assert hash_token(token) == hash_token(token)

    def test_hash_token_different_tokens_different_hash(self):
        assert hash_token("token-a") != hash_token("token-b")

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
        # 篡改最后几个字符
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
        assert len(t1) == 64  # 32 bytes hex

    def test_generate_secret_key_is_random(self):
        s1 = generate_secret_key()
        s2 = generate_secret_key()
        assert s1 != s2
        assert len(s1) == 128  # 64 bytes hex


class TestDashboardAuth:
    """Dashboard 登录 API 和中间件"""

    @pytest.fixture
    def auth_client(self):
        token = "b945ce0a170d757c1cd2f2fd4b0f81f9e6d6dd8ee2bf0aa5ee429496e9a9502d"  # generate_token()
        token_hash = "1854b7a4fac5df25d327ed8d6b0c193de8e46bbd9583ac16a401c0aa15a51f21"  # hash_token(token)
        secret = "e7227707a298bb548f65ddb49616c6709c052813009652f58d38fdbb99f2b369067caab40e8137e34bd078f8058bb30d5c3062d44e381742e8427f7092eea81f"  # generate_secret_key()

        with (
            patch("memos.config.config.auth.token_hash", token_hash),
            patch("memos.config.config.auth.secret_key", secret),
            patch("memos.config.config.auth.disable", False),
            patch("memos.config.loader.config.auth.token_hash", token_hash),
            patch("memos.config.loader.config.auth.secret_key", secret),
            patch("memos.config.loader.config.auth.disable", False),
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
        assert data["message"] == "登录成功"
        assert "memos_session" in resp.cookies

    def test_login_fails_with_wrong_token(self, auth_client):
        c, *_ = auth_client
        resp = c.post("/api/auth/login", json={"token": "wrong-token"})
        assert resp.status_code == 401
        assert "memos_session" not in resp.cookies

    def test_login_fails_with_empty_token(self, auth_client):
        c, *_ = auth_client
        resp = c.post("/api/auth/login", json={"token": ""})
        assert resp.status_code == 422  # Pydantic 校验：min_length=1

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
        assert resp.status_code == 307  # RedirectResponse default

    def test_index_shows_dashboard_with_auth(self, auth_client):
        c, _, token_hash, secret = auth_client
        session = create_session_token(token_hash, secret, 3600)
        c.cookies.set("memos_session", session)
        resp = c.get("/")
        assert resp.status_code == 200
        assert "仪表板" in resp.text or "MEMOS" in resp.text

    def test_login_shows_error_for_unconfigured_auth(self, monkeypatch):
        """服务端未配置认证时登录返回 500"""
        import sys

        monkeypatch.setattr(sys.modules["memos.web.app"], "ContextMemory", MagicMock)
        with (
            patch("memos.config.config.auth.token_hash", ""),
            patch("memos.config.config.auth.secret_key", ""),
            patch("memos.config.loader.config.auth.token_hash", ""),
            patch("memos.config.loader.config.auth.secret_key", ""),
        ):
            from memos.web.app import app

            with TestClient(app) as c:
                resp = c.post("/api/auth/login", json={"token": "anything"})
                assert resp.status_code == 400


class TestCLIAuth:
    """CLI init 中认证 Token 生成"""

    def test_init_generates_token_on_first_run(self, monkeypatch, capsys):
        tmp = tempfile.mkdtemp(prefix="memos-auth-test-")
        try:
            home_path = Path(tmp) / ".memos"
            monkeypatch.setenv("MEMOS_HOME", str(home_path))
            # 跳过所有耗时操作
            monkeypatch.setattr("memos.storage.embeddings.model_exists", lambda p: True)
            monkeypatch.setattr("memos.storage.embeddings.download_model", lambda p: None)
            monkeypatch.setattr("builtins.input", lambda _: "")
            monkeypatch.setattr("memos.cli.dispatch._verify_components", lambda cfg, home: None)
            monkeypatch.setattr("memos.cli.dispatch._configure_llm_interactive", lambda cfg: None)

            from memos.cli.dispatch import cmd_init
            import argparse

            # 使用 --model-path 跳过模型下载判断，指定已有模型路径
            model_path = str(Path(__file__).resolve().parent.parent / "model" / "bge-large-zh-v1.5")
            args = argparse.Namespace(model_path=model_path, force=False, migrate_from=None)
            cmd_init(args)

            captured = capsys.readouterr()
            # 应显示 Token（首次生成）
            assert "Dashboard 访问 Token" in captured.out
            # 配置应含 auth
            import json

            config_file = home_path / "etc" / "config.json"
            data = json.loads(config_file.read_text(encoding="utf-8"))
            assert "auth" in data
            assert data["auth"]["token_hash"]
            assert data["auth"]["secret_key"]
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
            # 恢复全局 config 单例，避免环境变量残留污染后续测试
            import memos.config.loader as _loader_mod

            _loader_mod.config = _loader_mod.MemoConfig.load()

    def test_status_shows_auth_status(self, monkeypatch, capsys):
        """memos status 显示认证状态"""
        from memos.cli.dispatch import cmd_status
        import argparse

        args = argparse.Namespace()
        cmd_status(args)
        captured = capsys.readouterr()
        assert "认证" in captured.out
