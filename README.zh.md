# MEMOS — AI 编程助手的长时记忆系统

[![Python](https://img.shields.io/badge/Python-3.12+-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.5.1-lightgrey)](https://pypi.org/project/memomate/)

> [English Docs](README.md)

MEMOS 是为 AI 编程助手打造的轻量级 RAG 记忆系统。采用 **统一服务架构**（FastAPI 单进程），通过 **SSE MCP 协议** 和 **Token 认证** 提供服务，支持多用户、多项目数据隔离。

## 核心特性

- **🧠 跨对话记忆** — 自动提炼对话中的知识点，跨会话持久化
- **🔌 MCP via SSE** — 12 个工具，SSE 直连，令牌认证
- **🔍 混合检索** — 向量语义（1024 维）× BM25 关键词加权 + 时间衰减排序
- **📊 Web 仪表板** — 记忆浏览、搜索、编辑、项目管理
- **🏗️ 四管线架构** — AI 写入 → 缓冲提炼 / 用户直写 / 自动采集 / 人工精炼
- **🗂️ 项目 + 用户隔离** — 双字段（creator_id + scope）数据隔离
- **⚡ 客户端轻量化** — `pip install memomate`（~3MB，零 ML 依赖）
- **🔐 多用户认证** — Token 管理，Dashboard 登录

## 前置条件

- **Python 3.12+** — [下载](https://www.python.org/downloads/)
- **pip** — Python 自带

创建并激活虚拟环境（推荐）：

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

## 快速开始

### 1. 安装服务端

```bash
pip install "memomate[server]"
```

### 2. 启动统一服务

```bash
memos server
```

首次启动自动创建 admin 用户并打印 Token。浏览器访问 http://127.0.0.1:8000 打开 Dashboard。

### 3. 连接 Claude Code（客户端）

```bash
pip install memomate
memos setup --server http://<服务器地址>:8000 --token <TOKEN> --project <项目名>
```

重新加载 Claude Code，MCP 工具和 Hook 即生效。

> **Windows 用户**: 如模型下载超时，请在首次启动服务前设置镜像源：
> ```powershell
> $env:HF_ENDPOINT = "https://hf-mirror.com"
> ```

## 架构

```mermaid
graph TB
    subgraph "Claude Code (客户端)"
        CC[Claude Code]
        HOOK[Hook 代理<br/>hook_proxy]
    end

    subgraph "MEMOS 统一服务"
        direction TB
        MCP[MCP SSE<br/>/mcp/{pid}/sse]
        HAPI[Hook API<br/>/api/hooks/*]
        DASH[Dashboard<br/>/ + /api/*]
        AUTH[认证层<br/>SessionAuthStore]
        ENGINE[引擎<br/>检索 + 提炼]
        STORE[(ChromaDB)]
    end

    CC -->|SSE + Token| MCP
    HOOK -->|HTTP + Token| HAPI
    CC ---->|浏览器| DASH
    MCP --> AUTH --> ENGINE --> STORE
    HAPI --> ENGINE --> STORE
    DASH --> AUTH --> ENGINE --> STORE
```

### 目录结构

```
memos/
├── src/memos/
│   ├── config/        配置层（Pydantic 模型 + 加载链）
│   ├── storage/       存储抽象层（ChromaDB）
│   ├── engine/        核心引擎（CRUD + 提炼 + BM25）
│   ├── server/        FastAPI 统一服务（MCP Handler + SSE Wrapper）
│   ├── web/           Web 仪表板（FastAPI + Jinja2）
│   ├── cli/           CLI 入口（setup / server / user）
│   ├── features/      辅助功能（备份、日报、通知）
│   ├── hook_proxy/    Hook 代理层（认证 + project_id）
│   └── hooks/         Hook 脚本（prompt/stop）
├── memdb/             ChromaDB 持久化数据
├── model/             本地嵌入模型（约 1.3GB）
└── etc/               配置与持久化数据
```

## MCP 工具（供 AI 助手调用）

通过 SSE 协议提供 12 个工具：

| 工具 | 管线 | 说明 |
|------|------|------|
| `remember(text, metadata)` | A | 追加到缓冲区，满 5 轮自动提炼 |
| `save_knowledge(text, type)` | B | 用户明确指令直写知识库 |
| `recall(query, top_k, ...)` | — | 语义检索 + 混合检索 |
| `list_memories(type, limit)` | — | 分页列出记忆 |
| `create_todo(content, priority, due_date)` | — | 创建待办事项 |
| `list_todos(status, limit)` | — | 列出待办事项 |
| `update_todo(id, status)` | — | 更新待办状态 |
| `delete_memory(memory_id)` | — | 删除记忆 |
| `update_memory(id, text, meta)` | — | 更新记忆内容 |
| `force_extract()` | A | 强制立即提炼缓冲区 |
| `set_project_id(pid)` | — | 切换项目空间 |
| `log_complete_turn(user, asst)` | A | 记录完整对话轮次 |

## CLI 命令

| 命令 | 说明 |
|------|------|
| `server` | 启动统一服务（MCP + Dashboard + Hook） |
| `setup` | 一键初始化客户端（SSE + Hook） |
| `user add/list/remove/token-regen` | 多用户管理 |
| `status` | 查看系统状态 |
| `doctor` | 诊断系统健康度 |
| `config show / set / validate` | 配置管理 |
| `export` | 导出记忆为 JSONL |
| `import` | 从 JSONL 导入 |
| `backup / restore` | 全量备份与恢复 |
| `hook install / uninstall / status` | Hook 管理 |
| `init` | 首次初始化向导 |
| `vacuum` | 回收磁盘空间 |
| `reindex` | 重建 BM25 索引 |

## 配置

配置文件 `etc/config.json`，核心字段：

```json
{
  "llm": {
    "endpoints": [
      {"name": "default", "api_base": "http://localhost:11434/v1"}
    ],
    "active": "default"
  },
  "model": {"name": "bge-large-zh-v1.5", "vector_dim": 1024},
  "memory": {"decay_lambda": 0.02, "default_top_k": 5}
}
```

所有字段均可通过 `MEMOS_{节}_{字段}` 环境变量覆盖。

## 系统要求

- Python 3.12+
- 服务端：约 2GB 磁盘（嵌入模型约 1.3GB）
- 客户端：约 3MB，零 ML 依赖
- Windows / Linux / macOS

## 许可

MIT
