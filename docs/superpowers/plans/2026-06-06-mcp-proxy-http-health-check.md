# MCP 代理 HTTP 模式修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 MCP 加载失败（`✘ failed`），删除 stdio 直接模式，HTTP 代理变默认并加 health check 等待 Server 就绪。

**Architecture:** 后台线程轮询 `GET /api/health`，主线程立即进入消息循环处理 MCP 握手。业务工具在 Server 就绪前返回 -32004 错误，就绪后 HTTP 转发。`--http` 成为默认模式。

**Tech Stack:** Python 3.12 | requests | threading.Event | FastAPI

**Spec:** `document/50版本/MEMOS-v0.5.0-MCP代理修复-设计方案.md`

---

### Task 1: 新增 GET /api/health 端点

**Files:**
- Modify: `src/memos/server/mcp_handler.py:89-97`

- [ ] **Step 1: 在 `handle_mcp_list` 之前添加 health 端点**

`/health` 路由必须在 `/{method_name}` 之前注册，避免路径冲突。

```python
# 在 @router.post("/list") 之前插入

@router.get("/health")
async def health():
    """健康检查端点，供代理启动时轮询"""
    return {"status": "ok", "version": "0.5.0"}
```

插入位置：`mcp_handler.py` 第 88 行后（`from types import SimpleNamespace` 之后，`@router.post("/list")` 之前）。

- [ ] **Step 2: 启动 Server 验证端点**

```bash
./venv/Scripts/python -m uvicorn memos.dashboard:app --host 127.0.0.1 --port 8000 &
sleep 3
curl -s http://localhost:8000/api/mcp/health
```

Expected: `{"status":"ok","version":"0.5.0"}`

- [ ] **Step 3: 验证路由优先级 —— `/list` 仍正常**

```bash
curl -s -X POST http://localhost:8000/api/mcp/list \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Expected: 返回 12 个工具（200 OK）。

- [ ] **Step 4: Commit**

```bash
git add src/memos/server/mcp_handler.py
git commit -m "feat: 添加 /api/mcp/health 健康检查端点"
```

---

### Task 2: 修复 handle_mcp_list 响应 id

**Files:**
- Modify: `src/memos/server/mcp_handler.py:89-97`

- [ ] **Step 1: 从 request body 取 id 而非 header**

当前代码（`handle_mcp_list` 函数）第 93-97 行：

```python
@router.post("/list")
async def handle_mcp_list(request: Request):
    """返回 MCP 工具列表"""
    tools = await _extract_tool_list()
    return {
        "jsonrpc": "2.0",
        "id": request.headers.get("x-request-id", "1"),
        "result": {"tools": tools},
    }
```

改为：

```python
@router.post("/list")
async def handle_mcp_list(request: Request):
    """返回 MCP 工具列表"""
    try:
        body = await request.json()
        req_id = body.get("id", "1")
    except Exception:
        req_id = "1"
    tools = await _extract_tool_list()
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"tools": tools},
    }
```

- [ ] **Step 2: 验证 id 正确回传**

```bash
curl -s -X POST http://localhost:8000/api/mcp/list \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":42,"method":"tools/list"}' | python -c "import sys,json; d=json.load(sys.stdin); assert d['id']==42, f'Expected id=42, got {d[\"id\"]}'; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/memos/server/mcp_handler.py
git commit -m "fix: handle_mcp_list 从 request body 解析 id"
```

---

### Task 3: 重构 proxy.py —— 删除 stdio 模式，加 health check 线程

**Files:**
- Modify: `src/memos/mcp_proxy/proxy.py`

- [ ] **Step 1: 删除 `_run_stdio_proxy` 函数**

删除整个函数体（第 208-281 行，约 74 行）。保留节标记注释：

```
# ==== Stdio 直接模式已删除（v0.5.0）====
# 统一 Server 独占 ChromaDB SQLite 锁
```

- [ ] **Step 2: 删除 `_handle_tools_list` 函数**

删除整个函数体（第 166-182 行，约 17 行）。

- [ ] **Step 3: 删除 `_handle_tools_call` 函数**

删除整个函数体（第 184-206 行，约 23 行）。

- [ ] **Step 4: 在 `_run_http_proxy` 中添加 health check 后台线程**

在 `_run_http_proxy` 函数内，`logger.info("HTTP 代理已连接: %s ...")` 之后、`while True:` 之前插入：

```python
    import threading

    server_ready = threading.Event()
    health_url = f"{server_url}/api/mcp/health"

    def _poll_health():
        while not server_ready.is_set():
            try:
                resp = session.get(health_url, timeout=5)
                if resp.ok:
                    server_ready.set()
                    logger.info("Server 就绪: %s", health_url)
                    return
            except Exception:
                pass
            time.sleep(2)

    threading.Thread(target=_poll_health, daemon=True).start()
    logger.info("Health check 线程已启动，等待 Server 就绪...")
