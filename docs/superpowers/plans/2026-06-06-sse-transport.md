# SSE 传输方案实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 memos MCP 从 stdio 代理模式切换为 SSE 直连模式，消除 Claude Code 加载失败问题。

**Architecture:** FastAPI 挂载 `FastMCP.sse_app()` Starlette 子应用（`/mcp` 路径）。`mcp_proxy/` 重命名为 `hook_proxy/`，仅保留 Hook 代理。删除 `mcp_handler.py` 的 MCP 转发路由。

**Tech Stack:** Python 3.12, FastAPI, Starlette, FastMCP (SSE), requests (Hook 代理)

---

### Task 1: SSE App 挂载 + 健康检查端点

**Files:**
- Modify: `src/memos/server/app.py:127-129`
- Modify: `src/memos/server/mcp_handler.py:89-92`

- [ ] **Step 1: 在 app.py 中挂载 SSE app，替代 mcp_router**

```python
# src/memos/server/app.py — 修改 create_unified_app() 末尾部分

def create_unified_app() -> FastAPI:
    """创建 unified 模式的 FastAPI 应用"""
    app = FastAPI(title="长时记忆系统（Unified）", lifespan=lifespan)

    # FR3: mcp_handler + hook_handler ready (Phase 2.2 + 2.3)
    from ..server.hook_handler import router as hook_router
    from ..server.mcp_handler import inject_project_context

    # 中间件：注入 project_id 到 contextvars（v3.0 NB1）
    app.middleware("http")(inject_project_context)

    # 复用现有 Dashboard 中间件
    from ..web.app import AuthASGIMiddleware

    app.add_middleware(AuthASGIMiddleware)

    # 复用现有 Dashboard ProjectContext 中间件
    from ..web.middleware.project_context import ProjectContextMiddleware

    app.add_middleware(ProjectContextMiddleware)

    # Session 中间件
    from starlette.middleware.sessions import SessionMiddleware

    _secret_key = config.auth.secret_key or _derive_secret_key()
    app.add_middleware(SessionMiddleware, secret_key=_secret_key)

    # 注册 Dashboard 路由
    from ..web.app import register_routes

    register_routes(app)

    # 挂载 SSE MCP 应用（替代 include_router(mcp_router)）
    from ..server.mcp import mcp

    app.mount("/mcp", mcp.sse_app())

    # 注册 Hook HTTP 路由
    app.include_router(hook_router, prefix="/api/hooks")

    # 健康检查端点
    from ..server.mcp_handler import health

    app.add_api_route("/api/health", health, methods=["GET"])

    logger.info("MEMOS Unified Server 初始化完成")
    return app
```

关键变更：
- 删除 `from ..server.mcp_handler import router as mcp_router` 和 `app.include_router(mcp_router, prefix="/api/mcp")`
- 新增 `app.mount("/mcp", mcp.sse_app())`
- 新增 `app.add_api_route("/api/health", health, methods=["GET"])`

- [ ] **Step 2: 在 mcp_handler.py 中提取 health 为独立函数**

```python
# src/memos/server/mcp_handler.py — 修改 health 端点

# 将 health 从 router 端点改为独立函数（供 app.py 使用）

@router.get("/health")  # 保留在 router 上以兼容旧路径 /api/mcp/health
async def health():
    """健康检查端点"""
    return {"status": "ok", "version": __version__}


# 同时暴露为模块级函数（供 app.py 的 /api/health 使用）
# 无需额外包装，health 本身就是 async def
```

实际上 `add_api_route` 可以直接引用 `mcp_handler.health`，无需修改 mcp_handler.py。

- [ ] **Step 3: 删除 mcp_handler.py 的 MCP 业务路由**

删除 `mcp_handler.py` 中以下内容：
- `POST /list` (`handle_mcp_list` 函数，第 95-108 行)
- `POST /{method_name}` (`handle_mcp_call` 函数，第 111-156 行)
- `_extract_tool_list()` 辅助函数（第 30-63 行）
- `_find_tool()` 辅助函数（第 66-86 行）

保留：
- `health` 端点（`GET /health`，第 89-92 行）
- `inject_project_context` 中间件（第 16-27 行）
- `router` 定义（第 13 行）
- 导入语句

