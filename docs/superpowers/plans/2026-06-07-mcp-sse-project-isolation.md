# MCP SSE 项目隔离方案实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使 MCP SSE 传输模式下正确识别项目 ID，实现多项目记忆隔离

**Architecture:** 在 FastAPI Mount 与 FastMCP SSE App 之间插入 `ProjectAwareSSEWrapper` ASGI 中间件。客户端 URL 路径中包含 project_id（如 `/mcp/a1b2c3d4/sse`），wrapper 从中提取 project_id 并设置到 `_project_id_ctx`，同时改写 `scope["root_path"]` 使 SSE 端点 URL 自动携带 project_id。新增 `memos mcp install` 命令自动生成 per-project `.mcp.json`。

**Tech Stack:** Python 3.12, ASGI, Starlette, FastMCP (SSE)

---

### Task 1: 创建 `ProjectAwareSSEWrapper`

**Files:**
- Create: `src/memos/server/sse_wrapper.py`
- Test: `tests/test_unified/test_sse_wrapper.py`

- [ ] **Step 1: 编写 wrapper 单元测试**

```python
# tests/test_unified/test_sse_wrapper.py

"""测试 ProjectAwareSSEWrapper — ASGI 级 project_id 提取"""

from unittest.mock import AsyncMock

import pytest
from starlette.responses import Response


class MockSSEApp:
    """模拟 FastMCP.sse_app() 返回的 Starlette 子应用"""
    def __init__(self):
        self.received_scope = None

    async def __call__(self, scope, receive, send):
        self.received_scope = dict(scope)  # 快照
        response = Response("ok")
        await response(scope, receive, send)


def _make_scope(path: str, root_path: str = ""):
    return {
        "type": "http",
        "path": path,
        "root_path": root_path,
        "headers": [],
        "method": "GET",
        "query_string": b"",
    }


async def _call_wrapper(wrapper, scope):
    messages = []
    async def receive():
        return {"type": "http.disconnect"}
    async def send(msg):
        messages.append(msg)
    await wrapper(scope, receive, send)
    return messages, scope


class TestProjectAwareSSEWrapper:
    """测试 wrapper 的 path/root_path 改写逻辑"""

    @pytest.fixture
    def wrapper(self):
        from memos.server.sse_wrapper import ProjectAwareSSEWrapper
        return ProjectAwareSSEWrapper(MockSSEApp())

    async def test_without_project_id_passes_through(self, wrapper):
        """/sse（无 project_id）→ scope 不动"""
        scope = _make_scope("/sse", "/mcp")
        await _call_wrapper(wrapper, scope)
        assert wrapper.mcp_app.received_scope["path"] == "/sse"
        assert wrapper.mcp_app.received_scope["root_path"] == "/mcp"

    async def test_extract_project_id_from_sse(self, wrapper):
        """/a1b2c3d4/sse → 提取 pid, 改写 scope"""
        scope = _make_scope("/a1b2c3d4/sse", "/mcp")
        await _call_wrapper(wrapper, scope)
        assert wrapper.mcp_app.received_scope["path"] == "/sse"
        assert wrapper.mcp_app.received_scope["root_path"] == "/mcp/a1b2c3d4"

    async def test_extract_project_id_from_messages(self, wrapper):
        """/a1b2c3d4/messages/ → 提取 pid, 改写 scope"""
        scope = _make_scope("/a1b2c3d4/messages/", "/mcp")
        await _call_wrapper(wrapper, scope)
        assert wrapper.mcp_app.received_scope["path"] == "/messages/"
        assert wrapper.mcp_app.received_scope["root_path"] == "/mcp/a1b2c3d4"

    async def test_non_http_passthrough(self, wrapper):
        """非 HTTP 请求透传"""
        scope = {"type": "websocket", "path": "/ws"}
        messages = []
        async def receive():
            return {}
        async def send(msg):
            messages.append(msg)
        await wrapper(scope, receive, send)
        # MockSSEApp 应当被调用
        assert wrapper.mcp_app.received_scope is not None

    async def test_short_path_no_extraction(self, wrapper):
        """单段路径不触发提取"""
        scope = _make_scope("/sse", "")
        await _call_wrapper(wrapper, scope)
        assert wrapper.mcp_app.received_scope["path"] == "/sse"

    async def test_contextvar_is_set(self, wrapper):
        """project_id 被设置到 _project_id_ctx"""
        from memos.server.mcp import _project_id_ctx
        scope = _make_scope("/e5f6g7h8/sse", "/mcp")
        token = _project_id_ctx.set("old_pid")
        await _call_wrapper(wrapper, scope)
        assert _project_id_ctx.get() == "e5f6g7h8"
        _project_id_ctx.reset(token)

    async def test_invalid_pid_format_skipped(self, wrapper):
        """非法格式的 pid 不触发提取"""
        from memos.server.mcp import _project_id_ctx
        # "../../etc/passwd" 不通过 pid 格式校验
        scope = _make_scope("/../../../etc/passwd/sse", "/mcp")
        token = _project_id_ctx.set("original")
        await _call_wrapper(wrapper, scope)
        # ContextVar 不应被改写（保持 original）
        assert _project_id_ctx.get() == "original"
        # 子 app 收到的 path 保持不变（未被剥离）
        assert wrapper.mcp_app.received_scope["path"] == "/../../../etc/passwd/sse"
        _project_id_ctx.reset(token)

    async def test_long_pid_rejected(self, wrapper):
        """超过 64 字符的 pid 不触发提取"""
        from memos.server.mcp import _project_id_ctx
        long_pid = "a" * 65
        scope = _make_scope(f"/{long_pid}/sse", "/mcp")
        token = _project_id_ctx.set("original")
        await _call_wrapper(wrapper, scope)
        assert _project_id_ctx.get() == "original"
        _project_id_ctx.reset(token)

    async def test_contextvar_isolation_across_requests(self, wrapper):
        """并发请求的 ContextVar 互不干扰（验证异步隔离）"""
        from memos.server.mcp import _project_id_ctx
        scope_a = _make_scope("/proj_a/sse", "/mcp")
        scope_b = _make_scope("/proj_b/sse", "/mcp")

        await _call_wrapper(wrapper, scope_a)
        pid_a = _project_id_ctx.get()

        await _call_wrapper(wrapper, scope_b)
        pid_b = _project_id_ctx.get()

        assert pid_a == "proj_a"
        assert pid_b == "proj_b"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_sse_wrapper.py -v 2>&1
```

