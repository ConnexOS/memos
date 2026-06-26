# SSE 令牌传递 + 客户端拆分 架构设计

> **版本**: v1.1 | **日期**: 2026-06-08 | **目标版本**: v0.5.1
> **评审**: 2026-06-08 | 10 条意见中 9 条采纳、1 条部分采纳

---

## 一、问题陈述

### 1.1 当前问题

v0.5.0 统一模式下，Claude Code 通过 SSE 直连 `/mcp/{pid}/sse`。存在三个关联问题：

| # | 问题 | 影响 |
|:-:|------|------|
| 1 | Claude Code `.mcp.json` SSE 不支持自定义 HTTP Header | `X-Auth-Token` 永远无法送达服务器 |
| 2 | `_resolve_creator_id()` 始终返回 `"unknown"` | 用户级数据隔离完全失效 |
| 3 | `memos` 包单体，`pip install` 需 750MB ML 依赖 | 开发者机器部署过重 |

### 1.2 约束

- Claude Code SSE 传输只可控 URL（路径 + query string）
- 令牌必须先由管理员在服务器上创建，再分发
- v0.5.0 仅支持项目级隔离（单用户多项目），v0.5.1 补全用户级隔离

---

## 二、架构方案

### 2.1 包拆分：可选依赖机制

不拆分源代码树，使用 Python 标准 **optional dependencies** 机制：

```
pip install memomate           → client-only (~2MB, 零 ML 依赖)
pip install "memomate[server]" → 完整服务端 (~750MB)
```

**client 层（轻量，无 ML 依赖）**：
```
memos/
├── __init__.py         # 仅导入 __version__（清理顶层重依赖）
├── _version.py
├── errors.py           # 共享异常（轻量）
├── config/             # Pydantic 配置模型（无重依赖）
│   ├── __init__.py
│   ├── models.py
│   ├── loader.py
│   └── prompts.py
├── cli/
│   ├── setup.py        ← 新增
│   └── dispatch.py
└── hook_proxy/
    ├── auth.py
    ├── project_id.py
    └── proxy.py
```

**server 层（完整服务端）**：依赖 client + 额外 server extras 依赖

**依赖清单**：

| 层级 | 依赖 | 大小 |
|:----:|------|:----:|
| 默认 (client) | `pydantic>=2.0`, `requests>=2.31` | ~3MB |
| `[server]` extras | `chromadb`, `sentence-transformers`, `mcp`, `fastapi`, `uvicorn`, `bcrypt`, `itsdangerous`, `huggingface-hub` | ~750MB |
| `[test]` extras | `pytest>=8.0` | - |

`__init__.py` 仅保留 `from memos._version import __version__`，不再顶层导入 `ContextMemory`/`MemoryExtractor`。`mcp` 和 `_detect_project_id` 通过 `__getattr__` 惰性加载。

### 2.2 项目 ID 解析：简化规则

统一使用 `.memos-project` JSON 文件，取消 git remote / CWD MD5 三条兜底链。

**文件格式**：
```json
{"id": "03e15994", "name": "SemSSE"}
```

**解析规则**：
```
--project "SemSSE" 给定？
  ├─ 是 → project_name = "SemSSE"
  │       project_id = MD5("SemSSE")[:8]
  │       强制写入 .memos-project（覆盖）
  └─ 否 → 读取 .memos-project
           ├─ 存在 → 使用文件中的 id 和 name
           └─ 不存在 → 报错，提示需指定 --project
```

`resolve_project_id()` 和 `resolve_project_name()` 均从 `.memos-project` 读取，单一数据源。推导（MD5）仅在 `--project` 指定时发生。

### 2.3 `memos setup` 一键命令

```
memos setup --server http://192.168.1.100:8000 \
            --token memo_xxx \
            --project "SemSSE"
```

#### 文件布局

| 文件 | 路径 | 生命周期 | Git 建议 |
|------|------|:--------:|:--------:|
| `.memos-project` | `CWD/.memos-project` | 项目级，随代码库 | ✅ 应入库 |
| `.mcp.json` | `CWD/.mcp.json` | 项目级 | ⚠️ 含 token，建议 `.gitignore` |
| `credentials.json` | `~/.memos/etc/credentials.json` | 用户级全局 | 不入库 |
| Hook 配置 | `CWD/.claude/settings.json` 或 `~/.claude/settings.json` | 项目或全局 | 不入库 |

不同目录有独立的 `.memos-project`，这是项目隔离的有意设计。