修改后的 `mcp_handler.py`：

```python
"""MCP HTTP Handler — 健康检查 + 上下文注入"""

import logging

from fastapi import APIRouter, Request

from .._version import __version__
from ..server.mcp import _auth_token_ctx, _project_id_ctx

logger = logging.getLogger(__name__)
router = APIRouter()


async def inject_project_context(request: Request, call_next):
    """FastAPI middleware — 注入 project_id/auth_token 到独立 ContextVar"""
    project_id = request.headers.get("X-Memos-Project-Id", "")
    if project_id:
        _project_id_ctx.set(project_id)

    auth_token = request.headers.get("X-Auth-Token", "")
    if auth_token:
        _auth_token_ctx.set(auth_token)

    response = await call_next(request)
    return response


@router.get("/health")
async def health():
    """健康检查端点，供代理启动时轮询"""
    return {"status": "ok", "version": __version__}
```

注意：`/health` 保留在 router 上（通过 `/api/mcp` prefix 仍可访问），同时通过 `add_api_route` 也暴露在 `/api/health`。

- [ ] **Step 4: 验证 server 启动**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -c "
import sys; sys.path.insert(0, 'src')
from memos.server.app import create_unified_app
app = create_unified_app()
print('Routes:')
for route in app.routes:
    print(f'  {route.path} -> {route.methods if hasattr(route, \"methods\") else \"mount\"}')
print('OK - App created with SSE mount')
" 2>&1
```

Expected: 看到 `/mcp` mount 和 `/api/health` 路由，无 `/api/mcp/list` 等旧路由。

- [ ] **Step 5: Commit**

```bash
git add src/memos/server/app.py src/memos/server/mcp_handler.py
git commit -m "feat: SSE app 挂载 + /api/health 端点，删除 MCP HTTP 转发路由"
```

---

### Task 2: mcp_proxy/ → hook_proxy/ 重命名与清理

**Files:**
- Create: `src/memos/hook_proxy/__init__.py`
- Create: `src/memos/hook_proxy/__main__.py`
- Create: `src/memos/hook_proxy/proxy.py`
- Create: `src/memos/hook_proxy/auth.py` (copy from mcp_proxy)
- Create: `src/memos/hook_proxy/project_id.py` (copy from mcp_proxy)
- Delete: `src/memos/mcp_proxy/` (全部文件)

- [ ] **Step 1: 创建 hook_proxy 目录并复制 auth.py 和 project_id.py**

```bash
cd "D:/DevSpace/MEMOS"
mkdir -p src/memos/hook_proxy
cp src/memos/mcp_proxy/auth.py src/memos/hook_proxy/auth.py
cp src/memos/mcp_proxy/project_id.py src/memos/hook_proxy/project_id.py
```

- [ ] **Step 2: 编写 proxy.py（仅 Hook 代理）**

```python
# src/memos/hook_proxy/proxy.py

"""Hook 代理：stdin → HTTP POST → stdout"""

import json
import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def _setup_file_logging():
    """添加文件日志处理器，写入 etc/hook_proxy.log 用于诊断"""
    try:
        etc_dir = Path(__file__).resolve().parent.parent.parent.parent / "etc"
        etc_dir.mkdir(exist_ok=True)
        log_file = etc_dir / "hook_proxy.log"
        existing = [
            h for h in logger.root.handlers
            if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(log_file)
        ]
        if existing:
            return
        handler = logging.FileHandler(log_file, encoding="utf-8", mode="a")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.root.addHandler(handler)
        logger.info("文件日志已开启: %s", log_file)
    except Exception as e:
        logger.warning("文件日志初始化失败: %s", e)