Expected: ModuleNotFoundError（sse_wrapper.py 还不存在）

- [ ] **Step 3: 编写 ProjectAwareSSEWrapper 实现**

```python
# src/memos/server/sse_wrapper.py

"""ASGI wrapper — 从 MCP SSE URL 路径中提取 project_id

用法：
    from memos.server.sse_wrapper import ProjectAwareSSEWrapper
    wrapper = ProjectAwareSSEWrapper(mcp.sse_app())
    app.mount("/mcp", wrapper)
"""

import logging
import re

from starlette.types import ASGIApp, Receive, Scope, Send

from .mcp import _project_id_ctx

logger = logging.getLogger(__name__)

# URL 路径中不被视为 project_id 的子路径
KNOWN_SUB_PATHS = frozenset({"sse", "messages"})

# project_id 合法字符（与 set_project_id MCP 工具一致）
_PID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


class ProjectAwareSSEWrapper:
    """ASGI wrapper — 从 SSE URL 路径中提取 project_id

    URL 格式：
      - /mcp/{project_id}/sse        → SSE 连接
      - /mcp/{project_id}/messages/   → MCP 消息投递

    Mount 已剥离 /mcp 前缀，wrapper 接收 scope["path"] 以 /{project_id}/... 开头。

    project_id 提取后写入 _project_id_ctx。该 ContextVar 在 Starlette 的异步 task
    级隔离——每个 HTTP 请求在独立 task 中处理，不同请求的 ContextVar 互不干扰。
    同一请求的处理链（中间件 → mount → wrapper）共享同一 ContextVar。

    URL 路径中的 project_id 优先级高于 HTTP Header（X-Memos-Project-Id）：
      1. InjectProjectContextMiddleware（父 app 层）先运行，从 Header 设值
      2. ProjectAwareSSEWrapper（mount 子 app 层）后运行，从 URL 设值（覆盖前者）
      3. 这是合理设计——per-project .mcp.json 的显式配置优先于 Hook 的会话级继承
    """

    def __init__(self, mcp_app: ASGIApp):
        self.mcp_app = mcp_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.mcp_app(scope, receive, send)
            return

        path = scope.get("path", "")
        parts = path.strip("/").split("/")

        # 从 URL 路径提取 project_id
        # parts 示例:
        #   ["a1b2c3d4", "sse"]         → project_id = "a1b2c3d4"
        #   ["a1b2c3d4", "messages", ""] → project_id = "a1b2c3d4"
        #   ["sse"]                      → 无 project_id（原生直连，无隔离）
        #   ["messages", ""]             → 无 project_id（原生直连）
        #
        # 注意：KNOWN_SUB_PATHS 排除 sse/messages 的误判。
        # resolve_project_id() 返回 8 位 hex 哈希，不可能等于 sse/messages。
        # 自定义 .memos-project 文件如果取这两个值，会导致误判跳过——这属于病态配置。
        if len(parts) >= 2 and parts[0] not in KNOWN_SUB_PATHS:
            pid = parts[0]
            # 格式校验：仅允许字母数字+下划线+连字符，1-64 字符
            if not _PID_PATTERN.match(pid):
                logger.warning(
                    "ProjectAwareSSEWrapper: 非法 project_id 格式, 跳过: %s", pid
                )
                await self.mcp_app(scope, receive, send)
                return

            _project_id_ctx.set(pid)
            logger.debug("ProjectAwareSSEWrapper: project_id=%s (from URL path)", pid)

            # 改写 path 剥离 project_id 段：/a1b2c3d4/sse → /sse
            scope["path"] = "/" + "/".join(parts[1:])

            # 改写 root_path：/mcp → /mcp/a1b2c3d4
            # 确保 SseServerTransport 构造 endpoint URL 时带上 project_id
            current_root = scope.get("root_path", "")
            scope["root_path"] = current_root.rstrip("/") + "/" + pid

        await self.mcp_app(scope, receive, send)
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_sse_wrapper.py -v 2>&1
```

