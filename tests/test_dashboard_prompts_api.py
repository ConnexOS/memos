"""Phase 3: Dashboard 提示词管理 API 测试"""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from memos.web.app import app
from memos.config import MemoConfig


@pytest.fixture
def api_client(monkeypatch):
    """隔离环境 + TestClient + Auth mock（修复 auth mock 污染导致的 401）"""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        os.environ["MEMOS_HOME"] = str(home)
        (home / "etc").mkdir(parents=True)
        (home / "memdb").mkdir(parents=True)

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

        # 模拟内存系统
        mock_mem = mock.Mock()
        mock_mem.store.get.return_value = {"ids": [], "documents": [], "metadatas": []}

        # Auth mock: mock verify_session_token 绕过认证中间件
        with (
            patch("memos.web.app.verify_session_token", return_value={"token_hash": "test", "exp": 9999999999}),
        ):
            app.state.mem = mock_mem
            client = TestClient(app, cookies={"memos_session": "test-session"})
            yield client


class TestPromptTemplateAPI:
    """模板 CRUD API"""

    def test_list_templates(self, api_client):
        resp = api_client.get("/api/prompts")
        assert resp.status_code == 200
        data = resp.json()
        assert "templates" in data
        assert "endpoints" in data
        assert len(data["endpoints"]) >= 1

    def test_create_template(self, api_client):
        resp = api_client.post(
            "/api/prompts",
            json={
                "endpoint": "create-test-ep",
                "name": "我的模板",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["id"] == "create-test-ep@extract"

    def test_create_duplicate(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "dup"})
        resp = api_client.post("/api/prompts", json={"endpoint": "dup"})
        assert resp.status_code == 409

    def test_get_template_detail(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "detail-test", "name": "详情测试"})
        resp = api_client.get("/api/prompts/detail-test@extract")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "detail-test@extract"
        assert "draft" in data
        assert "versions" in data

    def test_get_nonexistent_template(self, api_client):
        resp = api_client.get("/api/prompts/nonexistent")
        assert resp.status_code == 404

    def test_update_template(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "update-test"})
        resp = api_client.put("/api/prompts/update-test@extract", json={"name": "新名称"})
        assert resp.status_code == 200
        detail = api_client.get("/api/prompts/update-test@extract").json()
        assert detail["name"] == "新名称"

    def test_delete_template(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "to-delete"})
        resp = api_client.delete("/api/prompts/to-delete@extract")
        assert resp.status_code == 200
        assert api_client.get("/api/prompts/to-delete@extract").status_code == 404

    def test_delete_default_not_allowed(self, api_client):
        resp = api_client.delete("/api/prompts/default")
        assert resp.status_code == 400


