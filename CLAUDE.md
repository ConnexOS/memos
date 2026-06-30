# CLAUDE.md

**记忆的本质**：复刻过去是为了更好地服务未来

---

## 一、工作指令（每会话必读）

> 本项目配置了跨会话长时记忆（`memos MCP`）。以下规则**AI 助手必须严格执行**。

### 1.1 写操作

| 触发场景 | 调用 | type |
|---------|------|------|
| 解决了报错，有明确错误信息 + 修复步骤 | `save_knowledge(text, type)` | `solution` |
| 做出了技术选型/架构决策，有选项对比和理由 | `save_knowledge(text, type)` | `decision` |
| 完成了里程碑/重构/大修，有可沉淀的经验 | `save_knowledge(text, type)` | `lesson` |
| 用户说"记住这个流程/规范/操作步骤" | `save_knowledge(text, type)` | `process` |
| 用户说"记住/保存/记下来"（未明确类型） | `save_knowledge(text)` | 省略，系统推断 |
| 对话中出现需后续关注但不紧急的信息 | `remember(text)` | —（写入 watchlist） |

**核心原则**：判断权在你——只有你确认有价值再写。宁少写不错写。

**跳过**：寒暄、临时调试值、编程常识、用户说"不用记"。

### 1.2 创建待办

触发词："记得做"、"回头处理"、"先放着"、"提一个 issue"、"todo"。

`create_todo(content, priority, due_date)`

| priority | 判断标准 |
|----------|---------|
| `high` | 阻塞当前工作流、有明确截止时间且紧迫 |
| `medium` | 需要做但不紧急（**默认**） |
| `low` | 锦上添花、无时间压力 |

`due_date` 格式 `YYYY-MM-DD`，不确定则不传。

### 1.3 检索操作

收到涉及技术选型、架构决策、业务规则、Bug 根因分析的请求时：提炼 2-3 个核心关键词，调 `recall(query, top_k=5, hybrid=true)`。记忆与代码不一致时以代码为准，并用 `update_memory()` 更新过期记忆。

### 1.4 维护操作

| 场景 | 操作 |
|------|------|
| 记忆内容过时/不准确 | `update_memory(memory_id, text)` |
| 记忆完全错误 | `delete_memory(memory_id)` |
| 查看存量 | `list_memories(type_filter="decision", limit=20)` |
| 待办状态变更 | `update_todo(memory_id, todo_status)`（pending/in_progress/completed/cancelled）|

### 1.5 主动写入引导

上述 1.1 表中的场景（报错解决/技术决策/里程碑/用户指令）出现时，**主动**调用 `save_knowledge`，不要等用户说"记住"。判断权在你——宁可漏写也不错写。

---

## 二、项目速览

**定位**：MemoMate 是 Claude Code 的长期记忆层——自动记录、主动注入、Dashboard 可视化。

**当前版本**：v0.7.2「收官」（代号：Finale）。v0.7.X 系列最终版本——覆盖信号枢纽（统一收件箱）、遗留功能完善、候选品质打磨三个维度。

### 五层架构

```
L5 行为层（Insight）      Dashboard 六面板 — 记忆行为轨迹 + 修正入口
L4 交互层（Interaction）  todo / manual_suggestion / daily_report
L3 知识层（Knowledge）    solution / decision / lesson / process — Claude 自写
L2 上下文层（Context）    task（会话级，四态: pending/active/completed/archived）/ briefing（日频）
L1 原始记录层（Raw）      Hook 自动采集 user_input / assistant_output
```

### 知识类型