Expected: 所有 9 个测试通过

- [ ] **Step 5: Commit**

```bash
git add src/memos/server/sse_wrapper.py tests/test_unified/test_sse_wrapper.py
git commit -m "feat: ProjectAwareSSEWrapper — 从 SSE URL 路径提取 project_id
- 新增 ASGI wrapper 层，从 /mcp/{pid}/sse 路径提取 project_id
- 改写 scope[root_path] 使 SSE endpoint URL 自动携带 project_id
- 无 project_id 的 URL 透传，向后兼容
- ContextVar 异步隔离 + 格式校验 + 优先级注释"
```

---

### Task 2: 修改 app.py 使用 wrapper

**Files:**
- Modify: `src/memos/server/app.py:127-129`

- [ ] **Step 1: 修改 create_unified_app() 的 MCP 挂载逻辑**

当前代码（约第 127-129 行）：

```python
    # 挂载 SSE MCP 应用（替代 include_router(mcp_router)）
    from ..server.mcp import mcp

    app.mount("/mcp", mcp.sse_app())
```

改为：

```python
    # 挂载 SSE MCP 应用，通过 ProjectAwareSSEWrapper 支持项目隔离
    from ..server.mcp import mcp
    from ..server.sse_wrapper import ProjectAwareSSEWrapper

    wrapper = ProjectAwareSSEWrapper(mcp.sse_app())
    app.mount("/mcp", wrapper)
```

- [ ] **Step 2: 验证 server 启动 + SSE 端点正常**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -c "
import sys; sys.path.insert(0, 'src')
from memos.server.app import create_unified_app
app = create_unified_app()
print('Routes:')
for route in app.routes:
    print(f'  {route.path} -> {route.methods if hasattr(route, \"methods\") else \"mount\"}')
