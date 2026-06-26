# SSE 令牌传递 + 客户端拆分 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 SSE MCP 令牌传递（SessionAuthStore）+ 包轻量化 + `memos setup` 一键命令 + 项目 ID 解析简化

**Architecture:** 客户端通过 SSE URL query string 携带令牌，服务端 `SessionAuthStore` 建立 session_id→token 内存映射，后续消息请求按 session_id 查令牌注入 `_auth_token_ctx`。包依赖拆分实现 client-only 轻量安装。

**Tech Stack:** Python 3.12, Starlette ASGI, contextvars, threading, hashlib, json

**设计文档:** `docs/superpowers/specs/2026-06-08-sse-auth-client-split-design.md`

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|:--:|------|
| `pyproject.toml` | 修改 | 拆分依赖为默认(轻量) + `[server]` extras |
| `src/memos/__init__.py` | 修改 | 清理顶层重依赖导入 |
| `src/memos/hook_proxy/project_id.py` | 重写 | `.memos-project` JSON，取消三条兜底链 |
| `src/memos/cli/setup.py` | **新增** | `memos setup` 一键配置命令 |
| `src/memos/cli/dispatch.py` | 修改 | wiring setup 命令 + mcp_install 含 token |
| `src/memos/server/mcp.py` | 修改 | 新增 `SessionAuthStore`，导出 `_session_auth_store` |
| `src/memos/server/sse_wrapper.py` | 修改 | 提取 token + 拦截 endpoint + messages 注入 |

---

### Task 1: 清理 `memos/__init__.py` — 移除顶层重导入

**文件:**
- 修改: `src/memos/__init__.py`

- [ ] **Step 1: 替换文件内容**

当前文件从 `memos.engine.memory` 和 `memos.engine.extractor` 导入重型模块，导致 `import memos` 触发 ChromaDB/torch 依赖链。改为仅导入 `__version__`。

```python
from memos._version import __version__


def __getattr__(name: str):
    """惰性加载 server 模块，避免未安装 pywin32 时导入失败。"""
    if name == "mcp":
        from memos.server.mcp import mcp as _mcp

        return _mcp
    if name == "_detect_project_id":
        import warnings
        warnings.warn(
            "_detect_project_id 已废弃，仅作 SSE 连接前的 project_id 兜底，"
            "请使用 resolve_project_id() 从 .memos-project 读取",
            DeprecationWarning,
            stacklevel=2,
        )
        from memos.server.mcp import _detect_project_id as _fn

        return _fn
    raise AttributeError(f"module 'memos' has no attribute {name!r}")


__all__ = [
    "__version__",
    "mcp",
    "_detect_project_id",
]
```

- [ ] **Step 2: 验证轻量导入**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -c "import memos; print(memos.__version__)" 2>&1
```
预期: 输出版本号（如 `0.4.9`），不再触发 ChromaDB 导入。

- [ ] **Step 3: 验证 lazy loader 仍可用**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -c "from memos import mcp; print(type(mcp))" 2>&1
```
预期: `<class 'mcp.server.fastmcp.server.FastMCP'>`

- [ ] **Step 4: Commit**

```bash
git add src/memos/__init__.py
git commit -m "refactor: 清理 __init__.py 顶层重导入，仅保留 __version__"
```

---

### Task 2: 简化 `project_id.py` — `.memos-project` JSON 单一来源