class TestDraftUpgradeAPI:
    """草稿 + 升级 API"""

    def test_save_draft(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "draft-test"})
        resp = api_client.post(
            "/api/prompts/draft-test@extract/draft",
            json={
                "system_prompt": "自定义提示词",
                "parameters": {"temperature": 0.5},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["draft"]["system_prompt"] == "自定义提示词"

    def test_save_draft_partial(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "partial"})
        resp = api_client.post(
            "/api/prompts/partial@extract/draft",
            json={
                "system_prompt": "只改提示词",
            },
        )
        assert resp.status_code == 200

    def test_upgrade(self, api_client):
        # create_prompt 已自带 v1.0.0，upgrade 同名版本会自动递增
        api_client.post("/api/prompts", json={"endpoint": "upgrade-xx"})
        api_client.post(
            "/api/prompts/upgrade-xx@extract/draft",
            json={
                "system_prompt": "v2内容",
            },
        )
        resp = api_client.post(
            "/api/prompts/upgrade-xx@extract/upgrade",
            json={
                "version": "1.0.0",
                "changelog": "首个版本",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "1.0.1"
        assert data["version_count"] == 2

    def test_get_version_content(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "ver-test"})
        api_client.post(
            "/api/prompts/ver-test@extract/upgrade",
            json={
                "version": "1.0.0",
                "changelog": "升级",
            },
        )
        resp = api_client.get("/api/prompts/ver-test@extract/versions/1.0.0")
        assert resp.status_code == 200
        assert "system_prompt" in resp.json()

    def test_version_not_found(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "ver-nf"})
        resp = api_client.get("/api/prompts/ver-nf@extract/versions/99.99.99")
        assert resp.status_code == 404


class TestRollbackDiffAPI:
    """回滚 + Diff API"""

    def test_rollback(self, api_client):
        # create_prompt 已自带 v1.0.0，后续 upgrade("1.0.0") 会自动递增
        api_client.post("/api/prompts", json={"endpoint": "rb-x1"})
        # 第一次 upgrade → 1.0.0 已存在，自动递增为 1.0.1
        api_client.post("/api/prompts/rb-x1@extract/upgrade", json={"version": "1.0.0", "changelog": "v1"})
        api_client.post("/api/prompts/rb-x1@extract/draft", json={"system_prompt": "v2"})
        api_client.post("/api/prompts/rb-x1@extract/upgrade", json={"version": "2.0.0", "changelog": "v2"})
        resp = api_client.post("/api/prompts/rb-x1@extract/rollback/1.0.0", json={"changelog": "回滚"})
        assert resp.status_code == 200
        data = resp.json()
        # 版本: 1.0.0(自带) + 1.0.1(递增) + 2.0.0 + 1.0.2(回滚递增) = 4
        assert data["version_count"] == 4
        assert "1.0.2" in data["version"]

    def test_rollback_nonexistent(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "rb-nf-x"})
        resp = api_client.post("/api/prompts/rb-nf-x@extract/rollback/99.99.99")
        assert resp.status_code == 404

    def test_diff(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "diff-x1"})
        api_client.post("/api/prompts/diff-x1@extract/upgrade", json={"version": "1.0.0", "changelog": "v1"})
        api_client.post("/api/prompts/diff-x1@extract/draft", json={"system_prompt": "新版本内容"})
        api_client.post("/api/prompts/diff-x1@extract/upgrade", json={"version": "1.1.0", "changelog": "改了"})
        resp = api_client.get("/api/prompts/diff-x1@extract/diff?v1=1.0.0&v2=1.1.0")
        assert resp.status_code == 200
        data = resp.json()
        assert "diff" in data
        assert data["v1"]["version"] == "1.0.0"
        assert data["v2"]["version"] == "1.1.0"

    def test_activate_version(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "av-x1"})
        api_client.post("/api/prompts/av-x1@extract/upgrade", json={"version": "1.0.0", "changelog": "v1.0"})
        api_client.post("/api/prompts/av-x1@extract/upgrade", json={"version": "1.1.0", "changelog": "v1.1"})
        resp = api_client.post("/api/prompts/av-x1@extract/activate-version/1.0.0")
        assert resp.status_code == 200
        detail = api_client.get("/api/prompts/av-x1@extract").json()
        assert detail["active_version"] == "1.0.0"


class TestEndpointQueryAPI:
    """端点查询 API"""

    def test_for_endpoint_found(self, api_client):
        api_client.post("/api/prompts", json={"endpoint": "deepseek-ai", "name": "DeepSeek模板"})
        resp = api_client.get("/api/prompts/for-endpoint/deepseek-ai")
        assert resp.status_code == 200
        assert resp.json()["template"] is not None
        assert resp.json()["template"]["id"] in ("deepseek-ai@extract", "deepseek-ai")

    def test_for_endpoint_fallback_to_default(self, api_client):
        resp = api_client.get("/api/prompts/for-endpoint/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["template"] is not None
        # fallback 到同类型的默认模板（extract → default@extract）
        assert data["template"]["id"] in ("fallback", "fallback@extract", "default@extract")
        # is_fallback 仅当返回 fallback@* 或 fallback 模板时为 True
        if data["template"]["id"] in ("fallback", "fallback@extract"):
            assert data["template"]["is_fallback"] is True
        else:
            # default@extract 不是 fallback，是系统推荐的默认模板
            pass