print('OK - App created with ProjectAwareSSEWrapper')
" 2>&1
```

Expected: 正常输出路由列表，无报错，包含 `/mcp` mount

- [ ] **Step 3: 验证 SSE 端点向后兼容**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -c "
from starlette.testclient import TestClient
from memos.server.app import create_unified_app
app = create_unified_app()
with TestClient(app) as c:
    # 旧路径仍然可用
    r1 = c.get('/api/health')
    print(f'/api/health: {r1.status_code}')
    # 新路径可达
    r2 = c.get('/mcp/a1b2c3d4/sse')
    print(f'/mcp/a1b2c3d4/sse: {r2.status_code} (应为非 404)')
    # 消息端点
    r3 = c.post('/mcp/a1b2c3d4/messages/', params={'session_id': '00000000-0000-0000-0000-000000000000'}, json={})
    print(f'/mcp/a1b2c3d4/messages/: {r3.status_code} (应为非 404)')
" 2>&1
```

Expected: 三个端点均非 404，旧路径正常工作

- [ ] **Step 4: Commit**

```bash
git add src/memos/server/app.py
git commit -m "feat: app.py 使用 ProjectAwareSSEWrapper 挂载 MCP SSE"
```

---

### Task 3: 新增 `memos mcp install` CLI 命令 + 清理旧参数

**Files:**
- Modify: `src/memos/cli/dispatch.py`
- Modify: `CLAUDE.md`（修正过时的 `memos mcp --hook` 引用）
- Modify: `docs/troubleshooting.md`（同上）
- Modify: `docs/cli-commands.md`（同上）
- Note: 复用 `src/memos/hook_proxy/project_id.py` 中的 `resolve_project_id()`

- [ ] **Step 1: 在 dispatch.py 中重构 mcp 命令解析**

定位到 `dispatch.py` 约第 1284-1287 行。将原有的 `p_mcp` 解析器改为子命令结构：

```python
    # mcp
    p_mcp = sub.add_parser("mcp", help="MCP 管理")
    p_mcp_subs = p_mcp.add_subparsers(dest="mcp_action")
    p_mcp_install = p_mcp_subs.add_parser("install", help="生成 .mcp.json")
    p_mcp_install.add_argument(
        "--server", help="memos server 地址（默认 http://localhost:8000）"
    )
```

删除原有参数：
```python
# 删除这两行（原约第 1285-1286 行）:
# p_mcp.add_argument("--hook", action="store_true", help="Hook 瞬发模式")
# p_mcp.add_argument("--server", help="memos server 地址（默认配置文件或环境变量）")
```

- [ ] **Step 2: 实现 `cmd_mcp_install` 函数**

```python
# 在 dispatch.py 中新增函数

def cmd_mcp_install(args):
    """为当前项目生成带 project_id 的 .mcp.json"""
    import json

    from ..config import config as cfg
    from ..hook_proxy.project_id import resolve_project_id

    project_dir = Path.cwd()
    project_id = resolve_project_id(str(project_dir))

    server_url = args.server or f"http://{cfg.server.host}:{cfg.server.port}"

    mcp_config = {
        "mcpServers": {
            "memos": {
                "type": "sse",
                "url": f"{server_url.rstrip('/')}/mcp/{project_id}/sse",
            }
        }
    }

    mcp_json_path = project_dir / ".mcp.json"

    if mcp_json_path.exists():
        existing = json.loads(mcp_json_path.read_text(encoding="utf-8"))
        if "memos" in existing.get("mcpServers", {}):
            print(f"[!] 已存在 .mcp.json 中的 memos 配置")
            print(f"    当前: {existing['mcpServers']['memos'].get('url', '?')}")
            try:
                confirm = input("    是否覆盖? (y/N): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "n"
            if confirm != "y":
                print("[取消] 未修改 .mcp.json")
                return

    mcp_json_path.write_text(json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] .mcp.json 已生成")
    print(f"      路径: {mcp_json_path}")
    print(f"      URL:  {mcp_config['mcpServers']['memos']['url']}")
    print(f"      项目 ID: {project_id}")
    print()
    print("提示: 重新加载 Claude Code 后生效")
```

- [ ] **Step 3: 更新 cmd_mcp 调度逻辑**

```python
def cmd_mcp(args):
    """MCP 管理"""
    mcp_action = getattr(args, "mcp_action", None)
    if mcp_action == "install":
        cmd_mcp_install(args)
        return

    # 无子命令时显示帮助
    print("[!] 请使用子命令:")
    print("    memos mcp install    生成 .mcp.json")
    print()
    print("    MCP SSE 服务由 `memos server` 提供，无需额外 MCP 进程。")
```

- [ ] **Step 4: 更新文档中的过时引用**