**文件:**
- 修改: `src/memos/hook_proxy/project_id.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_unified/test_project_id.py 追加

import json
from pathlib import Path
from memos.hook_proxy.project_id import resolve_project_id, resolve_project_name, clear_project_id_cache


def test_read_from_memos_project_json(tmp_path):
    """读取 .memos-project JSON 文件获取 id 和 name"""
    proj_file = tmp_path / ".memos-project"
    proj_file.write_text(json.dumps({"id": "abc12345", "name": "MyProject"}), encoding="utf-8")
    clear_project_id_cache()
    pid = resolve_project_id(str(tmp_path))
    name = resolve_project_name(str(tmp_path))
    assert pid == "abc12345"
    assert name == "MyProject"


def test_no_memos_project_raises(tmp_path):
    """无 .memos-project 文件时抛出明确错误"""
    clear_project_id_cache()
    try:
        resolve_project_id(str(tmp_path))
        assert False, "Should have raised"
    except FileNotFoundError as e:
        assert ".memos-project" in str(e)


def test_memos_project_cache_hit(tmp_path):
    """缓存命中后不重复读文件"""
    proj_file = tmp_path / ".memos-project"
    proj_file.write_text(json.dumps({"id": "xyz", "name": "X"}), encoding="utf-8")
    clear_project_id_cache()
    pid1 = resolve_project_id(str(tmp_path))
    # 修改文件但缓存未清 → 仍返回旧值
    proj_file.write_text(json.dumps({"id": "abc", "name": "Y"}), encoding="utf-8")
    pid2 = resolve_project_id(str(tmp_path))
    assert pid1 == pid2 == "xyz"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_project_id.py::test_read_from_memos_project_json tests/test_unified/test_project_id.py::test_no_memos_project_raises tests/test_unified/test_project_id.py::test_memos_project_cache_hit -v 2>&1
```
预期: FAIL（test_read_from_memos_project_json / test_no_memos_project_raises 失败）

- [ ] **Step 3a: 替换测试文件 — 删除旧测试，保留新测试**

旧测试依赖三条兜底链（git remote / `.memos-project` 纯文本 / CWD MD5），与新版语义冲突。直接替换为 Task 1 创建的新测试。

```python
# tests/test_unified/test_project_id.py (全量替换)

import json
from pathlib import Path
from memos.hook_proxy.project_id import (
    resolve_project_id,
    resolve_project_name,
    clear_project_id_cache,
)


def test_read_from_memos_project_json(tmp_path):
    """读取 .memos-project JSON 文件获取 id 和 name"""
    proj_file = tmp_path / ".memos-project"
    proj_file.write_text(json.dumps({"id": "abc12345", "name": "MyProject"}), encoding="utf-8")
    clear_project_id_cache()
    pid = resolve_project_id(str(tmp_path))
    name = resolve_project_name(str(tmp_path))
    assert pid == "abc12345"
    assert name == "MyProject"


def test_no_memos_project_raises(tmp_path):
    """无 .memos-project 文件时抛出明确错误"""
    clear_project_id_cache()
    try:
        resolve_project_id(str(tmp_path))
        assert False, "Should have raised"
    except FileNotFoundError as e:
        assert ".memos-project" in str(e)


def test_memos_project_cache_hit(tmp_path):
    """缓存命中后不重复读文件"""
    proj_file = tmp_path / ".memos-project"
    proj_file.write_text(json.dumps({"id": "xyz", "name": "X"}), encoding="utf-8")
    clear_project_id_cache()
    pid1 = resolve_project_id(str(tmp_path))
    proj_file.write_text(json.dumps({"id": "abc", "name": "Y"}), encoding="utf-8")
    pid2 = resolve_project_id(str(tmp_path))
    assert pid1 == pid2 == "xyz"


def test_memos_project_bad_json_raises(tmp_path):
    """损坏的 .memos-project JSON 抛出 ValueError"""
    proj_file = tmp_path / ".memos-project"
    proj_file.write_text("not-json", encoding="utf-8")
    clear_project_id_cache()
    try:
        resolve_project_id(str(tmp_path))
        assert False, "Should have raised"
    except ValueError as e:
        assert "格式错误" in str(e)


def test_clear_cache_affects_only_specified(tmp_path):
    """clear_project_id_cache(cwd) 只清指定缓存"""
    d1 = tmp_path / "proj1"
    d1.mkdir()
    (d1 / ".memos-project").write_text(json.dumps({"id": "11111111", "name": "P1"}), encoding="utf-8")
    d2 = tmp_path / "proj2"
    d2.mkdir()
    (d2 / ".memos-project").write_text(json.dumps({"id": "22222222", "name": "P2"}), encoding="utf-8")
    clear_project_id_cache()
    pid1 = resolve_project_id(str(d1))
    pid2 = resolve_project_id(str(d2))
    clear_project_id_cache(str(d1))
    # d1 缓存被清，应读文件；d2 缓存保留
    assert resolve_project_id(str(d1)) == "11111111"
    assert resolve_project_id(str(d2)) == "22222222"
```