```

- [ ] **Step 5: 修改消息循环 —— tools/* 转发前检查 `server_ready`**

在主循环中，在 `_send_with_retry` 调用前封装 `server_ready` 检查。将：

```python
        # MCP 方法名如 "tools/list" → 截取 "list" 作为后端路径
        mcp_method = method.split("/")[-1]
        url = f"{server_url}/api/mcp/{mcp_method}"

        _send_with_retry(session, url, request, headers, timeout)
```

改为：

```python
        # 业务工具在 Server 就绪前返回明确错误
        if not server_ready.is_set():
            logger.warning("Server 未就绪，拒绝 %s (id=%s)", method, req_id)
            error_resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32004,
                    "message": "Server not ready, retrying...",
                },
            }
            body_bytes = json.dumps(error_resp).encode()
            sys.stdout.buffer.write(
                f"Content-Length: {len(body_bytes)}\r\n\r\n".encode()
            )
            sys.stdout.buffer.write(body_bytes)
            sys.stdout.buffer.flush()
            continue

        # MCP 方法名如 "tools/list" → 截取 "list" 作为后端路径
        mcp_method = method.split("/")[-1]
        url = f"{server_url}/api/mcp/{mcp_method}"

        _send_with_retry(session, url, request, headers, timeout)
```

- [ ] **Step 6: 移除函数内冗余 `import requests`**

`_run_http_proxy` 内的 `import requests` 改为 `pass` 或删除（模块顶部已导入）。

- [ ] **Step 7: 运行已有测试确认无回归**

```bash
./venv/Scripts/python -m pytest tests/test_unified/test_proxy.py -v
```

Expected: 所有测试通过（需更新测试后才能通过，此处先检查语法/导入无报错）。

- [ ] **Step 8: Commit**

```bash
git add src/memos/mcp_proxy/proxy.py
git commit -m "refactor: 删除 stdio 直接模式，HTTP 代理加 health check 线程"
```

---

### Task 4: 更新 __init__.py —— HTTP 变默认

**Files:**
- Modify: `src/memos/mcp_proxy/__init__.py`

- [ ] **Step 1: 重写 `main()` 函数**

将当前 `main()` 函数（第 12-35 行）替换为：

```python
def main(args):
    """CLI 入口"""
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    from .proxy import _run_hook_proxy, _run_http_proxy, _resolve_server_url

    # 1. Hook 模式：瞬发，与 HTTP server 通信
    if getattr(args, "hook", False):
        server_url = _resolve_server_url(getattr(args, "server", None))
        timeout = getattr(args, "timeout", 60)
        _run_hook_proxy(server_url, timeout)
        return

    # 2. 默认：HTTP 代理模式（唯一路径）
    server_url = _resolve_server_url(getattr(args, "server", None))
    timeout = getattr(args, "timeout", 60)
    _run_http_proxy(server_url, timeout)
```

- [ ] **Step 2: 更新模块文档字符串**

第 1-9 行改为：

```python
"""MCP 薄代理子包（v0.5.0 unified 模式）

运行模式:
  - 默认: HTTP 代理模式（stdio → HTTP 转发，需统一 Server 运行）
  - --hook: Hook 瞬发模式（stdin → HTTP）
"""
```

- [ ] **Step 3: Commit**

```bash
git add src/memos/mcp_proxy/__init__.py
git commit -m "refactor: HTTP 代理模式变为默认，移除 stdio 分支"
```

---

### Task 5: 更新 __main__.py —— 更新参数说明

**Files:**
- Modify: `src/memos/mcp_proxy/__main__.py`

- [ ] **Step 1: 更新模块文档字符串和 `_Args` 类**

将文件内容替换为：

```python
# src/memos/mcp_proxy/__main__.py