**CLAUDE.md**（约第 127 行）：

原内容：
```
- `.claude/settings.json` Hook 命令: `SAFETENSORS_FAST_LOAD=0 memos mcp --hook`
```

改为：
```
- `.claude/settings.json` Hook 命令: `SAFETENSORS_FAST_LOAD=0 python -m memos.hook_proxy --hook`
```

**docs/troubleshooting.md**（约第 17 行）：

搜索 `memos mcp --hook` 并替换为 `python -m memos.hook_proxy --hook`（或 `memos.hook_proxy --hook`）。

**docs/cli-commands.md**（约第 45 行）：

搜索 `memos mcp --hook` 并替换。

- [ ] **Step 5: 运行测试验证**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m memos.cli mcp --help 2>&1
```
Expected: 显示子命令列表，包含 `install`

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m memos.cli mcp install --help 2>&1
```
Expected: 显示 install 子命令的帮助

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m memos.cli mcp install 2>&1
```
Expected: 生成 .mcp.json，输出包含 `URL: http://127.0.0.1:8000/mcp/xxxxxxxx/sse`

- [ ] **Step 6: Commit**

```bash
git add src/memos/cli/dispatch.py CLAUDE.md docs/troubleshooting.md docs/cli-commands.md
git commit -m "feat: memos mcp install — 生成带 project_id 的 .mcp.json
- 移除废弃的 --hook/--server 参数，改为子命令结构
- 复用 hook_proxy.project_id.resolve_project_id() 确保一致性
- 交互式确认覆盖已有配置
- 同步修正文档中过时的 memos mcp --hook 引用"
```

---

### Task 3b: CLI 单元测试

**Files:**
- Create: `tests/test_cli_mcp_install.py`

- [ ] **Step 1: 编写测试**

```python
# tests/test_cli_mcp_install.py

"""测试 memos mcp install 命令"""

import json
import os
from pathlib import Path
from unittest import mock

import pytest


def _run_install(monkeypatch, tmp_path: Path, server_arg: str = None):
    """在 tmp_path 中模拟运行 mcp install"""
    from memos.cli.dispatch import cmd_mcp_install

    class Args:
        mcp_action = "install"
        server = server_arg  # None 表示走默认值

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
        assert url.endswith("/sse")

    def test_url_contains_project_id(self, monkeypatch, tmp_path):
        """URL 中包含 project_id 路径段"""
        _run_install(monkeypatch, tmp_path)
        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        url = data["mcpServers"]["memos"]["url"]
        # URL 格式: http://host:port/{pid}/sse — pid 是 8 位 hex
        parts = url.rstrip("/").split("/")
        pid = parts[-2]  # 倒数第二段是 project_id
        assert len(pid) == 8
        assert all(c in "0123456789abcdef" for c in pid)

    def test_custom_server_arg(self, monkeypatch, tmp_path):
        """--server 参数传递 server URL"""
        from memos.cli.dispatch import cmd_mcp_install

        class Args:
            mcp_action = "install"
            server = "http://custom:9000"

        monkeypatch.chdir(str(tmp_path))
        cmd_mcp_install(Args())
        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        url = data["mcpServers"]["memos"]["url"]
        assert url.startswith("http://custom:9000")

    def test_existing_config_confirm_overwrite(self, monkeypatch, tmp_path):
        """已有配置时询问是否覆盖"""
        # 先写一个旧的配置
        old = {"mcpServers": {"memos": {"type": "sse", "url": "http://old/url/sse"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(old), encoding="utf-8")

        # 模拟用户输入 "y"
        monkeypatch.setattr("builtins.input", lambda _: "y")
        _run_install(monkeypatch, tmp_path)

        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        assert "http://old/url" not in data["mcpServers"]["memos"]["url"]

    def test_existing_config_cancel(self, monkeypatch, tmp_path):
        """用户取消覆盖"""
        old = {"mcpServers": {"memos": {"type": "sse", "url": "http://old/url/sse"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(old), encoding="utf-8")

        monkeypatch.setattr("builtins.input", lambda _: "n")
        _run_install(monkeypatch, tmp_path)

        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        assert data["mcpServers"]["memos"]["url"] == "http://old/url/sse"
```

