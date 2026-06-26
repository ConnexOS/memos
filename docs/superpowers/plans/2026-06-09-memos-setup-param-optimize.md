# memos setup 参数优化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 `memos setup` 参数语义：`--server` 支持省略 `http://` 前缀、`--project` 改为目标目录路径、新增 `--name` 指定项目名、全部覆盖写入 target_dir。

**Architecture:** 集中在 3 个文件：`dispatch.py`（argparse 参数定义）、`setup.py`（核心逻辑重写）、`test_setup.py`（测试适配）。不改动 `install_hooks` 签名，通过临时 chdir 到 target_dir 来复用现有 Hook 安装逻辑。

**Tech Stack:** Python 3.12, argparse, pytest

---

### Task 1: 更新 argparse 参数定义 + 旧引用提示语

**Files:**
- Modify: `src/memos/cli/dispatch.py:1383-1387`

- [ ] **Step 1: 修改 argparse setup 参数**

```python
# setup
p_setup = sub.add_parser("setup", help="一键初始化：login + mcp + hook")
p_setup.add_argument("--server", required=True, help="memos server 地址（host:port 或完整 URL，如 127.0.0.1:8000）")
p_setup.add_argument("--token", required=True, help="用户 Token（从管理员获取）")
p_setup.add_argument("--project", default=None, help="目标项目目录路径（默认当前目录）")
p_setup.add_argument("--name", default=None, help="项目显示名称（默认取 --project 目录名或当前目录名）")
```

- [ ] **Step 2: 更新 cmd_mcp_install 中的旧 setup 提示语**

在 `src/memos/cli/dispatch.py:89`，将：
```python
print("请先运行: memos setup --server <URL> --token <TOKEN> --project <项目名>", file=sys.stderr)
```
改为：
```python
print("请先运行: memos setup --server <URL> --token <TOKEN>", file=sys.stderr)
```

- [ ] **Step 3: 更新 project_id.py 中的旧 setup 提示语**

在 `src/memos/hook_proxy/project_id.py:39`，将：
```python
f"未找到 .memos-project 文件（{proj_file}），请运行 memos setup --project <项目名> 初始化"
```
改为：
```python
f"未找到 .memos-project 文件（{proj_file}），请运行 memos setup --server <URL> --token <TOKEN> --project <项目目录> 初始化"
```

- [ ] **Step 4: 运行现有测试确认无回归**

```bash
.\venv\Scripts\python -m pytest tests/test_unified/test_setup.py -v
```

（预期：setup.py 未改，测试仍通过，但 argparse 变更可能导致旧参数验证逻辑不匹配，记下失败以便下一步修复）

- [ ] **Step 5: Commit**

```bash
git add src/memos/cli/dispatch.py src/memos/hook_proxy/project_id.py
git commit -m "feat(setup): 更新 argparse 参数定义和引用提示语

- --server 帮助文本支持 host:port 格式
- --project 改为可选的目标项目目录路径
- 新增 --name 参数
- 更新引用旧 setup 语法的错误提示

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 重写 cmd_setup 核心逻辑

**Files:**
- Modify: `src/memos/cli/setup.py`（全量重写）

- [ ] **Step 1: 重写 setup.py**

```python
"""memos setup — 一键初始化命令"""

import hashlib
import json
import logging
from pathlib import Path
from urllib.parse import quote

from ..hook_proxy.auth import save_credentials
from ..hook_proxy.project_id import clear_project_id_cache

logger = logging.getLogger(__name__)


def _normalize_server(server: str) -> str:
    """归一化 server 地址：无 :// 前缀时补 http://"""
    server = server.rstrip("/")
    if "://" not in server:
        server = f"http://{server}"
    return server