#### 内部步骤

| 步骤 | 操作 | 产出 |
|:----:|------|------|
| 1 | `project_id = MD5("SemSSE")[:8]`，写入 JSON | `.memos-project` |
| 2 | `save_credentials(server, token)` | `credentials.json` |
| 3 | 生成 `.mcp.json`，URL 含 `?name=SemSSE&token=memo_xxx` | `.mcp.json` |
| 4 | 安装 Hook 到 settings.json | Hook 配置 |

`.mcp.json` 示例：
```json
{
  "mcpServers": {
    "memos": {
      "type": "sse",
      "url": "http://192.168.1.100:8000/mcp/03e15994/sse?name=SemSSE&token=memo_a1b2c3d4e5f6"
    }
  }
}
```

#### 幂等语义

- 重复执行：`.memos-project` 覆盖，`credentials.json` 覆盖，`.mcp.json` 合并（只更新 memos 条目，保留其他 MCP server）
- `--project` 未提供：读 `.memos-project`，不存在则报错
- 不主动验证 server 可达性或 token 有效性（验证发生在 Claude Code SSE 连接时）

### 2.4 `mcp install` 定位

- **主要用途**：`memos setup` 的内部步骤
- **独立场景**：用户手动创建 `.memos-project` 后仅需生成 `.mcp.json`
- token 来源：从 `credentials.json` 读取，不需要额外参数

`cmd_mcp_install` 不再接受 `--project-id`/`--project-name` 参数（从 `.memos-project` 和 `credentials.json` 读取即可）。

### 2.5 SessionAuthStore：session→token 映射

SSE 令牌仅在连接 URL 中出现一次，后续 MCP 消息请求通过 session_id 关联。

#### 数据流

```
┌─ SSE 连接 ──────────────────────────────────────────┐
│ GET /mcp/{pid}/sse?token=memo_xxx&name=SemSSE       │
│   → ProjectAwareSSEWrapper 提取 token               │
│   → verify_token_against_users(token) → 有效        │
│   → 暂存 pending_token                              │
│   → 包装 send，拦截 endpoint 事件                   │
│         send_wrapper:                                │
│           if "event: endpoint" in body:              │
│             session_id = re.search(...)               │
│             _session_auth_store.put(sid, token)       │
│   → 提取 session_id → SessionAuthStore[sid] = token │
└─────────────────────────────────────────────────────┘

┌─ MCP 工具调用 ──────────────────────────────────────┐
│ POST /mcp/{pid}/messages/?session_id=abc123         │
│   → ProjectAwareSSEWrapper 提取 session_id          │
│   → token = SessionAuthStore.get(sid)               │
│   → _auth_token_ctx.set(token)                      │
│   → MCP 工具: _resolve_creator_id(from_ctx=True)    │
│   → verify_token_against_users(token) → creator_id  │
└─────────────────────────────────────────────────────┘
```

#### endpoint 事件拦截细节

`ProjectAwareSSEWrapper` 包装 ASGI `send` 回调：

```
send_wrapper(message: dict):
  case message["type"] == "http.response.body":
    text = body.decode("utf-8", errors="replace")
    if "event: endpoint" in text:
      session_id = regex(r"session_id=([a-zA-Z0-9_-]+)", text).group(1)
      _session_auth_store.put(session_id, pending_token)
  await original_send(message)
```

FastMCP SSE wire format: `event: endpoint\ndata: {path}/messages/?session_id={uuid}\n\n`。该格式自 `mcp>=1.27.0` 稳定。

#### SessionAuthStore 特性

**线程安全**：使用 `threading.Lock()`。ASGI async 任务可能运行在不同线程（uvicorn 线程池），`threading.Lock` 在 async 和 sync 上下文均可工作。基本 dict 操作在 CPython GIL 下原子，但检查+删除（惰性过期）需锁保护非原子操作。

**TTL 过期行为**：

| 阶段 | `get(sid)` 返回 | `_auth_token_ctx` | `_resolve_creator_id` 行为 |
|:--:|:---:|:---:|:---:|
| session 有效期内 | token | 设值 | 正常返回 creator_id |
| TTL 过期（30min） | None | 不设值 | 返回 `"unknown"`（静默降级） |
| session 不存在 | None | 不设值 | 同上 |

TTL 默认 30 分钟。**惰性清理**：`get()` 时检查时间戳，过期则删除，不启动后台扫描线程。后续版本可增加 401 错误响应。

