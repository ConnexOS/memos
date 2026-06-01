# CLAUDE.md

**记忆的本质**：`复刻过去是为了更好地服务未来`

## 项目概述

主动式记忆系统（MemoMate），包名 `memos`。专门为 AI 编程助手打造记忆伙伴，提供跨对话「记忆」能力。
当前版本 `v0.4.8`。8 子包分层架构：config/ → storage/ → engine/ → server/ + web/ + cli/ + features/ + hooks/。

## 技术栈

Python 3.12 | ChromaDB (PersistentClient) | bge-large-zh-v1.5 (1024维) | MCP (FastMCP, stdio) | FastAPI + Jinja2
LLM 多端点支持，OpenAI chat/completions 格式。混合检索：rank_bm25 + 向量加权融合。
测试 pytest (52+文件)，代码风格 ruff (line-length=120)。

## 核心架构

### 四条记忆管线

- **A**: `remember()` MCP → 缓冲区 → LLM 提炼 → 知识库
- **B**: `save_knowledge()` MCP → 直达知识库
- **C**: Hook 自动采集对话 → ChromaDB (user_input / assistant_output)
- **D**: Dashboard 人工选对话 → LLM 提炼 → 知识卡片

知识类型（共 7 种）：A+B 管线用 `fact`/`decision`/`preference`，D 管线用 `bug_fix`/`feature_design`/`code_optimize`/`tech_knowledge`。`todo` 已从知识类型中移除，待办请用 `create_todo` MCP 工具。

### 模块职责

| 模块 | 路径 | 职责 |
|------|------|------|
| 存储引擎 | `storage/` | ChromaDB + SentenceTransformer，CRUD + 混合检索 + 时间衰减 |
| 提炼引擎 | `engine/extractor.py` | 缓冲管理（5轮触发），LLM 调用 + JSON 三级回退 + 去重 |
| MCP Server | `server/mcp.py` | FastMCP，12 工具（含 create_todo） |
| Web 仪表板 | `web/` | FastAPI + Jinja2，routes/models/services 三层 |
| 配置系统 | `config/` | models.py(10子配置) + prompts.py + loader.py |
| 辅助功能 | `features/` | backup + daily_review + usage + notifications + wizard |
| Hook | `hooks/` | prompt.py + stop.py，对话自动采集 |

### 关键设计

- **抽象存储**: `VectorStore`(ABC) → `ChromaDBPersistentStore` / `ChromaDBHttpStore`，`create_store()` 工厂
- **集中配置**: `MemoConfig`(Pydantic, 10子配置)，加载链 `etc/config.json` → `MEMOS_{SECTION}_{FIELD}` 环境变量覆盖
- **JSON 回退链**: extractor 3级 → dashboard 4级，含 `<think>` 推理块剥离
- **Hook 架构**: `.claude/settings.json` → `SAFETENSORS_FAST_LOAD=0 python -m memos.hooks.prompt` → ChromaDB
- **分层检索**: Layer1 (sim≥0.55) 注入上下文, Layer2 (sim≥0.75) 写入主动建议，三重闸门控制

### 目录结构

```
D:/DevSpace/MEMOS/
├── src/memos/           # 核心源码（8 子包）
│   ├── config/           配置层 (models + prompts + loader)
│   ├── storage/          存储抽象层 (base + chroma + embeddings)
│   ├── engine/           核心引擎 (memory + extractor + review)
│   ├── server/           MCP 服务层
│   ├── web/              Web 仪表板 (app + auth + routes/ + templates/)
│   ├── cli/              CLI 入口
│   ├── features/         辅助功能 (backup + usage + notifications + wizard)
│   └── hooks/            Hook 脚本 (prompt.py + stop.py)
├── tests/               测试 (pytest, 52+ 文件)
├── etc/                 配置与持久化 (config.json + prompts/ + usage_log.jsonl)
├── scripts/             辅助脚本 (smoke_test, benchmark, backup 等)
├── docs/                参考文档 (MCP工具、CLI、API、测试、排障)
├── document/            开发阶段文档 + 日报 + ADR
├── memdb/               ChromaDB 持久化数据
├── model/               本地嵌入模型 (bge-large-zh-v1.5)
└── pyproject.toml       构建配置
```