"""python -m memos.mcp_proxy 入口

默认: HTTP 代理模式（stdio → HTTP 转发）
--hook: Hook 瞬发模式
--server URL: 指定 HTTP server 地址（默认 http://localhost:8000）
--timeout N: 请求超时秒数（默认 60）
"""
import sys

from . import main


class _Args:
    """简易参数对象"""

    def __init__(self):
        self.http = True  # 默认 HTTP 模式
        self.hook = "--hook" in sys.argv
        self.server = None
        self.timeout = 60

        for i, arg in enumerate(sys.argv):
            if arg == "--hook":
                self.hook = True
            elif arg == "--server" and i + 1 < len(sys.argv):
                self.server = sys.argv[i + 1]
            elif arg == "--timeout" and i + 1 < len(sys.argv):
                try:
                    self.timeout = int(sys.argv[i + 1])
                except ValueError:
                    pass


if __name__ == "__main__":
    main(_Args())
```

- [ ] **Step 2: Commit**

```bash
git add src/memos/mcp_proxy/__main__.py
git commit -m "chore: __main__.py 移除 --http 选项，HTTP 为默认行为"
```

---

### Task 6: 更新 .mcp.json

**Files:**
- Modify: `.mcp.json`

- [ ] **Step 1: 添加 `--http` 参数**

当前内容：

```json
{
  "mcpServers": {
    "memos": {
      "type": "stdio",
      "command": "D:/DevSpace/MEMOS/venv/Scripts/python",
      "args": [
        "-m",
        "memos.mcp_proxy"
      ],
      "env": {},
      "description": "v0.5.0 unified: mcp_proxy 将 stdio MCP 请求通过 HTTP 转发到 memos server，避免多进程 ChromaDB 锁竞争"
    }
  }
}
```

`args` 改为 `["-m", "memos.mcp_proxy", "--http"]`，description 更新：

```json
{
  "mcpServers": {
    "memos": {
      "type": "stdio",
      "command": "D:/DevSpace/MEMOS/venv/Scripts/python",
      "args": ["-m", "memos.mcp_proxy", "--http"],
      "env": {},
      "description": "v0.5.0 unified: HTTP 代理与统一 Server 通信，单进程持有 ChromaDB 锁"
    }
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add .mcp.json
git commit -m "chore: .mcp.json 启用 HTTP 代理模式"
```

---

### Task 7: 更新测试

**Files:**
- Modify: `tests/test_unified/test_proxy.py`

- [ ] **Step 1: 删除 stdio 模式相关测试类/用例**

搜索并删除引用 `_run_stdio_proxy`、`_handle_tools_list`、`_handle_tools_call` 的测试。

当前 `test_proxy.py` 不含 stdio 相关测试（之前的代码里没有），确认无需删除。

- [ ] **Step 2: 更新 `TestMcpProxy` 中的 `test_mcp_sends_request_with_project_id`**

该测试 mock 了 `_run_http_proxy` 的初始化流程。需要在 mock 链中加入 `threading.Event` 和 `server_ready.set()` 的逻辑。

修改后的测试（在 `_run_http_proxy` 入口处 mock `threading.Event` 和 `threading.Thread`）：

```python
class TestMcpProxy:
    """测试 MCP 代理循环"""

    def test_mcp_sends_request_with_project_id(self, monkeypatch):
        from memos.mcp_proxy.proxy import _run_http_proxy

        payload = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        content_line = f"Content-Length: {len(payload)}\r\n".encode()

        mock_stdin = mock.MagicMock()
        mock_stdin.buffer.readline.side_effect = [
            content_line,
            b"\r\n",
            b"",  # EOF，终止循环
        ]
        mock_stdin.buffer.read.return_value = payload.encode("utf-8")
        monkeypatch.setattr("sys.stdin", mock_stdin)

        mock_response = mock.MagicMock()
        mock_response.content = json.dumps(
            {"result": {"tools": ["tool1"]}}
        ).encode("utf-8")
        mock_session = mock.MagicMock()
        # session.get 用于 health check，session.post 用于工具转发
        mock_health_resp = mock.MagicMock()
        mock_health_resp.ok = True
        mock_session.get.return_value = mock_health_resp
        mock_session.post.return_value = mock_response
        monkeypatch.setattr(
            "requests.Session", mock.MagicMock(return_value=mock_session)
        )

        # mock threading.Event 和 Thread，让 health check 立即标记就绪
        mock_event = mock.MagicMock()
        mock_event.is_set.return_value = True
        monkeypatch.setattr("threading.Event", mock.MagicMock(return_value=mock_event))
        mock_thread = mock.MagicMock()
        monkeypatch.setattr(
            "threading.Thread", mock.MagicMock(return_value=mock_thread)
        )

        mock_stdout = mock.MagicMock()
        monkeypatch.setattr("sys.stdout", mock_stdout)

        _run_http_proxy("http://test:8000", timeout=60)

        call_args = mock_session.post.call_args
        assert call_args is not None

        url = call_args[0][0]
        assert "/api/mcp/list" in url

        headers = call_args[1]["headers"]
        assert "X-Memos-Project-Id" in headers

    def test_mcp_returns_error_when_server_not_ready(self, monkeypatch):
        """Server 未就绪时 tools/list 返回 -32004 错误"""
        from memos.mcp_proxy.proxy import _run_http_proxy

        payload = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        content_line = f"Content-Length: {len(payload)}\r\n".encode()

        mock_stdin = mock.MagicMock()
        mock_stdin.buffer.readline.side_effect = [
            content_line,
            b"\r\n",
            b"",  # EOF
        ]
        mock_stdin.buffer.read.return_value = payload.encode("utf-8")
        monkeypatch.setattr("sys.stdin", mock_stdin)

        mock_session = mock.MagicMock()
        monkeypatch.setattr(
            "requests.Session", mock.MagicMock(return_value=mock_session)
        )

        # server_ready.is_set() 返回 False
        mock_event = mock.MagicMock()
        mock_event.is_set.return_value = False
        monkeypatch.setattr("threading.Event", mock.MagicMock(return_value=mock_event))
        mock_thread = mock.MagicMock()
        monkeypatch.setattr(
            "threading.Thread", mock.MagicMock(return_value=mock_thread)
        )

        stdout_writes = []
        mock_stdout = mock.MagicMock()
        mock_stdout.buffer.write = lambda b: stdout_writes.append(b)
        mock_stdout.buffer.flush = mock.MagicMock()
        monkeypatch.setattr("sys.stdout", mock_stdout)

        _run_http_proxy("http://test:8000", timeout=60)

        # 确认 session.post 没有被调用（因为 server 未就绪）
        mock_session.post.assert_not_called()

        # 确认输出了错误响应
        all_output = b"".join(stdout_writes)
        assert b"-32004" in all_output
        assert b"Server not ready" in all_output
```

- [ ] **Step 3: 更新 `test_mcp_timeout_retry` 测试**

添加同样的 `threading.Event` / `threading.Thread` mock（`is_set` 返回 True 以允许转发）：

```python
    def test_mcp_timeout_retry(self, monkeypatch):
        from memos.mcp_proxy.proxy import _run_http_proxy

        payload = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 2})
        content_line = f"Content-Length: {len(payload)}\r\n".encode()

        mock_stdin = mock.MagicMock()
        mock_stdin.buffer.readline.side_effect = [
            content_line,
            b"\r\n",  # 一条消息
            b"",      # EOF
        ]
        mock_stdin.buffer.read.return_value = payload.encode("utf-8")
        monkeypatch.setattr("sys.stdin", mock_stdin)

        mock_session = mock.MagicMock()
        mock_health_resp = mock.MagicMock()
        mock_health_resp.ok = True
        mock_session.get.return_value = mock_health_resp
        mock_session.post.side_effect = [
            requests.exceptions.Timeout("timeout"),
            mock.MagicMock(
                content=json.dumps({"result": "ok"}).encode("utf-8")
            ),
        ]
        monkeypatch.setattr(
            "requests.Session", mock.MagicMock(return_value=mock_session)
        )

        mock_event = mock.MagicMock()
        mock_event.is_set.return_value = True
        monkeypatch.setattr("threading.Event", mock.MagicMock(return_value=mock_event))
        mock_thread = mock.MagicMock()
        monkeypatch.setattr(
            "threading.Thread", mock.MagicMock(return_value=mock_thread)
        )

        mock_stdout = mock.MagicMock()
        monkeypatch.setattr("sys.stdout", mock_stdout)
        monkeypatch.setattr("time.sleep", mock.MagicMock())

        _run_http_proxy("http://test:8000", timeout=60)
        assert mock_session.post.call_count == 2