- [ ] **Step 3b: 重写 `project_id.py`**

```python
# src/memos/hook_proxy/project_id.py

import hashlib
import json
from pathlib import Path

_project_id_cache: dict[str, str] = {}
_project_name_cache: dict[str, str] = {}
_last_source: str | None = None


def resolve_project_id(cwd: str) -> str:
    """读取 .memos-project JSON 文件，返回 project_id。文件不存在时抛 FileNotFoundError。"""
    cwd = str(Path(cwd).resolve())
    if cwd not in _project_id_cache:
        pid, name, source = _do_resolve(cwd)
        _project_id_cache[cwd] = pid
        _project_name_cache[cwd] = name
        global _last_source
        _last_source = source
    return _project_id_cache[cwd]


def resolve_project_name(cwd: str) -> str:
    """读取 .memos-project JSON 文件，返回 project_name。文件不存在时抛 FileNotFoundError。"""
    cwd = str(Path(cwd).resolve())
    if cwd not in _project_name_cache:
        pid, name, source = _do_resolve(cwd)
        _project_id_cache[cwd] = pid
        _project_name_cache[cwd] = name
    return _project_name_cache[cwd]


def _do_resolve(cwd: str) -> tuple[str, str, str]:
    """读取 .memos-project JSON 文件，返回 (project_id, project_name, source)。"""
    proj_file = Path(cwd) / ".memos-project"
    if not proj_file.exists():
        raise FileNotFoundError(
            f"未找到 .memos-project 文件（{proj_file}），请运行 memos setup --project <项目名> 初始化"
        )
    try:
        data = json.loads(proj_file.read_text(encoding="utf-8"))
        return data["id"], data["name"], "file"
    except (json.JSONDecodeError, KeyError) as e:
        raise ValueError(f".memos-project 格式错误（{proj_file}），期望 {{\"id\": \"...\", \"name\": \"...\"}}: {e}") from e


def get_project_id_source(cwd: str = None) -> str:
    """返回 project_id 来源描述。cwd 为 None 时返回最近一次解析的来源。"""
    if cwd:
        cwd = str(Path(cwd).resolve())
        # 触发解析以填充缓存
        resolve_project_id(cwd)
        return _last_source or "unknown"
    return _last_source or "unknown"


def clear_project_id_cache(cwd: str = None):
    """清空缓存（测试用）。cwd 为 None 时清空全部。"""
    global _project_id_cache, _project_name_cache, _last_source
    if cwd:
        cwd = str(Path(cwd).resolve())
        _project_id_cache.pop(cwd, None)
        _project_name_cache.pop(cwd, None)
    else:
        _project_id_cache.clear()
        _project_name_cache.clear()
        _last_source = None
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_project_id.py -v 2>&1
```
预期: 全部 PASS（旧测试已替换为新的 5 个测试）

- [ ] **Step 5: Commit**

```bash
git add src/memos/hook_proxy/project_id.py tests/test_unified/test_project_id.py
git commit -m "refactor: project_id 解析简化为 .memos-project JSON 单一来源"
```

---

### Task 3: 新增 `cli/setup.py` — `memos setup` 命令

