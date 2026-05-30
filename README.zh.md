# MEMOS — AI 编程助手的长时记忆系统

[![Python](https://img.shields.io/badge/Python-3.12+-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.4.6-lightgrey)](https://pypi.org/project/memomate/)

> [English Docs](README.md)

MEMOS 是为 AI 编程助手打造的轻量级 RAG 记忆系统。它能在对话中自动捕获技术决策、Bug 修复、用户偏好等信息，并在后续对话中智能召回——让 AI 助手真正"记住"你的项目。

## 核心特性

- **🧠 跨对话记忆** — 自动提炼对话中的知识点，跨会话持久化
- **🔌 MCP 协议** — 11 个工具，无缝集成 Claude Code 等 AI 助手
- **🔍 混合检索** — 向量语义（1024 维）× BM25 关键词加权 + 时间衰减排序
- **📊 Web 仪表板** — 记忆浏览、搜索、编辑、配置管理
- **🏗️ 四管线架构** — AI 写入 → 缓冲提炼 / 用户直写 / 自动采集 / 人工精炼
- **🗂️ 项目隔离** — 按工作目录自动隔离，多项目互不干扰
- **⚡ 零外部依赖** — 纯本地运行，单进程，无需数据库或云服务

## 快速开始

```bash
pip install memomate
memos init --force
memos dashboard
```

浏览器访问 http://127.0.0.1:8000

> **Windows 用户**: 如模型下载超时，请在 `memos init` 前设置镜像源：
> ```powershell
> $env:HF_ENDPOINT = "https://hf-mirror.com"
> ```

### 集成到 Claude Code

```bash
memos hook install
```

完成后 Claude Code 会在对话中自动读写记忆。

## 应用场景

| 场景 | 效果 |
|------|------|
| 记住技术选型 | "我们用的是 FastAPI + SQLAlchemy" — 下次对话自动回忆 |
| 追踪 Bug 修复 | 修复方案自动提炼为知识卡片，同类型问题不再重复排查 |
| 统一代码风格 | 记录项目的编码约定和命名规范，AI 生成代码自动遵循 |
| 跨会话上下文 | 即使新建对话，AI 助理仍记得项目的技术背景和决策历史 |
| 团队知识沉淀 | 多人维护同一项目时，记忆共享，减少重复沟通 |

## MCP 工具（供 AI 助手调用）

| 工具 | 管线 | 说明 |
|------|------|------|
| `remember(text, metadata)` | A | 追加到缓冲区，满 5 轮自动提炼 |
| `save_knowledge(text, type)` | B | 用户明确指令直写知识库 |
| `recall(query, top_k, ...)` | — | 语义检索 + 混合检索 |
| `list_memories(type, limit)` | — | 分页列出记忆 |
| `list_todos(status, limit)` | — | 列出待办事项 |
| `update_todo(id, status)` | — | 更新待办状态 |
| `delete_memory(memory_id)` | — | 删除记忆 |
| `update_memory(id, text, meta)` | — | 更新记忆内容 |
| `force_extract()` | A | 强制立即提炼缓冲区 |
| `set_project_id(pid)` | — | 切换项目空间 |
| `log_complete_turn(user, asst)` | A | 记录完整对话轮次 |

## 架构

```
AI 助手 ──MCP stdio──→ MEMOS Server
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          知识库存储    混合检索引擎   Web 仪表板
        (ChromaDB)   (Vector+BM25)  (FastAPI+Jinja2)
              │            │
              ▼            ▼
          嵌入模型      BM25 索引
    (bge-large-zh-v1.5)  (rank_bm25)
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `init` | 首次初始化向导 |
| `dashboard` | 启动 Web 面板 |
| `server` | 启动 MCP Server |
| `status` | 查看系统状态 |
| `doctor` | 诊断系统健康度 |
| `config show / set / validate` | 配置管理 |
| `export` | 导出记忆为 JSONL |
| `import` | 从 JSONL 导入 |
| `backup / restore` | 全量备份与恢复 |
| `hook install / uninstall / status` | Hook 管理 |
| `auth regen` | 重新生成访问令牌 |
| `vacuum` | 回收磁盘空间 |
| `reindex` | 重建 BM25 索引 |

## 配置

配置文件位于 `etc/config.json`，核心字段：

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

所有字段均可通过 `MEMOS_{节}_{字段}` 环境变量覆盖，无需直接编辑 JSON。

## 系统要求

- Python 3.12+
- 约 2GB 磁盘空间（嵌入模型约 1.3GB）
- Windows / Linux / macOS

## 许可

MIT