```

- [ ] **Step 4: 运行测试**

```bash
./venv/Scripts/python -m pytest tests/test_unified/test_proxy.py -v
```

Expected: 4 tests PASS（含新增 `test_mcp_returns_error_when_server_not_ready`）。

- [ ] **Step 5: Commit**

```bash
git add tests/test_unified/test_proxy.py
git commit -m "test: 更新代理测试 —— health check + server 未就绪错误响应"
```

---

### Task 8: 端到端验证

- [ ] **Step 1: 确保 Server 运行**

```bash
./venv/Scripts/python -m uvicorn memos.dashboard:app --host 127.0.0.1 --port 8000 &
sleep 3
```

- [ ] **Step 2: 启动代理并测试握手**

```bash
timeout 10 ./venv/Scripts/python -c "
import subprocess, json, time

VENV = r'D:\DevSpace\MEMOS\venv\Scripts\python.exe'
CWD = r'D:\DevSpace\MEMOS'

proc = subprocess.Popen(
    [VENV, '-m', 'memos.mcp_proxy', '--http'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=CWD,
)
time.sleep(3)

# initialize
body = json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'test','version':'1.0'}}}).encode()
proc.stdin.write(f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
proc.stdin.flush()
time.sleep(1)

# tools/list
body2 = json.dumps({'jsonrpc':'2.0','id':2,'method':'tools/list'}).encode()
proc.stdin.write(f'Content-Length: {len(body2)}\r\n\r\n'.encode() + body2)
proc.stdin.flush()
time.sleep(5)

proc.terminate()
stdout, stderr = proc.communicate(timeout=3)
print(f'STDOUT: {len(stdout)} bytes', flush=True)
# 解析帧
buf = stdout
count = 0
while buf:
    idx = buf.find(b'\r\n\r\n')
    if idx < 0: break
    hdr = buf[:idx].decode()
    buf = buf[idx+4:]
    cl = None
    for line in hdr.split('\r\n'):
        if 'content-length' in line.lower():
            cl = int(line.split(':')[1].strip())
    if cl and cl <= len(buf):
        body_data = json.loads(buf[:cl].decode())
        buf = buf[cl:]
        count += 1
        rid = body_data.get('id','?')
        if 'result' in body_data:
            r = body_data['result']
            if 'tools' in r:
                print(f'Frame {count} (id={rid}): TOOLS LIST - {len(r[\"tools\"])} tools', flush=True)
            else:
                print(f'Frame {count} (id={rid}): {str(r)[:100]}', flush=True)
        elif 'error' in body_data:
            print(f'Frame {count} (id={rid}): ERROR {body_data[\"error\"]}', flush=True)

assert count >= 2, f'Expected at least 2 frames, got {count}'
print('E2E PASS', flush=True)
"
```

Expected: 两帧响应（`initialize` + `tools/list`），`tools/list` 返回 12 个工具。

- [ ] **Step 3: 测试 Server 未就绪场景**

先停掉 Server，再启动代理：

```bash
# 停掉 Server
pkill -f "uvicorn memos.dashboard" 2>/dev/null || true

timeout 10 ./venv/Scripts/python -c "
import subprocess, json, time

VENV = r'D:\DevSpace\MEMOS\venv\Scripts\python.exe'
CWD = r'D:\DevSpace\MEMOS'

proc = subprocess.Popen(
    [VENV, '-m', 'memos.mcp_proxy', '--http'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=CWD,
)
time.sleep(2)

# 仅发送 initialize + tools/list
body = json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'test','version':'1.0'}}}).encode()
proc.stdin.write(f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
body2 = json.dumps({'jsonrpc':'2.0','id':2,'method':'tools/list'}).encode()
proc.stdin.write(f'Content-Length: {len(body2)}\r\n\r\n'.encode() + body2)
proc.stdin.flush()
time.sleep(3)

proc.terminate()
stdout, stderr = proc.communicate(timeout=3)
buf = stdout
while buf:
    idx = buf.find(b'\r\n\r\n')
    if idx < 0: break
    hdr = buf[:idx].decode()
    buf = buf[idx+4:]
    cl = None
    for line in hdr.split('\r\n'):
        if 'content-length' in line.lower():
            cl = int(line.split(':')[1].strip())
    if cl and cl <= len(buf):
        body_data = json.loads(buf[:cl].decode())
        buf = buf[cl:]
        rid = body_data.get('id','?')
        if 'error' in body_data:
            print(f'Frame id={rid}: ERROR {body_data[\"error\"]}', flush=True)
            assert body_data['error']['code'] == -32004
        elif 'result' in body_data:
            print(f'Frame id={rid}: OK {str(body_data[\"result\"])[:80]}', flush=True)

print('E2E (Server down) PASS', flush=True)
"
```

Expected: `initialize` 正常响应，`tools/list` 返回 `-32004 Server not ready` 错误。

- [ ] **Step 4: 恢复 Server 并验证健康检查恢复**

```bash
# 重新启动 Server
./venv/Scripts/python -m uvicorn memos.dashboard:app --host 127.0.0.1 --port 8000 &
sleep 3

# 启动代理，等待 health check 成功后发 tools/list
timeout 15 ./venv/Scripts/python -c "
import subprocess, json, time

VENV = r'D:\DevSpace\MEMOS\venv\Scripts\python.exe'
CWD = r'D:\DevSpace\MEMOS'

proc = subprocess.Popen(
    [VENV, '-m', 'memos.mcp_proxy', '--http'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=CWD,
)
time.sleep(1)

# 先发 initialize
body = json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'test','version':'1.0'}}}).encode()
proc.stdin.write(f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
proc.stdin.flush()
time.sleep(4)  # 等待 health check 成功

# 再发 tools/list
body2 = json.dumps({'jsonrpc':'2.0','id':2,'method':'tools/list'}).encode()
proc.stdin.write(f'Content-Length: {len(body2)}\r\n\r\n'.encode() + body2)
proc.stdin.flush()
time.sleep(3)

proc.terminate()
stdout, stderr = proc.communicate(timeout=3)
buf = stdout
while buf:
    idx = buf.find(b'\r\n\r\n')
    if idx < 0: break
    hdr = buf[:idx].decode()
    buf = buf[idx+4:]
    cl = None
    for line in hdr.split('\r\n'):
        if 'content-length' in line.lower():
            cl = int(line.split(':')[1].strip())
    if cl and cl <= len(buf):
        body_data = json.loads(buf[:cl].decode())
        buf = buf[cl:]
        rid = body_data.get('id','?')
        if 'result' in body_data:
            r = body_data['result']
            if 'tools' in r:
                print(f'Frame id={rid}: TOOLS - {len(r[\"tools\"])} tools (health check recovery OK)', flush=True)
        elif 'error' in body_data:
            print(f'Frame id={rid}: ERROR {body_data[\"error\"]}', flush=True)
"
```

Expected: `tools/list` 返回 12 个工具（health check 恢复成功）。

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: 端到端验证 HTTP 代理 + health check 全流程"
```

---

### Task 9: 最终清理与验证

- [ ] **Step 1: 运行完整测试套件**

```bash
./venv/Scripts/python -m pytest tests/ -v -k "not real" --timeout=60
```

Expected: 全部通过。

- [ ] **Step 2: 代码风格检查**

```bash
./venv/Scripts/python -m ruff check src/memos/mcp_proxy/ src/memos/server/mcp_handler.py
```

Expected: 无警告。

- [ ] **Step 3: 验证无 `_run_stdio_proxy` 引用残留**

```bash
grep -r "_run_stdio_proxy\|_handle_tools_list\|_handle_tools_call" src/ tests/
```

Expected: 无结果。

- [ ] **Step 4: 验证 `.mcp.json` 包含 `--http`**

```bash
grep -- "--http" .mcp.json
```

Expected: 匹配到 `"--http"`。

- [ ] **Step 5: Final commit (if any remaining changes)**

```bash
git status
git add -A
git diff --cached --stat
git commit -m "chore: 最终清理，确认无 stdio 模式残留"
```
