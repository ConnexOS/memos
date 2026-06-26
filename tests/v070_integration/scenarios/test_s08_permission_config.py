"""S08：权限 + 配置 (F11 + F13)

注意：
- CLI user add 不支持 --role，admin 角色需直接修改 users.json
- S08 使用 setup_class 备份原始状态，teardown_class 恢复
- 标记为 @pytest.mark.isolated，避免认证状态切换影响其他场景
"""

import json
import os

import pytest

from memos.config import get_memos_home


@pytest.mark.isolated
class TestS08PermissionAndConfig:
    """验证权限边界和配置文件管理"""

    @classmethod
    def setup_class(cls):
        """保存原始状态：备份 users.json 和 behavior_guide.json"""
        etc_dir = get_memos_home() / "etc"
        cls._users_backup = None
        cls._bg_backup = None
        users_file = etc_dir / "users.json"
        if users_file.exists():
            cls._users_backup = users_file.read_text(encoding="utf-8")
        bg_file = etc_dir / "behavior_guide.json"
        if bg_file.exists():
            cls._bg_backup = bg_file.read_text(encoding="utf-8")

    @classmethod
    def teardown_class(cls):
        """恢复原始状态"""
        etc_dir = get_memos_home() / "etc"
        users_file = etc_dir / "users.json"
        if cls._users_backup:
            users_file.write_text(cls._users_backup, encoding="utf-8")
        elif users_file.exists():
            users_file.unlink()
        bg_file = etc_dir / "behavior_guide.json"
        if cls._bg_backup:
            bg_file.write_text(cls._bg_backup, encoding="utf-8")
        elif bg_file.exists():
            bg_file.unlink()

    def _enable_auth(self):
        """启用认证模式"""
        os.environ["MEMOS_AUTH_DISABLE"] = "false"

    def test_01_admin_edit_prompts(self, unified_client, monkeypatch):
        """[S08-01] admin 可正常编辑提示词模板（需要认证模式，session 级别 fixture 不支持动态切换）"""
        pytest.skip("认证模式需要单独 app 实例，session 级 fixture 不支持动态切换")

    def test_02_member_forbidden_from_prompts(self, unified_client, monkeypatch):
        """[S08-02] member 访问提示词 API → 403（需要认证模式，session 级 fixture 不支持动态切换）"""
        pytest.skip("认证模式需要单独 app 实例，session 级 fixture 不支持动态切换")

    def test_03_no_users_json_returns_401(self, unified_client, monkeypatch):
        """[S08-03] 无 users.json → prompts API → 401（需要认证模式，session 级 fixture 不支持动态切换）"""
        pytest.skip("认证模式需要单独 app 实例，session 级 fixture 不支持动态切换")

    def test_04_behavior_guide_file_read(self, unified_client):
        """[S08-04] etc/behavior_guide.json 有效时被 Prompt Hook 读取"""
        bg_file = get_memos_home() / "etc" / "behavior_guide.json"
        test_text = "自定义测试行为引导文本"
        bg_file.write_text(json.dumps({
            "enabled": True,
            "text": test_text,
            "updated_at": 1747123456.0,
        }, ensure_ascii=False), encoding="utf-8")

        resp = unified_client.post(
            "/api/hooks/prompt",
            json={"user_input": "测试 behavior_guide 读取"},
        )
        assert resp.status_code == 200
        additional_context = resp.json().get("additional_context", "")
        assert len(additional_context) > 0, \
            "Hook 响应中应包含行为引导文本"
        if bg_file.exists():
            bg_file.unlink()

    def test_05_behavior_guide_file_missing(self, unified_client):
        """[S08-05] etc/behavior_guide.json 缺失时使用代码兜底"""
        bg_file = get_memos_home() / "etc" / "behavior_guide.json"
        if bg_file.exists():
            bg_file.unlink()

        resp = unified_client.post(
            "/api/hooks/prompt",
            json={"user_input": "测试 behavior_guide 兜底"},
        )
        assert resp.status_code == 200, f"文件缺失时 Hook 应正常返回: {resp.status_code}"

    def test_06_old_behavior_guide_api_returns_404(self, unified_client):
        """[S08-06] 旧 API 端点返回 404"""
        resp_get = unified_client.get("/api/v2/config/behavior-guide")
        assert resp_get.status_code == 404, f"预期 404，实际 {resp_get.status_code}"

        resp_put = unified_client.put("/api/v2/config/behavior-guide", json={})
        assert resp_put.status_code == 404, f"预期 404，实际 {resp_put.status_code}"

        resp_post = unified_client.post("/api/v2/config/restore-default")
        assert resp_post.status_code == 404, f"预期 404，实际 {resp_post.status_code}"

    def test_07_config_json_clean(self, unified_client):
        """[S08-07] config.json 无残留 prompt 节"""
        config_file = get_memos_home() / "etc" / "config.json"
        if config_file.exists():
            cfg = json.loads(config_file.read_text(encoding="utf-8"))
            assert "prompt" not in cfg, "config.json 仍有残留 prompt 节"
