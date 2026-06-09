# tests/test_cli_mcp_install.py

"""测试 memos mcp install 命令"""

import json
from pathlib import Path


def _prepare_project(tmp_path: Path, project_name: str = "TestProj"):
    """创建 .memos-project 和虚假凭据"""
    import hashlib

    pid = hashlib.md5(project_name.encode()).hexdigest()[:8]
    proj_file = tmp_path / ".memos-project"
    proj_file.write_text(json.dumps({"id": pid, "name": project_name}), encoding="utf-8")


def _run_install(monkeypatch, tmp_path: Path, server_arg: str = None):
    """在 tmp_path 中模拟运行 mcp install"""
    from memos.cli.dispatch import cmd_mcp_install

    class Args:
        mcp_action = "install"
        server = server_arg  # None 表示走默认值

    _prepare_project(tmp_path)
    monkeypatch.chdir(str(tmp_path))
    cmd_mcp_install(Args())


class TestMcpInstall:
    """测试 mcp install 命令"""

    def test_generates_mcp_json(self, monkeypatch, tmp_path):
        """正常生成 .mcp.json"""
        _run_install(monkeypatch, tmp_path)
        mcp_json = tmp_path / ".mcp.json"
        assert mcp_json.exists()
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
        assert "mcpServers" in data
        assert "memos" in data["mcpServers"]
        assert data["mcpServers"]["memos"]["type"] == "sse"
        url = data["mcpServers"]["memos"]["url"]
        assert url.startswith("http://")
        assert "/sse" in url

    def test_url_contains_project_id(self, monkeypatch, tmp_path):
        """URL 路径中包含 project_id 段，查询参数中包含 name"""
        _run_install(monkeypatch, tmp_path)
        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        url = data["mcpServers"]["memos"]["url"]
        path_part = url.split("?")[0].rstrip("/")
        pid = path_part.split("/")[-2]  # 倒数第二段是 project_id
        assert len(pid) == 8
        assert all(c in "0123456789abcdef" for c in pid)

    def test_url_contains_project_name(self, monkeypatch, tmp_path):
        """URL 查询参数中包含 project_name"""
        _run_install(monkeypatch, tmp_path)
        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        url = data["mcpServers"]["memos"]["url"]
        assert "?name=" in url

    def test_custom_server_arg(self, monkeypatch, tmp_path):
        """--server 参数传递 server URL"""
        from memos.cli.dispatch import cmd_mcp_install

        class Args:
            mcp_action = "install"
            server = "http://custom:9000"

        _prepare_project(tmp_path)
        monkeypatch.chdir(str(tmp_path))
        cmd_mcp_install(Args())
        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        url = data["mcpServers"]["memos"]["url"]
        assert url.startswith("http://custom:9000")

    def test_merge_preserves_other_servers(self, monkeypatch, tmp_path):
        """合并模式：保留已有的其他 MCP server 配置"""
        _prepare_project(tmp_path)
        old = {
            "mcpServers": {
                "other-tool": {"type": "stdio", "command": "other"},
                "memos": {"type": "sse", "url": "http://old/url/sse"},
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(old), encoding="utf-8")
        monkeypatch.chdir(str(tmp_path))
        from memos.cli.dispatch import cmd_mcp_install

        class Args:
            mcp_action = "install"
            server = None

        cmd_mcp_install(Args())

        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        # 其他 server 保留
        assert "other-tool" in data["mcpServers"]
        assert data["mcpServers"]["other-tool"]["command"] == "other"
        # memos URL 已更新（不包含旧 URL）
        assert "http://old/url" not in data["mcpServers"]["memos"]["url"]