**文件:**
- 新增: `src/memos/cli/setup.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_unified/test_setup.py

import json
from pathlib import Path
from memos.cli.setup import cmd_setup


class Args:
    server: str = "http://192.168.1.100:8000"
    token: str = "memo_test1234567890abcdef"
    project: str = "TestProject"


def test_setup_creates_memos_project(tmp_path, monkeypatch):
    """memos setup 创建 .memos-project JSON 文件"""
    monkeypatch.chdir(tmp_path)
    args = Args()
    args.project = "MyApp"
    cmd_setup(args)

    proj_file = tmp_path / ".memos-project"
    assert proj_file.exists()
    data = json.loads(proj_file.read_text(encoding="utf-8"))
    assert "id" in data
    assert data["name"] == "MyApp"
    assert len(data["id"]) == 8


def test_setup_creates_mcp_json(tmp_path, monkeypatch):
    """memos setup 创建 .mcp.json 含 token 和 name"""
    monkeypatch.chdir(tmp_path)
    args = Args()
    args.project = "MyApp"
    cmd_setup(args)

    mcp_file = tmp_path / ".mcp.json"
    assert mcp_file.exists()
    mcp = json.loads(mcp_file.read_text(encoding="utf-8"))
    url = mcp["mcpServers"]["memos"]["url"]
    assert "token=memo_test1234567890abcdef" in url
    assert "name=MyApp" in url
    assert mcp["mcpServers"]["memos"]["type"] == "sse"


def test_setup_saves_credentials(tmp_path, monkeypatch):
    """memos setup 保存 credentials.json（重定向到临时目录）"""
    monkeypatch.chdir(tmp_path)
    # 重定向 credentials 写入路径，避免污染真实 ~/.memos/etc/credentials.json
    fake_memos_home = tmp_path / ".memos"
    from memos.config.models import get_memos_home
    monkeypatch.setattr("memos.hook_proxy.auth._CREDENTIALS_DIR", fake_memos_home / "etc")
    args = Args()
    args.project = "MyApp"
    cmd_setup(args)

    cred_file = fake_memos_home / "etc" / "credentials.json"
    assert cred_file.exists()
    creds = json.loads(cred_file.read_text(encoding="utf-8"))
    assert creds["server_url"] == "http://192.168.1.100:8000"
    assert creds["token"] == "memo_test1234567890abcdef"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_setup.py -v 2>&1
```
预期: FAIL（模块不存在）

- [ ] **Step 3: 实现 `cmd_setup`**

```python
# src/memos/cli/setup.py

import hashlib
import json
import logging
from pathlib import Path
from urllib.parse import quote

from ..hook_proxy.auth import save_credentials
from ..hook_proxy.project_id import clear_project_id_cache

logger = logging.getLogger(__name__)


def cmd_setup(args):
    """一键初始化：login + mcp install + hook install"""
    project_dir = Path.cwd()
    server_url = args.server.rstrip("/")
    token = args.token
    project_name = args.project if hasattr(args, "project") and args.project else None

    if not project_name:
        # 尝试从已有 .memos-project 读取
        proj_file = project_dir / ".memos-project"
        if not proj_file.exists():
            print("[错误] 未指定 --project 且当前目录无 .memos-project 文件")
            print("       请运行: memos setup --server <URL> --token <TOKEN> --project <项目名>")
            return
        data = json.loads(proj_file.read_text(encoding="utf-8"))
        project_name = data["name"]
        project_id = data["id"]
        print(f"[OK] 从 .memos-project 读取: id={project_id} name={project_name}")
    else:
        project_id = hashlib.md5(project_name.encode()).hexdigest()[:8]
        proj_file = project_dir / ".memos-project"
        proj_file.write_text(
            json.dumps({"id": project_id, "name": project_name}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        clear_project_id_cache(str(project_dir))
        print(f"[OK] .memos-project 已创建: id={project_id} name={project_name}")

    # Step 2: 保存凭据
    save_credentials(server_url, token)
    print(f"[OK] 凭据已保存: {server_url}")

    # Step 3: 生成 .mcp.json
    mcp_config = {
        "mcpServers": {
            "memos": {
                "type": "sse",
                "url": f"{server_url}/mcp/{project_id}/sse?name={quote(project_name)}&token={token}",
            }
        }
    }
    mcp_json_path = project_dir / ".mcp.json"
    if mcp_json_path.exists():
        existing = json.loads(mcp_json_path.read_text(encoding="utf-8"))
        existing.setdefault("mcpServers", {})
        existing["mcpServers"]["memos"] = mcp_config["mcpServers"]["memos"]
        mcp_json_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] .mcp.json 已合并（保留已有 MCP server 配置）")
    else:
        mcp_json_path.write_text(json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] .mcp.json 已生成: {mcp_config['mcpServers']['memos']['url']}")

    # Step 4: 安装 Hook
    from .dispatch import install_hooks

    install_hooks(global_mode=False)
    print("[OK] Hook 已安装到项目 settings.json")
    print()
    print("提示: 重新加载 Claude Code 后生效")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_setup.py -v 2>&1
```
预期: PASS