| 层 | 类型 | 生命周期 | 写者 | 注入时机 |
|----|------|---------|------|---------|
| L2 | **task** | 会话级（四态：pending→active→completed→archived） | Claude 自评 + 人类追溯 | 每次会话开始，最高优先级 |
| L2 | **briefing** | 24h 过期 | MEMOS LLM（定时/兜底/手动） | 跨天首次会话 |
| L3 | **solution** | 长期有效 | Claude 自写 | 检测到相似错误描述时 |
| L3 | **decision** | 长期有效 | Claude 自写 | 讨论相关主题时 |
| L3 | **lesson** | 中期有效（90 天 TTL） | Claude 自写 | 进入类似工作场景时 |
| L3 | **process** | 长期稳定 | Claude 自写（用户指令触发） | 涉及对应操作环节时 |
| L5 | **watchlist** | 30 天未处理自动归档 | `remember()` MCP | L5「待关注」面板展示 |

### v0.7.2 关键变更

- **收件箱全页（/inbox）**：三区聚合布局（系统通知 / 待关注 / 待修正），30s 未读轮询，全部已读与单条忽略，action→按钮动态映射
- **通知类型去硬编码**：`get_unread_counts()` 动态 JSONL 统计，新增 quality_alert / ttl_warning / watchlist_update / dedup_failed 类型
- **MCP 去重优化**：`save_knowledge()` 异步 LLM 判断（30s 超时），按类型差异化策略（solution/decision/lesson/process），MCP 立即返回不阻塞
- **手工提炼 prompt 更新**：新 4 类体系（solution/decision/lesson/process），`default@extract` 模板同步更新
- **任务审计弹窗**：`/api/tasks/audit` 端点，日期选择 + done 项时间线展示
- **今日回顾右侧栏**：历史日报列表（日期+首行预览），点击加载完整日报
- **统计卡修复**：`/api/v2/stats/pending-archive` 真实计算 forgotten+超期记忆数
- **导航重构**：顶栏「🔔收件箱」入口，原待关注/待修正面板迁入收件箱
- **通知 Badge 泛化**：从后端动态渲染通知类型
- **安全加固**：认证审查、API 鉴权覆盖、输入过滤、敏感信息遮蔽

---

## 三、技术栈

Python 3.12 | ChromaDB (PersistentClient) | bge-large-zh-v1.5 (1024维) | MCP (FastMCP, stdio) | FastAPI + Jinja2

LLM 多端点支持，OpenAI chat/completions 格式。混合检索：rank_bm25 + 向量加权融合。测试 pytest（52+ 文件），代码风格 ruff (line-length=120)。

---

## 四、常用命令

```powershell
# 开发安装
pip install -e .

# 测试
.\venv\Scripts\python -m pytest tests/ -v
.\venv\Scripts\python -m pytest tests/ -v -k "not real"

# 覆盖率
.\venv\Scripts\python -m pytest tests/ --cov=src/memos --cov-report=term

# Lint / Format
.\venv\Scripts\python -m ruff check --fix src/
.\venv\Scripts\python -m ruff format src/

# 启动 Dashboard
.\venv\Scripts\python -m uvicorn memos.dashboard:app --host 127.0.0.1 --port 8000 --reload

# CLI 常用
.\venv\Scripts\python -m memos.cli status
.\venv\Scripts\python -m memos.cli doctor
.\venv\Scripts\python -m memos.cli today
.\venv\Scripts\python -m memos.cli config show
.\venv\Scripts\python -m memos.cli hook status
.\venv\Scripts\python -m memos.cli reindex --batch-size 500
.\venv\Scripts\python -m memos.migrate types --dry-run    # 旧 7 类迁移预览
```

---

## 五、架构细节

### 管线

| 管线 | 作用 | 状态 |
|:----:|------|:----:|
| **A** | `remember()` → watchlist 直写 ChromaDB | 已交付 |
| **B** | `save_knowledge()` → L3 知识层，Claude Code 自写主路径 | 已交付 |
| **C** | Hook 自动采集 → L1 原始记录层 | 已交付 |
| **D** | Dashboard 手工提炼 + task 追溯，选中对话记录→MEMOS LLM 加工 | 已交付 |
| **E** | 活动日志采集 → `etc/activity_log.jsonl`，L5「记忆流」数据源 | 已交付 |
| **F** | Dashboard SchedulerThread + Hook lazy 兜底 → briefing 定时生成 | 已交付 |