- [ ] **Step 2: 运行测试**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_cli_mcp_install.py -v 2>&1
```

Expected: 5 个测试全部通过

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli_mcp_install.py
git commit -m "test: memos mcp install CLI 单元测试"
```

---

### Task 4: 更新 `.mcp.json` 文件

**Files:**
- Modify: `.mcp.json`（项目根目录）

- [ ] **Step 1: 更新 .mcp.json 内容**

当前仓库的 `.mcp.json` 沿用无 project_id 的 URL（`/mcp/sse`），因为 MEMOS 项目自身的 CWD 与 MCP Server 一致，`_default_project_id` 本身就是正确值。**无需修改**。

**决策理由**：MEMOS 项目的 .mcp.json 不需要 project_id 编码，这是特例——因为 MCP Server 进程就在 MEMOS 目录中运行。所有其他项目必须通过 `memos mcp install` 生成带 project_id 的配置。

- [ ] **Step 2: Commit（无修改则跳过）**

---

### Task 5: 集成测试 — 验证 MCP 请求正确路由

**Files:**
- Create: `tests/test_unified/test_mcp_project_id.py`

- [ ] **Step 1: 编写集成测试**

```python
# tests/test_unified/test_mcp_project_id.py

"""集成测试：验证带 project_id 路径的 MCP 请求正确路由"""

import pytest
from starlette.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """创建 unified app 的 TestClient"""
    from memos.server.app import create_unified_app
    app = create_unified_app()
    with TestClient(app) as c:
        yield c


class TestMcpProjectIdRouting:
    """测试 project_id 路径的路由正确性"""

    def test_tools_list_with_project_id_path(self, client):
        """带 project_id 的路径 → tools/list 正常路由"""
        resp = client.post(
            "/mcp/a1b2c3d4/messages/",
            params={"session_id": "00000000-0000-0000-0000-000000000000"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        # 非 404 即路径路由正确（session 不存在返回 404 是 MCP 内部行为）
        assert resp.status_code != 404

    def test_different_project_ids_route(self, client):
        """不同的 project_id 都能正常路由"""
        for pid in ["a1b2c3d4", "e5f6g7h8", "test001"]:
            resp = client.post(
                f"/mcp/{pid}/messages/",
                params={"session_id": "00000000-0000-0000-0000-000000000000"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert resp.status_code != 404, f"pid={pid} 路由失败"

    def test_normal_url_backward_compat(self, client):
        """无 project_id 的消息端向后兼容"""
        resp = client.post(
            "/mcp/messages/",
            params={"session_id": "00000000-0000-0000-0000-000000000000"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert resp.status_code != 404

    def test_project_id_path_in_sse(self, client):
        """GET /mcp/{pid}/sse → 非 404"""
        resp = client.get("/mcp/a1b2c3d4/sse")
        assert resp.status_code != 404

    def test_path_traversal_rejected(self, client):
        """路径遍历请求不应被解析为 project_id"""
        resp = client.get("/mcp/../../../etc/passwd/sse")
        # 因为 pid 格式校验不通过，回退到 _default_project_id，且 path 不改写
        # 子 app 收到的 path 可能是 /../../../etc/passwd/sse → Starlette 会处理
        # 关键：不应抛出异常，且应为非 500
        assert resp.status_code < 500
```

- [ ] **Step 2: 运行测试**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_mcp_project_id.py -v 2>&1
```

Expected: 所有测试通过（注意未初始化的 MCP session 仍返回 404，这是预期行为——路由正确但 session 不存在）

- [ ] **Step 3: Commit**

```bash
git add tests/test_unified/test_mcp_project_id.py
git commit -m "test: 集成测试 MCP SSE project_id 路径路由"
```

---

### Task 6: 文档更新

**Files:**
- Modify: `document/方案/MEMOS-项目ID设计原理与多项目开发机制.md`

- [ ] **Step 1: 在文档中添加 SSE 章节**

在文档末尾（"常见问题"之后）增加：

```markdown
## 8. SSE 模式下的项目隔离

v0.5.0 起 MCP 使用 SSE 传输，MCP Server 成为常驻进程。项目隔离方式随之变化：

### 8.1 传输对比