## 记忆行为准则（始终生效）

- **自动记住**: 遇到技术决策、项目约定、用户偏好时，调 `remember(text, metadata)` 或 `save_knowledge(text, type)`，metadata.type 支持 7 种：`fact`/`decision`/`preference`/`bug_fix`/`feature_design`/`code_optimize`/`tech_knowledge`。不记：寒暄、临时调试、常识、用户说"不用记"。
- **创建待办**: 需要跟踪的待办事项，调 `create_todo(content, priority, due_date)`。
- **检索先于决策**: 开始新任务或做技术选型前，先 `recall(query)` 检索历史记忆。
- **记录对话轮次**: 重要讨论结束后调 `log_complete_turn(user_message, assistant_message)` 存档。

## 关键约定

- **去重阈值**: 按向量维度自适应 — 1024维=0.55, 384维=0.65（`_THRESHOLD_MAP`），未知维度回退 `config.memory.similarity_threshold`
- **项目隔离**: ChromaDB `where.project_id` 过滤，MCP `set_project_id` 切换
- **BM25 惰性重建**: 写入 `_invalidate_bm25()` 失效，查询 `_ensure_bm25_index()` 懒加载
- **ChromaDB 锁**: 严禁 MCP Server 和 Dashboard 同时对同一项目写入（SQLite 文件级锁）
- **Hook 安装**: `memos hook install` 写入 `SAFETENSORS_FAST_LOAD=0 python -m memos.hooks.prompt` 命令（Windows 必需）
- **Stop Hook 幂等**: `pending_assistant=false` 标记，防止重复写入
- **缓存**: 系统状态 15s TTL，项目列表 30s TTL
- **嵌入模型**: 本地 `./model/bge-large-zh-v1.5`，无需联网

## 常用命令

```powershell
# 测试
.\venv\Scripts\python -m pytest tests/ -v
.\venv\Scripts\python -m pytest tests/ -v -k "not real"

# Lint / Format
.\venv\Scripts\python -m ruff check src/
.\venv\Scripts\python -m ruff format src/

# 启动 Dashboard
.\venv\Scripts\python -m uvicorn memos.dashboard:app --host 127.0.0.1 --port 8000 --reload

# CLI 常用
.\venv\Scripts\python -m memos.cli status
.\venv\Scripts\python -m memos.cli doctor
.\venv\Scripts\python -m memos.cli today
```

## Windows 终端编码陷阱

**现象**：Python 输出中文文本时显示乱码（如 `��ʱ����`），容易误判为功能异常。

**根因**：Windows 终端默认 GBK（cp936）编码，Python 输出 UTF-8 文本时不匹配，导致显示乱码。ChromaDB 中存储的数据本身是完整的 UTF-8，终端显示问题不影响数据完整性。

**规避**：
- 怀疑 recall/MCP 输出异常时，先确认是否为终端编码问题：输出到文件而非终端
  ```bash
  python -c "..." > output.txt  # 文件用 UTF-8 打开即可
  ```
- 或在 Python 命令中显式控制编码：`.encode('utf-8').decode('utf-8')`
- 中文字符串比较时避免依赖终端显示结果

## 参考索引

| 文档 | 内容 | 路径 |
|------|------|------|
| MCP 工具详情 | 12 个工具参数 + 管线说明 | `docs/mcp-tools.md` |
| CLI 命令参考 | 完整命令行参考 | `docs/cli-commands.md` |
| API 端点参考 | Dashboard 全部 API | `docs/api-reference.md` |
| 测试基础设施 | 分组 + Fixture + 模式 | `docs/test-infrastructure.md` |
| 故障排查 | 常见问题 + 解决方案 | `docs/troubleshooting.md` |
| 版本特性 | v0.4.x 新特性汇总 | `docs/v0.4.x-features.md` |