- [ ] **Step 5: Commit**

```bash
git add src/memos/cli/setup.py tests/test_unified/test_setup.py
git commit -m "feat: 新增 memos setup 一键配置命令"
```

---

### Task 4: 修改 `cli/dispatch.py` — wiring + mcp_install 含 token

**文件:**
- 修改: `src/memos/cli/dispatch.py`

- [ ] **Step 1: 修改 `cmd_mcp_install` — 支持 token 拼入 URL**

当前 `cmd_mcp_install`（`dispatch.py:74`）生成的 URL 不含 token。修改为从 `credentials.json` 读取并拼入。

```python
# dispatch.py:74 — 在 cmd_mcp_install 函数中，替换 mcp_config 生成逻辑

def cmd_mcp_install(args):
    """为当前项目生成带 project_id 和 token 的 .mcp.json"""
    import json
    from urllib.parse import quote

    from ..config import config as cfg
    from ..hook_proxy.auth import load_credentials
    from ..hook_proxy.project_id import resolve_project_id, resolve_project_name

    project_dir = Path.cwd()
    project_id = resolve_project_id(str(project_dir))
    project_name = resolve_project_name(str(project_dir))

    server_url = args.server or f"http://{cfg.server.host}:{cfg.server.port}"

    url = f"{server_url.rstrip('/')}/mcp/{project_id}/sse?name={quote(project_name)}"
    # 尝试从 credentials 读取 token 拼入 URL
    creds = load_credentials()
    if creds and creds.get("token"):
        url += f"&token={creds['token']}"

    mcp_config = {
        "mcpServers": {
            "memos": {
                "type": "sse",
                "url": url,
            }
        }
    }

    mcp_json_path = project_dir / ".mcp.json"
    if mcp_json_path.exists():
        existing = json.loads(mcp_json_path.read_text(encoding="utf-8"))
        existing.setdefault("mcpServers", {})
        existing["mcpServers"]["memos"] = mcp_config["mcpServers"]["memos"]
        mcp_json_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] .mcp.json 已合并更新")
    else:
        mcp_json_path.write_text(json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] .mcp.json 已生成")
```

- [ ] **Step 2: 将 `_install_hooks` 重命名为 `install_hooks`（公开）**

`dispatch.py` 中 `_install_hooks` 是私有函数（下划线前缀），`setup.py` 跨模块调用需改为公开。

```python
# dispatch.py 中，修改函数定义和所有引用
# 将 def _install_hooks(global_mode: bool): → def install_hooks(global_mode: bool):
# 将 def _uninstall_hooks(global_mode: bool): → def uninstall_hooks(global_mode: bool):
# 更新 _hook_status 和 cmd_hook 中对这两个函数的引用
```

- [ ] **Step 3: 添加 `memos setup` 到参数解析器**

找到 `p_mcp_subs`、`p_mcp_install` 等参数定义区域（约 `dispatch.py:1340`），在附近添加 `memos setup` 子命令：

```python
# dispatch.py 参数解析区域，添加 setup 子命令

p_setup = subparsers.add_parser("setup", help="一键初始化：login + mcp + hook")
p_setup.add_argument("--server", required=True, help="memos server 地址")
p_setup.add_argument("--token", required=True, help="用户 Token（从管理员获取）")
p_setup.add_argument("--project", help="项目名称（生成 project_id 并写入 .memos-project）")
```

- [ ] **Step 4: 在 `main()` 中添加 dispatch 分支**

