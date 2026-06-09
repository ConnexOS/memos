"""测试 memos setup 命令 — v0.5.1 参数语义"""

import json
from pathlib import Path

from memos.cli.setup import cmd_setup


class Args:
    server: str = "192.168.1.100:8000"  # 新格式：省略 http://
    token: str = "memo_test1234567890abcdef"
    project: str | None = None  # 新语义：目录路径，None=默认CWD
    name: str | None = None  # 新参数


def _redirect_credentials(monkeypatch, tmp_path):
    """重定向 credentials 写入路径，避免污染真实 ~/.memos/"""
    fake_etc = tmp_path / ".memos" / "etc"
    from memos.hook_proxy import auth
    monkeypatch.setattr(auth, "_CREDENTIALS_DIR", fake_etc)
    monkeypatch.setattr(auth, "_CREDENTIALS_FILE", fake_etc / "credentials.json")
    monkeypatch.setattr("os.chmod", lambda p, m: None)


class TestSetupBasic:
    """memos setup 基础功能测试"""

    def test_setup_creates_memos_project(self, tmp_path, monkeypatch):
        """memos setup 创建 .memos-project JSON 文件"""
        _redirect_credentials(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        args = Args()
        cmd_setup(args)

        proj_file = tmp_path / ".memos-project"
        assert proj_file.exists()
        data = json.loads(proj_file.read_text(encoding="utf-8"))
        assert "id" in data
        assert len(data["id"]) == 8

    def test_setup_creates_mcp_json(self, tmp_path, monkeypatch):
        """memos setup 创建 .mcp.json 含 token 和 name"""
        _redirect_credentials(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        args = Args()
        args.name = "MyApp"
        cmd_setup(args)

        mcp_file = tmp_path / ".mcp.json"
        assert mcp_file.exists()
        mcp = json.loads(mcp_file.read_text(encoding="utf-8"))
        url = mcp["mcpServers"]["memos"]["url"]
        assert "token=memo_test1234567890abcdef" in url
        assert "name=MyApp" in url
        assert mcp["mcpServers"]["memos"]["type"] == "sse"

    def test_setup_saves_credentials(self, tmp_path, monkeypatch):
        """memos setup 保存凭据到目标目录 .claude/memos-credentials.json"""
        _redirect_credentials(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        args = Args()
        cmd_setup(args)

        cred_file = tmp_path / ".claude" / "memos-credentials.json"
        assert cred_file.exists(), f"凭据文件不存在: {cred_file}"
        creds = json.loads(cred_file.read_text(encoding="utf-8"))
        assert creds["server_url"] == "http://192.168.1.100:8000"
        assert creds["token"] == "memo_test1234567890abcdef"


class TestServerNormalization:
    """--server 归一化测试"""

    def test_server_without_prefix_gets_http(self, tmp_path, monkeypatch):
        """无前缀的 host:port 自动补 http://"""
        _redirect_credentials(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        args = Args()
        args.server = "10.0.0.1:8000"
        args.name = "Test"
        cmd_setup(args)

        mcp = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        url = mcp["mcpServers"]["memos"]["url"]
        assert url.startswith("http://10.0.0.1:8000")

    def test_server_with_http_prefix_unchanged(self, tmp_path, monkeypatch):
        """带 http:// 前缀的不重复添加"""
        _redirect_credentials(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        args = Args()
        args.server = "http://192.168.1.1:8000"
        args.name = "Test"
        cmd_setup(args)

        mcp = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        url = mcp["mcpServers"]["memos"]["url"]
        assert url.startswith("http://")

    def test_server_with_https_prefix_unchanged(self, tmp_path, monkeypatch):
        """带 https:// 前缀的不重复添加"""
        _redirect_credentials(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        args = Args()
        args.server = "https://memos.example.com"
        args.name = "Test"
        cmd_setup(args)

        mcp = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        url = mcp["mcpServers"]["memos"]["url"]
        assert url.startswith("https://")


class TestProjectAndName:
    """--project 和 --name 参数语义测试"""

    def test_project_specifies_target_dir(self, tmp_path, monkeypatch):
        """--project 指定目标目录，配置写入该目录"""
        _redirect_credentials(monkeypatch, tmp_path)
        target = tmp_path / "myproject"
        target.mkdir()
        work_dir = tmp_path / "workspace"
        work_dir.mkdir()
        monkeypatch.chdir(work_dir)

        args = Args()
        args.project = str(target)
        args.name = "MyProj"
        cmd_setup(args)

        assert (target / ".memos-project").exists()
        assert (target / ".mcp.json").exists()
        assert not (work_dir / ".memos-project").exists()

    def test_name_defaults_to_dirname(self, tmp_path, monkeypatch):
        """--name 省略时默认取 --project 目录名"""
        _redirect_credentials(monkeypatch, tmp_path)
        target = tmp_path / "AwesomeApp"
        target.mkdir()
        monkeypatch.chdir(tmp_path)

        args = Args()
        args.project = str(target)
        cmd_setup(args)

        data = json.loads((target / ".memos-project").read_text(encoding="utf-8"))
        assert data["name"] == "AwesomeApp"

    def test_name_overrides_dirname(self, tmp_path, monkeypatch):
        """--name 指定时覆盖目录名"""
        _redirect_credentials(monkeypatch, tmp_path)
        target = tmp_path / "some-dir"
        target.mkdir()
        monkeypatch.chdir(tmp_path)

        args = Args()
        args.project = str(target)
        args.name = "CustomName"
        cmd_setup(args)

        data = json.loads((target / ".memos-project").read_text(encoding="utf-8"))
        assert data["name"] == "CustomName"

    def test_overwrites_existing_config(self, tmp_path, monkeypatch):
        """重复执行 setup 覆盖已有配置"""
        _redirect_credentials(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)

        args1 = Args()
        args1.name = "OldName"
        cmd_setup(args1)
        old_data = json.loads((tmp_path / ".memos-project").read_text(encoding="utf-8"))
        assert old_data["name"] == "OldName"

        args2 = Args()
        args2.name = "NewName"
        cmd_setup(args2)
        new_data = json.loads((tmp_path / ".memos-project").read_text(encoding="utf-8"))
        assert new_data["name"] == "NewName"