#### _auth_token_ctx 设置时序

两个设置点，执行顺序明确：

```
1. InjectProjectContextMiddleware (父 app middleware 层)
   → 从 HTTP Header 设 _auth_token_ctx
   → SSE 请求无 X-Auth-Token Header → 不设值

2. ProjectAwareSSEWrapper (mount 子 app 层，后运行)
   → 消息请求时从 SessionAuthStore 查 token 并设值
   → 覆盖 middleware 可能设的值（如有）
```

Starlette mount 运行在 middleware 链之后，wrapper 的 ContextVar 设置值是最终值。无时序碰撞风险。

---

## 三、安全考虑

| 风险 | 说明 | 缓解 |
|:----:|------|------|
| token 在 `.mcp.json` 明文 | `.mcp.json` 可能提交到 git | 文档建议 `.gitignore` 排除 `.mcp.json`；token 以 `memo_` 前缀可被 git hooks 检测 |
| token 在 URL query string | 服务器 access log 可能记录 | 服务器端应脱敏 URL query 中的 token（日志配置层过滤） |
| 中间人窃听 | token 在传输中暴露 | 生产环境强制 HTTPS；局域网场景风险可控 |

---

## 四、改动清单

| 层面 | 文件 | 改动类型 | 说明 |
|------|------|:--------:|------|
| 打包 | `pyproject.toml` | 重构 | 依赖拆分为默认 + `[server]` extras |
| Client | `memos/__init__.py` | 修改 | 清理顶层重依赖导入，仅保留 `__version__` + lazy loader |
| Client | `hook_proxy/project_id.py` | 重写 | `.memos-project` JSON 单一来源，移除三条兜底链 |
| Client | `cli/setup.py` | **新增** | `memos setup` 一键配置命令（合并 login + mcp + hook） |
| Client | `cli/dispatch.py` | 修改 | `cmd_mcp_install` 从 credentials 读取 token；wiring setup |
| Server | `server/mcp.py` | 新增 | `SessionAuthStore` 类（`threading.Lock` + 惰性 TTL） |
| Server | `server/sse_wrapper.py` | 修改 | token 提取 + `send_wrapper` 拦截 endpoint + messages 查 SessionAuthStore |

---

## 五、测试策略

| 测试对象 | 级别 | 关键场景 |
|:--------:|:----:|----------|
| `SessionAuthStore` | 单元 | put/get、TTL 过期、并发安全、cleanup |
| `resolve_project_id/name` | 单元 | JSON 读取、文件不存在异常、缓存命中 |
| `memos setup` | CLI 集成 | 文件生成、幂等性、参数缺失报错 |
| SSE wrapper | 集成 | project_id 提取、token 拦截、session 映射 |
| 回归 | 全量 | 597 现有测试全部 PASS |

实施计划中每个任务含测试步骤和代码。

---

## 六、数据流验证

### 6.1 管理员初始化
```
[服务器] memos user add alice → Token: memo_xxx
```

### 6.2 开发者配置
```
[开发机] memos setup --server http://192.168.1.100:8000 --token memo_xxx --project "SemSSE"
         → .memos-project / .mcp.json / credentials.json / Hook 全部就绪
```

### 6.3 Claude Code 调用
```
recall("技术决策")
  → 请求携带 sid → SessionAuthStore 查 token → creator_id="alice"
  → scope="personal" 过滤 → 只看到 alice 自建数据
  → scope="team" 不过滤 → 同项目所有用户可见
```

### 6.4 Hook 采集
```
hook_proxy --hook
  → resolve_project_id() 读 .memos-project → project_id
  → load_credentials() → server_url + token
  → POST /api/hooks/prompt 携带 X-Memos-Project-Id + X-Auth-Token
```

---

## 七、迁移考虑

- 现有项目需创建 `.memos-project` 文件：`{"id": "d0ff92fa", "name": "MEMOS"}`
- `pip install "memomate[server]"` 兼容现有开发环境
- 测试环境中 `.memos-project` 文件由测试夹具管理
- `resolve_project_id()` 调用方需适配：未找到文件时返回错误而非兜底值

---

## 八、不在范围内的内容

- 密码登录（`POST /api/auth/login`）— 独立特性，单独设计
- Dashboard 的 project_name 展示优化 — 已有机制可用
- `memos migrate` 自动创建 `.memos-project` — v0.5.0 已处理
- Session 过期返回 401 — 后续版本改进