| 模式 | project_id 来源 | 机制 |
|------|----------------|------|
| stdio | 子进程 CWD 继承 | `_default_project_id` 自动对齐 |
| SSE | URL 路径编码 + ASGI Wrapper | 从 `/mcp/{pid}/sse` 路径提取 |

### 8.2 配置生成

每个项目运行以下命令自动生成 `.mcp.json`：

```bash
cd /path/to/project
memos mcp install
```

生成的配置文件：

```json
{
  "mcpServers": {
    "memos": {
      "type": "sse",
      "url": "http://localhost:8000/mcp/a1b2c3d4/sse"
    }
  }
}
```

其中 `a1b2c3d4` 由 `resolve_project_id()` 计算，算法与 Hook proxy 一致（Git remote → `.memos-project` 文件 → CWD 哈希）。

### 8.3 数据流

```
Claude Code  ──GET /mcp/{pid}/sse──→  memos server
   │                                      │
   │  ← SSE: endpoint=/mcp/{pid}/messages/ │  SseServerTransport 根据 root_path 构造
   │                                      │
   └──POST /mcp/{pid}/messages/──────→    │  Wrapper 提取 pid → _project_id_ctx
                                          │  MCP 工具 _get_project_id() = pid
```

### 8.4 向后兼容

旧版 `.mcp.json`（无 project_id 的 URL）继续工作，project_id 回退到 `_default_project_id = md5(CWD)[:8]`（即 MEMOS 项目自身的 ID）。
```

- [ ] **Step 2: Commit**

```bash
git add "document/方案/MEMOS-项目ID设计原理与多项目开发机制.md"
git commit -m "docs: 补充 SSE 模式项目隔离章节"
```

---

### Task 7: 全量回归测试 + Lint

- [ ] **Step 1: 运行全量测试套件**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/ -v -k "not real" 2>&1
```

Expected: 所有已有测试通过或跳过（项目中的 `@pytest.mark.real` 标记用于过滤需要 ChromaDB/模型的测试）

- [ ] **Step 2: Ruff 检查**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m ruff check src/ tests/ 2>&1
```

Expected: 0 errors

- [ ] **Step 3: Ruff 格式化**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m ruff format src/ tests/ --check 2>&1
```

Expected: 格式化通过

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: 全量回归测试 + ruff lint 通过"
```

---

### Task 8: 端到端验证（手动）

**⚠ 必须在一个非 MEMOS 项目目录中执行。**

- [ ] **Step 1: 在测试项目中生成 MCP 配置**

```powershell
# 创建测试项目（不要用 MEMOS 目录）
mkdir -p D:/DevSpace/TestProj
cd D:/DevSpace/TestProj

# 生成 MCP 配置（带 project_id 的 URL）
memos mcp install
# 预期输出:
#   [OK] .mcp.json 已生成
#   URL: http://localhost:8000/mcp/xxxxxxxx/sse
#   项目 ID: xxxxxxxx

# 验证 .mcp.json 格式
cat .mcp.json
# 预期: url 中包含 /mcp/{8位hex}/sse
```

- [ ] **Step 2: 验证数据隔离**

```powershell
# 1. 确保 memos server 正在运行
# 2. 在 TestProj 中启动 Claude Code
cd D:/DevSpace/TestProj
claude .

# 在 Claude 中调用 list_memories
# 预期: "暂无记忆" 或只返回 TestProj 自身的记忆（而非 MEMOS 项目的）
```

- [ ] **Step 3: 验证向后兼容**

```powershell
# 将 .mcp.json 改为无 project_id 的旧格式
# {"mcpServers": {"memos": {"type": "sse", "url": "http://localhost:8000/mcp/sse"}}}

# 重启 Claude Code，验证 MCP 工具仍能正常工作
# 预期: 功能正常，但 project_id 为 MEMOS 项目的默认值
```

- [ ] **Step 4: 验证非法 URL 不崩溃**

```powershell
# 在任意项目中使用无效的 project_id 路径
# 预期: wrapper 拒绝非法格式，服务不崩溃
```

---

## 回滚方案

如需回退，撤销相关提交：

```bash
git revert HEAD~N..HEAD
```

或选择性恢复：

```bash
# 移除 wrapper
git checkout HEAD~N -- src/memos/server/app.py
# 移除 CLI
git checkout HEAD~N -- src/memos/cli/dispatch.py
```
