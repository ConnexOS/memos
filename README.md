# MEMOS — 长时记忆系统

[![Python](https://img.shields.io/badge/Python-3.12+-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-lightgrey)](https://pypi.org/project/memos/)

轻量级 RAG 引擎，为 AI 编程助手提供跨对话的「记忆」能力。语义检索 + 自动提炼 + MCP 协议 + Web 仪表板。

## 目录

- [核心能力](#核心能力)
- [快速开始](#快速开始)
- [使用指南](#使用指南)
  - [Python 库](#python-库)
  - [MCP Server](#mcp-server)
  - [Web 仪表板](#web-仪表板)
  - [CLI 命令](#cli-命令)
- [架构](#架构)
- [配置](#配置)
- [开发](#开发)
- [许可证](#许可证)

## 核心能力

| 能力 | 说明 |
|------|------|
| 语义记忆 | sentence-transformers 向量编码 + ChromaDB 持久化，余弦相似度检索 |
| 混合搜索 | 向量相似度 + BM25 关键词加权融合，兼顾语义与精确匹配 |
| 时间衰减 | 记忆按时间降权，新知识优先呈现 |
| 自动提炼 | 对话累积后自动调用 LLM 提取结构化知识卡片 |
| 项目隔离 | 通过 `project_id` 实现不同项目的记忆空间隔离 |
| MCP 协议 | 基于 FastMCP 暴露 6 个工具，可被 Claude Code 等 AI 助手直接调用 |
| 对话采集 | Hook 机制自动记录每轮对话，无需手动操作 |
| Web 仪表板 | FastAPI + Jinja2，CRUD、搜索、配置管理、提示词模板管理 |

## 快速开始

### 前置条件

- Python 3.12+
- 嵌入模型（`bge-large-zh-v1.5`，1024维，首次运行自动下载至 `model/`）
- （可选）LLM 服务用于自动提炼，兼容 llama.cpp completion 与 OpenAI `/v1/chat/completions` 格式

### 安装

```bash
# 从 PyPI 安装
pip install memos

# 首次初始化（创建数据目录、下载模型、写入配置）
memos init

# 启动 MCP Server
memos server
```

### 注册到 Claude Code

```bash
# 注册 MCP Server（项目级）
claude mcp add --scope project memos -- python -m memos.server

# 注册为全局可用
claude mcp add --scope user memos -- python -m memos.server

# 一键安装对话自动采集 Hook
memos hook install
```

### 从源码安装

```bash
git clone https://github.com/laofisher/memos.git
cd memos

# 创建虚拟环境
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/macOS: source venv/bin/activate

pip install -e ".[test]"
memos init
```

## 使用指南

### Python 库

```python
from memos.memory import LongTermMemory

mem = LongTermMemory()

# 存储记忆
mem.remember("项目使用 FastAPI 框架，端口 8000", {"type": "decision"})

# 语义检索
results = mem.recall("我们用的什么后端？", top_k=3)
for r in results:
    print(r["content"], r["similarity"])

# 混合检索（向量 + BM25）
results = mem.recall("FastAPI", top_k=5, hybrid=True)

# 按类型过滤 + 时间范围
results = mem.recall("架构", type_filter="decision", days_limit=30)
```

### MCP Server

注册后，AI 助手可调用 6 个 MCP 工具：

| 工具 | 管线 | 说明 |
|------|------|------|
| `remember(text, metadata)` | A | 追加到缓冲区，累积后自动 LLM 提炼 |
| `save_knowledge(text, type)` | B | 用户指令直写知识库 |
| `recall(query, top_k, type_filter, days_limit, hybrid)` | — | 语义检索相关记忆 |
| `list_memories(type_filter, limit, offset)` | — | 分页列出当前项目所有记忆 |
| `set_project_id(pid)` | — | 切换项目隔离空间 |
| `log_complete_turn(user_message, assistant_message)` | A | 记录完整对话轮次 |

> 更新/删除/归档操作统一通过 Web 仪表板完成，减少 AI 助手 token 消耗。

### Web 仪表板

```bash
# 启动
memos dashboard

# 或开发模式（热重载）
python -m uvicorn memos.dashboard:app --host 127.0.0.1 --port 8000 --reload
```

访问 `http://127.0.0.1:8000`，功能包括：
- 记忆的 CRUD、搜索、归档与恢复
- 对话记录浏览与手动 LLM 提炼
- 系统配置可视化编辑
- 提示词模板版本管理（编辑草稿 → 升级版本 → 历史回滚）

### CLI 命令

```
memos init                    首次初始化
memos init --force            强制重新初始化
memos server                  启动 MCP Server（stdio）
memos dashboard               启动 Web 仪表板
memos status                  查看系统状态
memos doctor                  诊断系统健康度
memos config show             查看当前配置
memos config set <k> <v>      修改配置项
memos hook install            安装对话自动采集 Hook
memos hook status             查看 Hook 状态
memos prompt list             列出提示词模板
memos prompt show <id>        查看模板详情
memos prompt edit <id>        编辑模板草稿
memos prompt activate <id> <v> 切换活跃版本
```

## 架构

### 四条记忆管线

```
Pipeline A (AI 写入 → 缓冲 → LLM 提炼):   remember() / log_complete_turn() → 知识库
Pipeline B (用户指令直写):                 save_knowledge() → 知识库
Pipeline C (Hook 自动采集):               on_prompt / on_stop → 对话存档
Pipeline D (Dashboard 人工提炼):          Web UI 选对话 → LLM 提炼 → 知识卡片
```

Pipeline C 采集的对话原文作为 Pipeline D 的原料——用户在仪表板上挑选对话，触发 LLM 提炼为结构化知识卡片。

### 模块职责

| 模块 | 核心文件 | 职责 |
|------|---------|------|
| 存储引擎 | `memory.py` `store.py` `chromastore.py` | ChromaDB 封装 + 混合检索 + 时间衰减 |
| 提炼引擎 | `extractor.py` | 缓冲管理 + LLM 调用 + JSON 解析回退 |
| MCP Server | `server.py` | FastMCP，6 工具暴露 |
| Web 仪表板 | `dashboard.py` | FastAPI + Jinja2，CRUD + 配置 + 提示词管理 |

## 配置

### 配置文件

加载链：`etc/config.json` → `MEMOS_{SECTION}_{FIELD}` 环境变量覆盖。

```json
{
  "llm": {
    "endpoints": [
      {"name": "local-llm", "api_base": "http://192.168.8.12:8080/v1", "model": "gemma"},
      {"name": "deepseek-ai", "api_base": "https://api-inference.modelscope.cn/v1", "model": "deepseek-ai/DeepSeek-V4-Flash"}
    ],
    "active": "deepseek-ai"
  }
}
```

### 环境变量

```bash
# Linux/macOS
export MEMOS_LLM_ACTIVE=deepseek-ai
export MEMOS_CHROMA_PATH=/data/memdb

# Windows
set MEMOS_LLM_ACTIVE=deepseek-ai
set MEMOS_DASHBOARD_PORT=9000
```

提示词模板独立存储于 `etc/prompts/` 目录（`index.json` + 各端点子目录 + 版本快照），通过 Dashboard 或 CLI 管理。

## 开发

```bash
# 安装开发依赖
pip install -e ".[test]"
pip install ruff

# 运行全部测试（311 用例）
pytest tests/ -v

# 排除真实 LLM 依赖
pytest tests/ -v -k "not real"

# 按分组运行
pytest tests/test_integration_all.py -v -k "TestGroupA"

# 单个文件
pytest tests/test_buffer.py -v

# 代码检查
ruff check src/
ruff format src/ --check
```

### 测试分组

| 分组 | 覆盖 | 用例 |
|------|------|------|
| A (CRUD) | 增删改查、归档恢复、分页、类型过滤 | 12 |
| B (Buffer/Extract) | 缓冲累积、触发、限速、去重、截断 | 9 |
| C (MCP) | MCP 工具全链路 | 10 |
| D (Project Isolation) | 项目隔离、跨项目检索 | 6 |
| E (Hybrid/Decay) | 混合检索、时间衰减排序 | 5 |
| F (Cross-Session) | 持久化验证、多 collection 隔离 | 4 |
| G (Exceptions) | LLM 不可用、非 JSON、空文本、并发 | 6 |

### 技术栈

| 组件 | 技术 |
|------|------|
| 向量数据库 | ChromaDB（PersistentClient 本地 / HTTP 远程） |
| 嵌入模型 | bge-large-zh-v1.5（1024维，本地推理） |
| 协议层 | MCP (FastMCP)，stdio JSON-RPC |
| LLM 提炼 | 多端点切换，兼容 llama.cpp / OpenAI Chat Completions |
| 混合检索 | rank_bm25（BM25Okapi，惰性重建） |
| Web 仪表板 | FastAPI + Jinja2 |
| 配置管理 | Pydantic（8 子配置，环境变量自动覆盖） |

## 许可证

[MIT](LICENSE)