```python
# 在 main() 函数的命令分发区域添加

if args.command == "setup":
    from .setup import cmd_setup
    cmd_setup(args)
    return
```

- [ ] **Step 5: 运行已有测试确认无回归**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_project_id.py tests/test_unified/test_mcp_project_id.py -v 2>&1
```
预期: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add src/memos/cli/dispatch.py
git commit -m "feat: cmd_mcp_install 支持 token 拼入 URL + wiring memos setup"
```

---

### Task 5: SessionAuthStore — 服务端 session→token 映射

**文件:**
- 修改: `src/memos/server/mcp.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_unified/test_session_auth.py

import time
from memos.server.mcp import SessionAuthStore


def test_put_and_get():
    store = SessionAuthStore(ttl_seconds=60)
    store.put("session-1", "token-aaa")
    assert store.get("session-1") == "token-aaa"


def test_get_nonexistent():
    store = SessionAuthStore(ttl_seconds=60)
    assert store.get("no-such-session") is None


def test_ttl_expiry():
    store = SessionAuthStore(ttl_seconds=0.05)
    store.put("session-1", "token-aaa")
    time.sleep(0.2)  # 4x TTL，留足余量避免 CI 调度抖动
    assert store.get("session-1") is None


def test_overwrite():
    store = SessionAuthStore(ttl_seconds=60)
    store.put("session-1", "token-old")
    store.put("session-1", "token-new")
    assert store.get("session-1") == "token-new"


def test_cleanup():
    store = SessionAuthStore(ttl_seconds=0.05)
    store.put("s1", "t1")
    time.sleep(0.2)  # 4x TTL
    store.cleanup()
    assert store.get("s1") is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_session_auth.py -v 2>&1
```
预期: FAIL（`SessionAuthStore` 不存在）

- [ ] **Step 3: 在 `mcp.py` 顶部添加 `SessionAuthStore` 类**

```python
# 在 mcp.py 的 import 区域之后、_default_project_id 之前添加

import threading
import time as _time

class SessionAuthStore:
    """线程安全 session_id → token 映射，支持 TTL 过期。"""

    def __init__(self, ttl_seconds: int = 1800):
        self._store: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def put(self, session_id: str, token: str) -> None:
        with self._lock:
            self._store[session_id] = (token, _time.monotonic())

    def get(self, session_id: str) -> str | None:
        with self._lock:
            entry = self._store.get(session_id)
            if entry is None:
                return None
            token, ts = entry
            if _time.monotonic() - ts > self._ttl:
                del self._store[session_id]
                return None
            # 刷新时间戳（活跃 session 续期）
            self._store[session_id] = (token, _time.monotonic())
            return token

    def cleanup(self) -> int:
        """清理所有过期 session，返回清理数量。"""
        now = _time.monotonic()
        removed = 0
        with self._lock:
            expired = [sid for sid, (_, ts) in self._store.items() if now - ts > self._ttl]
            for sid in expired:
                del self._store[sid]
                removed += 1
        return removed


# 全局单例
_session_auth_store = SessionAuthStore(ttl_seconds=1800)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_session_auth.py -v 2>&1
```
预期: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/memos/server/mcp.py tests/test_unified/test_session_auth.py
git commit -m "feat: 新增 SessionAuthStore — 线程安全 session→token 映射 + TTL"
```

---

### Task 6: 修改 `sse_wrapper.py` — token 提取 + session 映射

**文件:**
- 修改: `src/memos/server/sse_wrapper.py`

- [ ] **Step 1: 实现修改**

```python
# src/memos/server/sse_wrapper.py 完整重写

"""ASGI wrapper — 从 MCP SSE URL 路径中提取 project_id 和 auth token"""

import asyncio
import logging
import re
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

from ..web.auth import verify_token_against_users
from .mcp import (
    _PID_PATTERN,
    _auth_token_ctx,
    _session_auth_store,
    _set_session_project_id,
)

logger = logging.getLogger(__name__)