def _resolve_server_url(args_server: str | None) -> str:
    """解析 server URL，优先级：CLI参数 > 环境变量 > 配置 > 默认值"""
    if args_server:
        logger.debug("server URL from CLI: %s", args_server)
        return args_server
    env_server = os.environ.get("MEMOS_SERVER")
    if env_server:
        logger.debug("server URL from env MEMOS_SERVER: %s", env_server)
        return env_server
    try:
        _etc_dir = Path(__file__).resolve().parent.parent.parent.parent / "etc"
        _config_file = _etc_dir / "config.json"
        if _config_file.exists():
            with open(_config_file, encoding="utf-8") as _f:
                _cfg = json.load(_f)
            _proxy_url = _cfg.get("mcp_proxy", {}).get("server_url")
            if _proxy_url:
                logger.debug("server URL from etc/config.json: %s", _proxy_url)
                return _proxy_url
    except Exception:
        pass
    try:
        from ..config import config

        url = config.mcp_proxy.server_url
        logger.debug("server URL from MemoConfig: %s", url)
        return url
    except Exception:
        pass
    logger.info("server URL 使用默认值: http://localhost:8000")
    return "http://localhost:8000"


def run_hook_proxy(server_url: str, timeout: int = 30):
    """瞬发 Hook 代理：stdin → HTTP → stdout"""
    from .auth import load_credentials
    from .project_id import resolve_project_id

    _setup_file_logging()
    logger.info("Hook 代理启动: server_url=%s", server_url)

    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Hook 输入非 JSON，跳过")
        return

    project_id = resolve_project_id(os.getcwd())
    headers = {"X-Memos-Project-Id": project_id}

    creds = load_credentials()
    if creds and creds.get("token"):
        headers["X-Auth-Token"] = creds["token"]

    if "last_assistant_message" in payload:
        endpoint = "/api/hooks/stop"
    else:
        endpoint = "/api/hooks/prompt"
    logger.info(
        "Hook %s → %s%s",
        "stop" if "last_assistant_message" in payload else "prompt",
        server_url,
        endpoint,
    )

    for attempt in range(2):
        try:
            resp = requests.post(
                f"{server_url}{endpoint}",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            logger.debug("Hook 响应 HTTP %d", resp.status_code)
            result = resp.json()
            additional_context = result.get("additional_context", "")
            if additional_context:
                sys.stdout.write(additional_context)
            break
        except Exception as e:
            logger.warning("Hook 请求失败 (attempt %d/2): %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(1)
                continue
        finally:
            sys.stdout.flush()

    logger.info("Hook 代理完成")
```

- [ ] **Step 3: 编写 __init__.py**

```python
# src/memos/hook_proxy/__init__.py

"""Hook 代理子包（v0.5.0 SSE 模式）

运行模式:
  - --hook: Hook 瞬发模式（stdin → HTTP）
"""


def main(args):
    """CLI 入口"""
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from .proxy import _resolve_server_url, run_hook_proxy

    server_url = _resolve_server_url(getattr(args, "server", None))
    timeout = getattr(args, "timeout", 60)
    run_hook_proxy(server_url, timeout)
```

- [ ] **Step 4: 编写 __main__.py**

```python
# src/memos/hook_proxy/__main__.py

"""python -m memos.hook_proxy 入口

--server URL: 指定 HTTP server 地址（默认 http://localhost:8000）
--timeout N: 请求超时秒数（默认 60）
"""
import sys

from . import main


class _Args:
    """简易参数对象"""

    def __init__(self):
        self.hook = True
        self.server = None
        self.timeout = 60

        for i, arg in enumerate(sys.argv):
            if arg == "--server" and i + 1 < len(sys.argv):
                self.server = sys.argv[i + 1]
            elif arg == "--timeout" and i + 1 < len(sys.argv):
                try:
                    self.timeout = int(sys.argv[i + 1])
                except ValueError:
                    pass


if __name__ == "__main__":
    main(_Args())
```

- [ ] **Step 5: 删除 mcp_proxy 目录**

```bash
cd "D:/DevSpace/MEMOS"
rm -rf src/memos/mcp_proxy
```

- [ ] **Step 6: 验证导入**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -c "from memos.hook_proxy import main; print('import OK')" 2>&1
```

- [ ] **Step 7: Commit**

```bash
git add src/memos/hook_proxy/
git rm -r src/memos/mcp_proxy/
git commit -m "refactor: mcp_proxy 重命名为 hook_proxy，仅保留 Hook 代理功能"
```

---

### Task 3: 更新 CLI dispatch 引用

**Files:**
- Modify: `src/memos/cli/dispatch.py:74-96`

- [ ] **Step 1: 更新 import 路径**

```python
# src/memos/cli/dispatch.py — 修改 cmd_mcp、cmd_login、cmd_logout

def cmd_mcp(args):
    """启动 MCP 服务（由 SSE Server 自动处理，此命令已废弃）"""
    print("[!] MCP 代理已废弃。请启动 memos server 并通过 SSE 连接。")
    print("    .mcp.json: { \"type\": \"sse\", \"url\": \"http://localhost:8000/mcp\" }")


def cmd_login(args):
    """保存凭据到本地"""
    from ..hook_proxy.auth import save_credentials

    save_credentials(args.server, args.token)
    print(f"[OK] 凭据已保存: {args.server}")


def cmd_logout(args):
    """清除本地凭据"""
    from ..hook_proxy.auth import clear_credentials

    if clear_credentials():
        print("[OK] 凭据已清除")
    else:
        print("[!] 无凭据需要清除")
```

- [ ] **Step 2: Commit**

```bash
git add src/memos/cli/dispatch.py
git commit -m "refactor: dispatch.py 引用 hook_proxy，cmd_mcp 提示废弃"
```

---

### Task 4: 更新 .mcp.json 和 settings.json

**Files:**
- Modify: `.mcp.json`
- Modify: `.claude/settings.json`

- [ ] **Step 1: 更新 .mcp.json 为 SSE 类型**

```json
{
  "mcpServers": {
    "memos": {
      "type": "sse",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

- [ ] **Step 2: 更新 .claude/settings.json Hook 命令**

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "SAFETENSORS_FAST_LOAD=0 D:/DevSpace/MEMOS/venv/Scripts/python -m memos.hook_proxy --hook",
            "timeout": 60
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "SAFETENSORS_FAST_LOAD=0 D:/DevSpace/MEMOS/venv/Scripts/python -m memos.hook_proxy --hook",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: 验证 Hook 代理可用**

```bash
cd "D:/DevSpace/MEMOS" && echo '{"user_input":"test"}' | SAFETENSORS_FAST_LOAD=0 ./venv/Scripts/python -m memos.hook_proxy --hook 2>&1
```

Expected: 日志显示 "Hook 代理启动" 和 "Hook prompt → ..." 输出（如果 server 运行中，应返回 additional_context）

- [ ] **Step 4: Commit**

```bash
git add .mcp.json .claude/settings.json
git commit -m "config: .mcp.json 改为 SSE 类型，settings.json Hook 引用 hook_proxy"
```

---

### Task 5: 更新测试

**Files:**
- Modify: `tests/test_unified/test_proxy.py`
- Create: `tests/test_unified/test_sse.py`

- [ ] **Step 1: 重写 test_proxy.py 为 Hook 代理测试**

删除所有 MCP 代理测试（`TestReadMessage`、`TestMcpProxy`），保留 Hook 和 Auth 测试，更新导入路径：

```python
# tests/test_unified/test_proxy.py

"""测试 Hook 代理核心逻辑"""

import json
from unittest import mock

import pytest


class TestResolveServerUrl:
    """测试 server URL 解析"""

    def test_cli_arg_first(self):
        from memos.hook_proxy.proxy import _resolve_server_url

        url = _resolve_server_url("http://custom:9000")
        assert url == "http://custom:9000"

    def test_env_var_second(self, monkeypatch):
        from memos.hook_proxy.proxy import _resolve_server_url

        monkeypatch.setenv("MEMOS_SERVER", "http://env:8001")
        url = _resolve_server_url(None)
        assert url == "http://env:8001"

    def test_config_file_third(self, monkeypatch, tmp_path):
        from memos.hook_proxy.proxy import _resolve_server_url

        fake_src = tmp_path / "src" / "memos" / "hook_proxy" / "proxy.py"
        fake_src.parent.mkdir(parents=True, exist_ok=True)

        etc_dir = tmp_path / "etc"
        etc_dir.mkdir()
        (etc_dir / "config.json").write_text(
            json.dumps({"mcp_proxy": {"server_url": "http://config:8002"}}),
            encoding="utf-8",
        )

        monkeypatch.setattr("memos.hook_proxy.proxy.__file__", str(fake_src))

        url = _resolve_server_url(None)
        assert url == "http://config:8002"

    def test_default_fallback(self, monkeypatch):
        from memos.hook_proxy.proxy import _resolve_server_url

        monkeypatch.delenv("MEMOS_SERVER", raising=False)
        url = _resolve_server_url(None)
        assert url == "http://localhost:8000"


class TestAuth:
    """测试凭据管理"""

    def test_load_nonexistent(self):
        from memos.hook_proxy.auth import clear_credentials, load_credentials

        clear_credentials()
        result = load_credentials()
        assert result is None

    def test_save_and_load(self, monkeypatch, tmp_path):
        from memos.hook_proxy import auth as auth_module

        monkeypatch.setattr(auth_module, "_CREDENTIALS_DIR", tmp_path / ".memos" / "etc")
        monkeypatch.setattr(
            auth_module, "_CREDENTIALS_FILE",
            tmp_path / ".memos" / "etc" / "credentials.json",
        )

        auth_module.save_credentials("http://test:8080", "test-token-123")
        loaded = auth_module.load_credentials()
        assert loaded == {"server_url": "http://test:8080", "token": "test-token-123"}

        auth_module.clear_credentials()
        assert auth_module.load_credentials() is None

    def test_clear_returns_bool(self):
        from memos.hook_proxy.auth import clear_credentials

        clear_credentials()
        assert clear_credentials() is False


class TestHookProxy:
    """测试 Hook 代理"""

    def test_hook_proxy_sends_context(self, monkeypatch):
        from memos.hook_proxy.proxy import run_hook_proxy

        payload = json.dumps({"text": "test input"})
        mock_stdin = mock.MagicMock()
        mock_stdin.buffer.read.return_value = payload.encode("utf-8")
        monkeypatch.setattr("sys.stdin", mock_stdin)

        mock_response = mock.MagicMock()
        mock_response.json.return_value = {"additional_context": "found: test"}
        mock_post = mock.MagicMock(return_value=mock_response)

        monkeypatch.setattr("requests.post", mock_post)

        mock_stdout = mock.MagicMock()
        monkeypatch.setattr("sys.stdout", mock_stdout)

        run_hook_proxy("http://test:8000", timeout=30)

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"] == json.loads(payload)
        assert call_kwargs["timeout"] == 30

        mock_stdout.write.assert_called_with("found: test")


@pytest.fixture(autouse=True)
def _cleanup_globals():
    """清理可能影响其他测试的全局状态"""
    from memos.hook_proxy.project_id import clear_project_id_cache
    clear_project_id_cache()
    yield
    clear_project_id_cache()
```

- [ ] **Step 2: 创建 SSE 集成测试**

```python
# tests/test_unified/test_sse.py

"""测试 SSE MCP 挂载"""

import pytest
from starlette.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """创建 unified app 的 TestClient"""
    # 注意：SSE mount 需要 lifetime，用 TestClient 测试
    from memos.server.app import create_unified_app

    app = create_unified_app()
    with TestClient(app) as c:
        yield c


class TestSSEMount:
    """SSE MCP 端点测试"""

    def test_health_endpoint(self, client):
        """/api/health 返回正常"""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_health_legacy_endpoint(self, client):
        """旧 /api/mcp/health 仍可用"""
        resp = client.get("/api/mcp/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_no_mcp_list_route(self, client):
        """/api/mcp/list 已移除"""
        resp = client.post("/api/mcp/list", json={})
        # 可能 404（无此路由）或被 mount 拦截返回其他响应
        # 关键：不再是 200 的 MCP 工具列表
        assert resp.status_code != 200 or "result" not in resp.json()

    def test_mcp_sse_endpoint_exists(self, client):
        """/mcp/sse GET 端点存在（SSE 挂载）"""
        resp = client.get("/mcp/sse")
        # SSE 端点返回 200（建立 SSE 流）或 406（需要 Accept: text/event-stream）
        # 不管哪种，不应是 404
        assert resp.status_code != 404


class TestSSEEndToEnd:
    """SSE 端到端测试：通过 HTTP 模拟 MCP 客户端"""

    def test_mcp_initialize_via_sse(self, client):
        """模拟 MCP initialize 握手"""
        resp = client.post(
            "/mcp/messages/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("result", {}).get("serverInfo", {}).get("name") == "memos"

    def test_mcp_tools_list_via_sse(self, client):
        """模拟 MCP tools/list"""
        resp = client.post(
            "/mcp/messages/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        tools = data.get("result", {}).get("tools", [])
        # v0.5.0 应有 12 个 MCP 工具
        assert len(tools) >= 1
        tool_names = [t["name"] for t in tools]
        assert "remember" in tool_names
        assert "recall" in tool_names
```

- [ ] **Step 3: 运行测试验证**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/test_unified/test_proxy.py tests/test_unified/test_sse.py -v 2>&1
```

Expected: 所有新建/修改的测试通过。

- [ ] **Step 4: Commit**

```bash
git add tests/test_unified/test_proxy.py tests/test_unified/test_sse.py
git commit -m "test: 更新 Hook 代理测试 + 新增 SSE 挂载测试"
```

---

### Task 6: 全量回归测试 + Lint

- [ ] **Step 1: 运行全量测试套件**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m pytest tests/ -v --ignore=tests/test_unified/test_sse.py -k "not real" 2>&1
```

Expected: 所有已有测试通过（SSE 测试可能因 ChromaDB 初始化跳过）。

- [ ] **Step 2: Ruff 检查**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m ruff check src/ 2>&1
```

Expected: 0 errors。

- [ ] **Step 3: Ruff 格式化**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m ruff format src/ --check 2>&1
```

- [ ] **Step 4: 确认无 mcp_proxy 引用残留**

```bash
cd "D:/DevSpace/MEMOS" && grep -r "mcp_proxy" src/ tests/ .mcp.json .claude/settings.json 2>&1 || echo "No residual references found"
```

Expected: 仅在 `.claude/settings.json` hook command 中不再出现 `mcp_proxy`；`etc/config.json` 中的 `mcp_proxy` 配置字段名保留（不影响功能）。

- [ ] **Step 5: Commit**

```bash
git commit -m "chore: 回归测试通过，ruff 检查无残留引用"

# 如有 ruff 自动修复的修改，先 git add 再 commit
```

---

### Task 7: 端到端验证（手动）

**前置条件：memos server 运行中**

- [ ] **Step 1: 启动 memos server**

```bash
cd "D:/DevSpace/MEMOS" && ./venv/Scripts/python -m uvicorn memos.server.app:create_unified_app --factory --host 127.0.0.1 --port 8000 &
```

- [ ] **Step 2: 验证 SSE 端点**

```bash
curl -v http://localhost:8000/mcp/sse 2>&1 | head -20
```

Expected: 返回 SSE 流响应头或 200。

- [ ] **Step 3: 验证 MCP 工具列表**

```bash
curl -s -X POST http://localhost:8000/mcp/messages/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python -m json.tool 2>&1
```

Expected: 返回 12 个工具（`remember`, `recall`, `list_memories`, ...）。

- [ ] **Step 4: 验证 Hook 代理**

```bash
echo '{"user_input":"test hook message","prompt":"test hook message"}' | \
  SAFETENSORS_FAST_LOAD=0 D:/DevSpace/MEMOS/venv/Scripts/python -m memos.hook_proxy --hook 2>&1
```

Expected: 日志显示 "Hook prompt → http://localhost:8000/api/hooks/prompt"，正常响应。

- [ ] **Step 5: 验证健康检查**

```bash
curl -s http://localhost:8000/api/health | python -m json.tool 2>&1
```

Expected: `{"status": "ok", "version": "0.5.0"}`。

---

## 回滚方案

如需回退到旧代理方案：

```bash
git revert HEAD~7..HEAD  # 回退所有 SSE 相关提交
# 或单独恢复
git checkout HEAD~N -- src/memos/mcp_proxy/
git checkout HEAD~N -- .mcp.json .claude/settings.json
```
