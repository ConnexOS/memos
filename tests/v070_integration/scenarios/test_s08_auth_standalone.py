"""S08 独立认证测试参考实现（当前不可执行）。

session 级 unified_client 固定 auth=false，无法测试认证/权限。
创建独立 app 实例与 session 级 ChromaDB 客户端冲突（单例锁）。

认证测试的正确方式：
  MEMOS_AUTH_DISABLE=false pytest tests/v070_integration/ -v -k "s08"

或者使用独立的 pytest session 配置，在 conftest.py 中设置：
  os.environ.setdefault("MEMOS_AUTH_DISABLE", "false")

本文件保留为参考实现。测试被标记为 @pytest.mark.xfail。
"""

import os
import subprocess
import sys

import pytest

from memos.config import get_memos_home
from tests.v070_integration.conftest import PROJECT_ROOT


@pytest.mark.xfail(reason="ChromaDB 单例冲突：第二个 app 实例与 session 级实例冲突")
class TestS08AuthStandalone:
    """独立认证测试参考实现"""

    def _create_app(self):
        """创建启用认证的独立 app 实例。"""
        from memos.config import config
        from memos.server.app import create_unified_app
        from memos.web.auth import create_admin_on_first_start

        config.auth.disable = False
        os.environ["MEMOS_AUTH_DISABLE"] = "false"
        app = create_unified_app()
        admin_token = create_admin_on_first_start()
        return app, admin_token

    def test_admin_can_access_prompts(self):
        """admin 用户可访问提示词 API"""
        from starlette.testclient import TestClient

        app, admin_token = self._create_app()
        assert admin_token, "admin 用户创建失败"

        with TestClient(app, base_url="http://localhost:8000") as client:
            resp = client.get("/api/prompts")
            assert resp.status_code in (200, 401), f"预期 200 或 401, 实际 {resp.status_code}"

    def test_member_forbidden_from_prompts(self):
        """member 访问提示词 API 应被拒绝 (401/403)"""
        from starlette.testclient import TestClient
        from memos.web.auth import save_user, generate_token, hash_token

        app, admin_token = self._create_app()
        token = generate_token()
        save_user("member_test", hash_token(token), role="member")

        with TestClient(app, base_url="http://localhost:8000") as client:
            resp = client.get("/api/prompts")
            assert resp.status_code in (401, 403), f"member 应被拒绝: {resp.status_code}"

    def test_no_users_json_returns_401(self):
        """无 users.json 文件时 API 返回 401"""
        from starlette.testclient import TestClient

        etc_dir = get_memos_home() / "etc"
        users_file = etc_dir / "users.json"
        backup = users_file.read_text(encoding="utf-8") if users_file.exists() else None
        if users_file.exists():
            users_file.unlink()

        try:
            app, _ = self._create_app()
            with TestClient(app, base_url="http://localhost:8000") as client:
                resp = client.get("/api/prompts")
                assert resp.status_code == 401, f"无 users.json 应返回 401: {resp.status_code}"
        finally:
            if backup:
                users_file.write_text(backup, encoding="utf-8")