KNOWN_SUB_PATHS = frozenset({"sse", "messages"})
# 用于从 SSE endpoint 事件中提取 session_id
_SESSION_ID_RE = re.compile(r"session_id=([a-zA-Z0-9_-]+)")


class ProjectAwareSSEWrapper:
    """从 SSE URL 提取 project_id + token，将消息请求的 session_id 映射到 token。"""

    def __init__(self, mcp_app: ASGIApp):
        self.mcp_app = mcp_app
        self._pending_auth: dict[str, str] = {}  # scope_id → token（SSE 连接建立后待映射）

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.mcp_app(scope, receive, send)
            return

        path = scope.get("path", "")
        root_path = scope.get("root_path", "")

        if root_path and path.startswith(root_path):
            effective_path = path[len(root_path):]
        else:
            effective_path = path

        slash_idx = effective_path.find("/", 1)
        if slash_idx != -1:
            first_segment = effective_path[1:slash_idx]
        else:
            first_segment = effective_path[1:]

        if slash_idx != -1 and first_segment not in KNOWN_SUB_PATHS:
            pid = first_segment
            if not _PID_PATTERN.match(pid):
                logger.warning("ProjectAwareSSEWrapper: 非法 project_id 格式, 跳过: %s", pid)
                await self.mcp_app(scope, receive, send)
                return

            qs = scope.get("query_string", b"")
            params = parse_qs(qs.decode("utf-8", errors="replace")) if qs else {}

            project_name = (params.get("name") or [None])[0]
            _set_session_project_id(pid, project_name)

            # 提取 token（仅在 SSE 连接时 ?token= 参数有效）
            token = (params.get("token") or [None])[0]
            if token:
                user = verify_token_against_users(token)
                if user:
                    scope_key = str(id(scope))
                    self._pending_auth[scope_key] = token
                    logger.info("SSE token 验证通过: creator_id=%s", user["creator_id"])

            # 对于 messages 请求，按 session_id 查 token
            if slash_idx != -1 and effective_path[slash_idx:].startswith("/messages"):
                session_id = (params.get("session_id") or [None])[0]
                if session_id:
                    stored_token = _session_auth_store.get(session_id)
                    if stored_token:
                        _auth_token_ctx.set(stored_token)

            scope["root_path"] = root_path.rstrip("/") + "/" + pid

            # 包装 send 以拦截 SSE endpoint 事件，建立 session_id → token 映射
            scope_key = str(id(scope))
            if scope_key in self._pending_auth:
                pending_token = self._pending_auth.pop(scope_key)

                async def send_wrapper(message):
                    if message["type"] == "http.response.body":
                        body = message.get("body", b"")
                        text = body.decode("utf-8", errors="replace")
                        match = _SESSION_ID_RE.search(text)
                        if match and "event: endpoint" in text:
                            session_id = match.group(1)
                            _session_auth_store.put(session_id, pending_token)
                            logger.debug("SessionAuthStore: sid=%s 已映射", session_id[:8])
                    await send(message)

                try:
                    await self.mcp_app(scope, receive, send_wrapper)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("ProjectAwareSSEWrapper: SSE 子应用异常", exc_info=True)
                return

        try:
            await self.mcp_app(scope, receive, send)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("ProjectAwareSSEWrapper: SSE 子应用异常", exc_info=True)
```

- [ ] **Step 2: 运行现有测试确认无回归**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_sse_wrapper.py -v 2>&1
```
预期: 全部 PASS