def cmd_setup(args):
    """一键初始化：创建 .memos-project + 保存凭据 + 生成 .mcp.json + 安装 Hook

    所有配置写入 --project 指定的目录（默认当前目录），全部覆盖。
    """
    # Step 1: 解析参数
    project_dir_str = args.project if hasattr(args, "project") and args.project else None
    target_dir = Path(project_dir_str).resolve() if project_dir_str else Path.cwd()
    project_name = args.name if hasattr(args, "name") and args.name else target_dir.name
    project_id = hashlib.md5(project_name.encode()).hexdigest()[:8]
    server_url = _normalize_server(args.server)
    token = args.token

    # 确保目标目录存在
    target_dir.mkdir(parents=True, exist_ok=True)

    # Step 2: 写入 .memos-project（覆盖）
    proj_file = target_dir / ".memos-project"
    proj_file.write_text(
        json.dumps({"id": project_id, "name": project_name}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    clear_project_id_cache(str(target_dir))
    print(f"[OK] .memos-project 已创建: id={project_id} name={project_name}")

    # Step 3: 保存凭据到目标目录（覆盖）
    save_credentials_to(server_url, token, str(target_dir))
    print(f"[OK] 凭据已保存: {server_url}")

    # Step 4: 生成 .mcp.json（覆盖）
    mcp_config = {
        "mcpServers": {
            "memos": {
                "type": "sse",
                "url": f"{server_url}/mcp/{project_id}/sse?name={quote(project_name)}&token={token}",
            }
        }
    }
    mcp_json_path = target_dir / ".mcp.json"
    mcp_json_path.write_text(json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] .mcp.json 已生成")

    # Step 5: 安装 Hook（覆盖 memos 相关项，保留其他配置）
    import os as _os
    from .dispatch import install_hooks

    _orig_cwd = _os.getcwd()
    try:
        _os.chdir(str(target_dir))
        install_hooks(global_mode=False)
    finally:
        _os.chdir(_orig_cwd)
    print("[OK] Hook 已安装到项目 settings.json")
    print()
    print("提示: 重新加载 Claude Code 后生效")


def save_credentials_to(server_url: str, token: str, target_dir: str):
    """将凭据直接写入指定目录的 .claude/memos-credentials.json（覆盖）。"""
    cred_dir = Path(target_dir) / ".claude"
    cred_file = cred_dir / "memos-credentials.json"
    cred_dir.mkdir(parents=True, exist_ok=True)
    data = {"server_url": server_url, "token": token}
    with open(cred_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
```

注意：`save_credentials_to` 是独立的函数，不依赖 `save_credentials` 的 `.memos-project` 检测逻辑（因为 `save_credentials` 通过 `_get_project_credentials_path` 检测 `.memos-project`，而我们已经确保 `.memos-project` 存在）。但为了代码干净，直接在 setup.py 中内联写入。

**或者更简洁：直接复用 `save_credentials`。** 由于我们已经在 Step 2 写入了 `.memos-project`，在 Step 5 chdir 后 `save_credentials` 的 `_get_project_credentials_path` 检测逻辑可以正常工作。但 `save_credentials` 在写入前不一定会检测 target_dir 的 `.memos-project`（它检测的是 CWD）。所以在 Step 3 调用前也需要 chdir 或直接内联。

**推荐方案：全部 Step 在 chdir 后执行，简化代码。**

- [ ] **Step 2: 最终简化版 setup.py**

```python
"""memos setup — 一键初始化命令"""

import hashlib
import json
import logging
import os as _os
from pathlib import Path
from urllib.parse import quote

from ..hook_proxy.auth import save_credentials
from ..hook_proxy.project_id import clear_project_id_cache

logger = logging.getLogger(__name__)


def _normalize_server(server: str) -> str:
    server = server.rstrip("/")
    if "://" not in server:
        server = f"http://{server}"
    return server


def cmd_setup(args):
    """一键初始化：创建 .memos-project + 保存凭据 + 生成 .mcp.json + 安装 Hook

    所有配置写入 --project 指定的目录（默认当前目录），全部覆盖。
    """
    # Step 1: 解析参数
    project_dir_str = args.project if hasattr(args, "project") and args.project else None
    target_dir = Path(project_dir_str).resolve() if project_dir_str else Path.cwd()
    target_dir.mkdir(parents=True, exist_ok=True)
    project_name = args.name if hasattr(args, "name") and args.name else target_dir.name
    project_id = hashlib.md5(project_name.encode()).hexdigest()[:8]
    server_url = _normalize_server(args.server)
    token = args.token

    # 切换到目标目录，后续所有写入基于 CWD
    _orig_cwd = _os.getcwd()
    try:
        _os.chdir(str(target_dir))

        # Step 2: 写入 .memos-project（覆盖）
        proj_file = Path(".memos-project")
        proj_file.write_text(
            json.dumps({"id": project_id, "name": project_name}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        clear_project_id_cache(str(target_dir))
        print(f"[OK] .memos-project 已创建: id={project_id} name={project_name}")

        # Step 3: 保存凭据（覆盖）
        save_credentials(server_url, token)
        print(f"[OK] 凭据已保存: {server_url}")

        # Step 4: 生成 .mcp.json（覆盖）
        mcp_config = {
            "mcpServers": {
                "memos": {
                    "type": "sse",
                    "url": f"{server_url}/mcp/{project_id}/sse?name={quote(project_name)}&token={token}",
                }
            }
        }
        mcp_json_path = Path(".mcp.json")
        mcp_json_path.write_text(json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] .mcp.json 已生成")

        # Step 5: 安装 Hook（覆盖 memos 相关项，保留 settings.json 其他配置）
        from .dispatch import install_hooks

        install_hooks(global_mode=False)
        print("[OK] Hook 已安装到项目 settings.json")
    finally:
        _os.chdir(_orig_cwd)

    print()
    print("提示: 重新加载 Claude Code 后生效")
```

- [ ] **Step 3: 运行测试**

```bash
.\venv\Scripts\python -m pytest tests/test_unified/test_setup.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/memos/cli/setup.py
git commit -m "feat(setup): 重写 cmd_setup 支持 --project 目录路径 + --name + --server 简化格式

- --server 无 :// 前缀时自动补 http://
- --project 改为目标项目目录路径，默认 CWD
- --name 指定项目显示名称，默认取目录名
- 所有配置写入 target_dir，全部覆盖
- 通过 chdir 复用现有 save_credentials 和 install_hooks 逻辑

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: 适配测试用例

**Files:**
- Modify: `tests/test_unified/test_setup.py`

当前测试的结构：
- `Args` 类：`server="http://...", project="TestProject"` — 旧语义
- `test_setup_creates_memos_project`: `--project` 传项目名，验证 `.memos-project` 内容
- `test_setup_creates_mcp_json`: 验证 URL 含 token 和 name
- `test_setup_saves_credentials`: 验证凭据文件

需适配：
1. `--project` 不再传项目名，改为传目录路径（用 tmp_path）
2. `--server` 可测试无前缀格式
3. 新增 `--name` 的测试

- [ ] **Step 1: 重写测试文件**

```python
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
        # 不设 --project 和 --name，使用默认值（CWD 目录名）
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
        # 在另一个目录执行
        work_dir = tmp_path / "workspace"
        work_dir.mkdir()
        monkeypatch.chdir(work_dir)

        args = Args()
        args.project = str(target)
        args.name = "MyProj"
        cmd_setup(args)

        # 配置应该写入 target 而非 CWD
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
        # 不设 --name
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
        assert data["id"] != "CustomName"  # id 基于 name 的 hash

    def test_overwrites_existing_config(self, tmp_path, monkeypatch):
        """重复执行 setup 覆盖已有配置"""
        _redirect_credentials(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)

        # 第一次执行
        args1 = Args()
        args1.name = "OldName"
        cmd_setup(args1)
        old_data = json.loads((tmp_path / ".memos-project").read_text(encoding="utf-8"))
        assert old_data["name"] == "OldName"

        # 第二次执行，覆盖
        args2 = Args()
        args2.name = "NewName"
        cmd_setup(args2)
        new_data = json.loads((tmp_path / ".memos-project").read_text(encoding="utf-8"))
        assert new_data["name"] == "NewName"
```

- [ ] **Step 2: 运行测试验证全部通过**

```bash
.\venv\Scripts\python -m pytest tests/test_unified/test_setup.py -v
```

预期：11 个测试全部 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_unified/test_setup.py
git commit -m "test(setup): 适配 --project 目录路径 + --name + --server 无前缀语义

- 覆盖参数默认值、server 归一化、project/name 组合逻辑
- 新增覆盖写入验证测试
- 共 11 个测试用例

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: 运行全量回归

- [ ] **Step 1: 运行完整测试套件**

```bash
.\venv\Scripts\python -m pytest tests/ -v -k "not real"
```

预期：全部 PASS（现有的 52+ 测试不受影响）

- [ ] **Step 2: 如有失败，修复后再 commit**

---

### 文件变更总览

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/memos/cli/dispatch.py` | 修改 | argparse 参数定义 + `cmd_mcp_install` 提示语 |
| `src/memos/cli/setup.py` | 重写 | 核心逻辑：参数解析 + 归一化 + 全量覆盖写入 |
| `src/memos/hook_proxy/project_id.py` | 修改 | 错误提示语更新 |
| `tests/test_unified/test_setup.py` | 重写 | 适配新参数语义，11 个测试用例 |