### 模块结构

```
src/memos/
├── config/      配置（Pydantic，15 子配置） → models.py / loader.py / prompts.py
├── storage/     存储抽象（base → chroma / embeddings）
├── engine/      核心引擎（memory / extractor / review）
├── server/      MCP + SSE + HookHandler + TaskHandler
├── web/         Dashboard（FastAPI + Jinja2，routes / templates / models）
├── dashboard/   Uvicorn 启动入口 + SchedulerThread
├── cli/         CLI 命令 + 数据迁移
├── features/    活动日志 / scheduler / backup / 通知 / wizard
├── hooks/       prompt.py（L2+L3 注入）+ stop.py（task 自评采集）
└── hook_proxy/  SSE/stdio 代理 + auth + project_id
```

### 关键约定

- **去重阈值**：1024维 → 0.55，384维 → 0.65，未知维度回退 `config.memory.similarity_threshold`
- **项目隔离**：ChromaDB `where.project_id`，MCP `set_project_id` 切换
- **BM25 惰性重建**：写入 `_invalidate_bm25()`，查询 `_ensure_bm25_index()` 懒加载
- **ChromaDB 锁**：严禁 MCP Server 和 Dashboard 同时对同一项目写入
- **Hook 安装**：`memos hook install --unified` → Windows 需 `SAFETENSORS_FAST_LOAD=0`
- **Stop Hook 幂等**：`pending_assistant=false` 防止重复写入
- **缓存**：系统状态 15s TTL，项目列表 30s TTL
- **嵌入模型**：本地 `./model/bge-large-zh-v1.5`
- **活动日志**：`etc/activity_log_YYYY-MM-DD.jsonl`，按天轮转保留 30 天
- **TTL 首次扫描保护**：24h 宽限期内仅记录不执行
- **配置向前兼容**：缺失新配置节时 Pydantic `Field(default=...)` 自动补全
- **MEMOS LLM 职责边界**：只做结构化加工（task 自评 / briefing / 手工提炼），不做被动扫描

---

## 六、参考索引

| 文档 | 内容 |
|------|------|
| `document/72版本/MEMOS-v0.7.2-需求规格说明书.md` | v0.7.2 完整需求规格（收件箱/去重/审计/安全） |
| `document/72版本/MEMOS-v0.7.2-产品设计定义.md` | v0.7.2 产品设计定义 — 头脑风暴决议 |
| `document/72版本/MEMOS-v0.7.2-实施计划.md` | v0.7.2 三阶段实施计划 |
| `document/71版本/MEMOS-v0.7.1-需求规格说明书.md` | v0.7.1 完整需求规格（TTL / 导航 / UX / API） |
| `document/71版本/MEMOS-v0.7.1-实施计划.md` | v0.7.1 三阶段实施计划 |
| `document/71版本/MEMOS-v0.7.1-Task管理架构方案.md` | Task 四态管理 + 时序追溯设计 |
| `document/产品/MEMOS-产品设计定义.md` | 五层架构 + 全生命周期 + 核心约束 |
| `document/产品/MEMOS-知识分类体系-重构方案.md` | 6 类分类体系重构设计 |
| `document/产品/MEMOS-记忆元数据治理方案.md` | 三层分离 status 模型 + 旧 7 类迁移 |
| `document/产品/MEMOS-v0.6.0至v1.0.0版本路线图.md` | 版本路线 |
| `docs/mcp-tools.md` | MCP 工具参数 + 管线说明 |
| `docs/cli-commands.md` | 完整 CLI 参考 |
| `docs/api-reference.md` | Dashboard API 端点 |
| `docs/test-infrastructure.md` | 测试分组 + Fixture + 模式 |
| `docs/troubleshooting.md` | 故障排查指南 |