- [ ] **Step 3: 运行全量测试**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/ -v --ignore=tests/test_unified/test_setup.py 2>&1 | tail -30
```
预期: PASS（setup 测试在 Task 3 创建，单独跑）

- [ ] **Step 4: Commit**

```bash
git add src/memos/server/sse_wrapper.py
git commit -m "feat: ProjectAwareSSEWrapper — SSE token 提取 + SessionAuthStore 映射"
```

---

### Task 7: 拆分 pyproject.toml 依赖

**文件:**
- 修改: `pyproject.toml`

- [ ] **Step 1: 确认 client-only CLI 可用依赖**

CLI + hook_proxy 需要的依赖（不含 ML）：
- `pydantic>=2.0` — config/models.py 需要
- `requests>=2.31` — hook_proxy HTTP 调用

当前所有依赖中，只有这两个是客户端必需的。

- [ ] **Step 2: 修改 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "memomate"
version = "0.5.1"
description = "主动式记忆伙伴"
readme = "README.md"
license = "MIT"
requires-python = ">=3.12"
authors = [
    {name = "MEMOS Team"},
]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3.12",
    "Topic :: Software Development :: Libraries :: Application Frameworks",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
]
dependencies = [
    "pydantic>=2.0",
    "requests>=2.31",
]

[project.optional-dependencies]
server = [
    "chromadb>=0.4.22",
    "sentence-transformers>=2.2.2",
    "mcp>=1.27.0",
    "rank-bm25>=0.2.2",
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "bcrypt>=4.1",
    "itsdangerous>=2.1",
    "huggingface-hub>=0.20",
]
test = [
    "pytest>=8.0",
]

[project.urls]
Homepage = "https://github.com/laofisher/memos"
Documentation = "https://github.com/laofisher/memos"
Source = "https://github.com/laofisher/memos"

[project.scripts]
memos = "memos.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"memos.web" = ["templates/**/*"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
target-version = "py312"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["N802", "N803", "N806"]

[tool.ruff.format]
quote-style = "double"
```

- [ ] **Step 3: 验证 `pip install -e .` 仅安装轻量依赖**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pip install -e . 2>&1 | grep -E "Requirement|Installing|Successfully"
```
预期: 不出现 chromadb / torch / sentence-transformers

- [ ] **Step 4: 验证 `pip install -e .[server]` 安装全量**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pip install -e ".[server]" 2>&1 | grep -E "Requirement|Installing|Successfully"
```
预期: 包含 chromadb / sentence-transformers 等

- [ ] **Step 5: 为 MEMOS 自身创建 `.memos-project`**

```json
{"id": "d0ff92fa", "name": "MEMOS"}
```

```bash
echo '{"id": "d0ff92fa", "name": "MEMOS"}' > D:/DevSpace/MEMOS/.memos-project
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .memos-project
git commit -m "refactor: pyproject.toml 依赖拆分 — 默认轻量 + [server] extras"
```

---

### Task 8: 端到端验证

**注意:** 此任务需手动执行，涉及真实服务端和 Claude Code。

- [ ] **Step 1: 安装服务器依赖并启动**

```bash
cd "D:/DevSpace/MEMOS" && pip install -e ".[server]" && memos server
```

- [ ] **Step 2: 创建测试用户**

```bash
memos user add alice
# 记录输出的 Token
```

- [ ] **Step 3: 在测试目录运行 setup**

```bash
mkdir D:/WorkSpace/e2e-test && cd D:/WorkSpace/e2e-test
memos setup --server http://localhost:8000 --token <alice_token> --project "E2ETest"
```

- [ ] **Step 4: 验证文件生成**

```bash
cat .memos-project   # 预期: {"id": "...", "name": "E2ETest"}
cat .mcp.json        # 预期: URL 含 ?name=E2ETest&token=memo_...
```

- [ ] **Step 5: Claude Code 验证**

重启 Claude Code，执行：
```
/memos:remember text="E2E 测试数据" metadata.type="fact" metadata.scope="personal"
/memos:list_memories limit=5
```

预期: `remember` 和 `list_memories` 均正常，不再报内部错误。`list_memories` 返回的 `creator_id` 应为 `"alice"` 而非 `"unknown"`。

- [ ] **Step 6: 清理**

```bash
rm -rf D:/WorkSpace/e2e-test
memos user remove alice
```

---

## 实施顺序建议

Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7 → Task 8

- Task 1-2 解耦基础依赖（无风险，可最先做）
- Task 3-4 客户端新功能（依赖 Task 2）
- Task 5-6 服务端新功能（依赖 Task 1，与 Task 3-4 可并行）
- Task 7 打包收尾（依赖以上全部）
- Task 8 手动验收
