# Changelog

## [0.5.1] - 2026-06-09

### 统一服务器架构
- 单进程 FastAPI 统一服务（MCP SSE + Dashboard + Hook 三合一）
- SSE 直连协议，移除 MCP 薄代理
- SessionAuthStore session→token 映射解决 SSE 无自定义 Header 问题
- ProjectAwareSSEWrapper 从 URL 提取 project_id + token

### 身份与安全
- 多用户 Token 认证系统（bcrypt + users.json）
- Dashboard login/logout 页面
- 首次启动自动创建 admin 用户

### 数据隔离
- creator_id + scope 双字段用户级数据隔离
- 3 种隔离级别：team / personal / 混合

### 客户端轻量化
- 依赖拆分为默认 + [server] extras（默认 ~3MB，零 ML 依赖）
- `memos setup` 一键配置命令（login + mcp + hook 合并）
- `.memos-project` JSON 单一数据源，移除三条兜底链
- `__init__.py` 清理顶层重导入，重型模块惰性加载

### CLI 增强
- `memos setup`：一键初始化客户端
- `memos server`：启动统一服务
- `memos user add/list/remove/token-regen`：用户管理
- `memos migrate --to-unified`：v0.4.x 升级迁移入口

### 开发基础设施
- contextvars 替代 threading.local，异步安全
- E2E 测试框架 + 数据隔离测试
- `scripts/release_exclude.txt` 发布排除清单

## [0.5.0] - 2026-06-05

### 统一服务器架构
- 新增 FastAPI 统一进程（Dashboard + MCP + Hook 三合一）
- 支持 unified/legacy 双模式，零成本降级
- 新增 MCP 薄代理子包，stdio→HTTP 转发

### 身份与安全
- 多用户 Token 认证系统（bcrypt + users.json）
- login/logout API 端点
- 首次启动自动创建 admin 用户

### 数据隔离
- creator_id + scope 双字段数据隔离
- 3 种隔离级别：team / personal / 混合

### 开发体验
- contextvars 替代 threading.local，异步安全
- CLI 增强：mcp/user/login/logout/migrate 子命令
- E2E 测试框架 + 数据隔离测试

---

## v0.4.9 "项目级管理" — 2026-06-03

### Added

- **项目管理对话框**
  - Dashboard 新增"管理"按钮，打开全项目列表视图
  - 展示项目名称、ID、按类型分布（badge 形式）、操作列
  - 当前项目置顶，其余按活跃时间倒序

- **项目数据删除**
  - `DELETE /api/projects/{pid}` 端点，幂等删除指定项目全部数据
  - `GET /api/projects/{pid}/stats` 端点，查询项目数据分布概览
  - 删除前输入项目名二次确认，不可撤销

- **默认项目持久化**
  - 项目切换自动保存到 localStorage
  - 页面刷新按 localStorage → CWD → 第一个项目 优先级自动选中

### Changed

- **备份删除体验改进**
  - 原生 `confirm()` 替换为 Bootstrap 模态框确认
  - 删除按钮增加 loading spinner 动画，防止重复点击
  - 与项目管理删除流程一致的交互体验

- **项目列表接口增强**
  - `GET /api/projects` 返回每个项目的 `by_type` 字段，前端一次查询即可获取全部分布数据

### Fixed

- **项目删除端点 limit 截断**
  - `DELETE /api/projects/{pid}` 和 `GET /api/projects/{pid}/stats` 移除 `limit=10000` 硬限制
  - 修复：数据量超过 1 万条时删除不完整、统计不准确的问题

### Internal

- `_get_projects_from_db()` 增加 `by_type` 计数器
- ruff 格式检查和修复（4 文件）

## v0.4.8 "稳健维护" — 2026-05-31

### Added

- **`memos reindex` 命令**
  - 三阶段：重建向量索引 → 重建 BM25 索引 → VACUUM
  - 支持 persistent 和 http 两种 ChromaDB 模式
  - 分批导出（500 条/批 + limit/offset），防止大数据量静默截断

- **ChromaDB 操作级重试机制**
  - `_col_call()` 指数退避重试（最多 2 次，0.2s/0.5s），覆盖所有 ChromaDB 操作
  - 解决多进程并发写入导致的 HNSW 索引与 embeddings 数据表不同步问题

### Changed

- **Project ID 设计重构**
  - 移除 `MemoryConfig.default_project_id` 配置项（运行时无人读取该字段，且所有组件独立计算 `md5(CWD)[:8]`，保留在配置中误导用户）
  - `engine/memory.py`、`engine/extractor.py`、`hooks/prompt.py` 中的 project_id fallback 改为运行时计算
  - 前端 Dashboard.js 移除对应配置渲染

### Fixed

- **主动建议面板统计数 80 但列表为空**
  - 根因：`routes/suggestions.py:172` 收集 `source_memory_id` 时列表推导式未去重，多个建议引用同一源记忆导致 ChromaDB `get()` 抛出 `DuplicateIDError`
  - 修复：改为集合推导式去重

- **对话搜索降级**
  - 根因：ChromaDB 并发写致向量索引异常，异常被静默吞掉
  - 修复：向量搜索报 `ChromaDBError` 时自动降级为关键词 LIKE 搜索

- **`save_knowledge` 异常区分**
  - 区分 `ChromaDBError`（数据库异常）与一般异常
  - ChromaDBError 时返回警告提示用户运行 `memos doctor` 诊断

- **提示词模板 Bug 三连修复**
  - 模板列表默认端点只显示提取模板，不显示今日回顾 → 改为按 `(端点, 类型)` 二元组去重
  - 虚拟模板"升级为新版本"报 404 → 解析 `ep@type` 格式，创建后用新 ID 发起升级
  - 新建提示词模板误报"已存在" → 增加 `!t.is_virtual` 过滤

- **安装向导硬编码 `python` 命令**
  - Windows venv 环境下 `"python"` 解析到全局 Python（缺少 memos 包）
  - 改为 `sys.executable`，Windows 适配 `SAFETENSORS_FAST_LOAD=0` 环境变量

- **`reindex` 数据完整性**
  - `get()` 未分批导出导致大数据量静默截断
  - 改为 500 条/批 + limit/offset 分批导出

- **MCP 作用域冲突**
  - 移除 user 作用域的 memos MCP 配置，保留 project 作用域

### Removed

- `MemoryConfig.default_project_id` 配置项
- 清理 8628 个 `test_*` 残留 collection（`memos vacuum --purge-test`），数据库 319MB → 14.5MB

---

## v0.4.7 "修补版" — 2026-05-31

### Fixed

- **PyPI 打包遗漏 web/templates/static 目录**
  - 根因：`pyproject.toml` 的 `package-data` 中 glob 模式 `templates/*` 不递归，且 key 写错为 `memos` 而非 `"memos.web"`
  - 修复：key 改为 `"memos.web"`，glob 改为 `templates/**/*`

- **Dashboard 版本号不一致**
  - Dashboard 启动日志显示"模块化架构 v0.4.3"与当前版本号不一致
  - 修复：`web/app.py` 中版本号改为从 `memos.__version__` 动态读取

---

## v0.4.6 "产品化" — 2026-05-30

### Added

- **M4: i18n 国际化基础设施**
  - `src/memos/i18n.py` — `Translator` 类 + `get_translator()` 工厂 + `_()` 简写函数
  - `etc/locales/{zh,en}.json` — 50+ UI 字符串中英文翻译
  - Jinja2 全局注册 `_()` 函数，模板可直接调用 `{{ _('key') }}`
  - 导航栏语言切换按钮，设置持久化（`DashboardConfig.locale`）

- **M3: 发布素材准备**
  - `.github/workflows/ci.yml` — lint + test 双 job CI
  - `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.md` + `PULL_REQUEST_TEMPLATE.md`
  - `README.md`（英文）+ `README.zh.md`（中文）完全重写，含 Mermaid 架构图、MCP 11 工具表、快速指南
  - `install.ps1` / `install.sh` — 一键安装脚本，支持镜像源
  - `scripts/sync-to-github.ps1` — SVN→GitHub 筛选同步脚本

- **M7: 初始化向导增强**
  - 环境检测步骤新增 `huggingface-hub` 依赖检查
  - 模型选择步骤检测已有 ChromaDB collection 维度，不匹配时给出警告

- **M10: 跨平台兼容性**
  - `OMP_NUM_THREADS` / `MKL_NUM_THREADS` / `SAFETENSORS_FAST_LOAD` 仅在 Windows 设置
  - `memos doctor` safetensors 检查仅 Windows 执行

### Changed

- **M8: 设置面板重构**
  - Tab 布局 → 三级折叠卡片：端点管理(P0) / 基础设置(P1) / 高级设置(P2)
  - system_suggestion 独立 Tab 移除，整合至高级设置区
  - 仅 `llm` 节为 schema 必选，其余可选

- **M2: 反馈字段合并**
  - `useful_feedback_count` / `not_useful_feedback_count` → 统一 `reuse_count`
  - 排序加成公式：`math.log2(reuse_count + 1) * 0.15`
  - 建议卡片显示「已被提升 N 次」

- **M6: 源码脱敏**
  - `LLMEndpoint.api_base` 默认值：`192.168.8.12:8080` → `localhost:11434`
  - 创建 `etc/config.example.json`（零真实凭据示例配置）

- **M9: CLI 命令精简**
  - 移除 `todo` 命令（含 `--todo-status` / `--project-id`）
  - 移除 `prompt` 命令（含 list/show/create/edit/activate/diff/rollback 7 子命令）

### Fixed

- **`recall(include_archived=True)` 失效**: `_build_where()` 第 4 个位置参数 `include_archived` 错传给 `type_filter`，导致归档记忆无法检索
- **`_migrate_memory_to_suggestion` 覆盖新默认值**: `update()` 用旧 `memory` 默认值（0.60/0.55）覆盖 `suggestion` 新默认值（0.65/0.50）
- 测试修复：`test_active_memory_config.py`(24)、`test_auth.py`(4)、`test_endpoint_prompt_decoupling.py`(3)、`test_time_decay.py`(1)、`test_suggestion*`(2)、`test_integration_all.py`(1) 等 10 个文件共 28 个遗留失败 + 审计阶段修复 `test_integration_v030.py`(5) 回归（Starlette 1.0.0 TestClient 上下文兼容）

### Removed

- `_compute_feedback_boost()` 方法
- `cmd_todo()` / `cmd_prompt()` 函数及对应 CLI 命令

### Known Issues

- PyPI 包名 `memos` 已被占用，确认使用 `memomate` 发布（`pip install memomate`）。GitHub 仓库名 `memos` 保持不变，CLI 命令 `memos` 不变。

---

## v0.4.5 "主动守护" — 2026-05-26

### Added

- **R1: 冲突检测增强**
  - `conflict_detection_enabled` / `conflict_use_llm` 配置开关
  - Dashboard 冲突检测面板：配对数据展示 + 4 选项解决 + 统计
  - LLM 判断模式 vs 纯向量降级模式

- **R2: 待办系统**
  - `list_todos` / `update_todo` MCP 工具
  - 每日待办提醒后台任务（`daily_todo_time` 配置）
  - Dashboard 待办面板：CRUD + 拖放排序 + 状态过滤
  - 从日报提取待办（`POST /api/conversations/extract-todos`）
  - `todo-extract` 默认提示词模板

- **R3: Agent 决策引擎（配置占位）**
  - `AgentConfig` 全字段定义（Phase 1-3 预留）
  - `pattern_detection_enabled` / `daily_briefing` 等配置项

### Changed

- `conflict_distance_threshold` 默认值从 `0.55` 调升至 `0.85`
- `_build_where()` 统一 4 处重复的 where 条件构建逻辑
- 默认 `project_id` 从 `"default"` 占位符改为 CWD 的 MD5 前 8 位

### Fixed

- 防御：`project_id` 若为 `"default"` 占位符，自动替换为 CWD 的 MD5
- Dashboard 启动时自动迁移 config.json 中的旧 `"default"` 值

---

## v0.4.4 "智能检索" — 2026-05-23

### Added

- **F1: 知识面板检索升级**（前端）
  - 搜索框顶置，移除底部旧"检索测试"区域
  - 卡片式搜索结果（类型标签 + 时间 + 相似度进度条 + 匹配方式标签）
  - 关键词高亮（XSS 安全转义）+ 搜索建议下拉（localStorage）
  - 高级选项折叠面板（top_k/days/type/hybrid/bm25_weight），状态 sessionStorage 记忆
  - 空状态引导 + 清除结果按钮

- **F2: 会话记录检索**（后端 + 前端）
  - `POST /api/conversations/search` — 混合检索 + 日期范围下推 ChromaDB where 子句
  - 结果按 round_id 配对为对话轮次，同 round_id 多条取最高相似度
  - 未配对记录单独展示
  - 对话面板顶部搜索栏 + 日期范围选择器 + 关键词高亮

- **F3: Hook 分层检索重构**
  - `_build_layered_context()` 阈值分层：Layer 1（sim≥0.55，≤3 条注入上下文）+ Layer 2（sim≥0.75 写入 suggestion）
  - `_write_suggestions()` — 含冷却期检查（ChromaDB where 子句）+ 每日上限检查 + expiry_days=0 不过期
  - 免打扰文件检测（`.claude/no_suggestions`，JSON 解析失败仍阻断）
  - `MEMOS_USE_OLD_CONTEXT=1` 环境变量回退到旧版行为
  - `main()` 级 JSON 兜底保护：任何异常输出 `{"additionalContext": ""}`

- **F4: Dashboard 主动建议面板**（前端）
  - 新建「主动建议」Tab，10s 轮询 + 页面隐藏暂停
  - 建议卡片（滑入动画 + 有用/无用/关闭 + 全部已读）
  - 暂停推送开关 + 历史记录分页 + 面板底部统计
  - Tab 角标 + document.title 未读数更新

- **F5: 主动建议 API**（后端 6 端点）
  - `GET /api/suggestions` — 列表（分页+状态过滤+过期排除）
  - `GET /api/suggestions/count` — 轻量计数
  - `POST /api/suggestions/{id}/dismiss` — 单条关闭
  - `POST /api/suggestions/dismiss-all` — 批量关闭
  - `POST /api/suggestions/{id}/feedback` — 提交反馈（useful/not_useful）
  - `GET /api/suggestions/stats` — 统计（total/useful/not_useful/dismissed/useful_rate）
  - `GET /api/suggestions/no-suggestions-status` — 免打扰文件状态

- **F6: 配置基础**（MemoryConfig 7 新字段）
  - `enable_active_suggestions` / `active_suggestion_threshold` / `context_injection_threshold`
  - `context_max_items` / `suggestion_cooldown_minutes` / `suggestion_max_per_day` / `suggestion_expiry_days`
  - 向后兼容：`suggestion_max_per_session` 别名 → `suggestion_max_per_day`

### Tests

- 新增 6 个测试文件，90 个测试用例
- `test_active_memory_config.py` (25) — 默认值/别名/校验/环境变量/schema
- `test_suggestions_api.py` (19) — 6 端点全生命周期 + 认证
- `test_hook_layered_context.py` (23) — 分层/格式化/冷却期/上限/免打扰/异常
- `test_hook_prompt_integration.py` (7) — main() JSON 输出 + 兜底保护 + MEMOS_USE_OLD_CONTEXT 回退
- `test_conversation_search.py` (7) — 搜索/配对/日期过滤/未配对
- `test_suggestion_dedup.py` (9) — 冷却期边界/每日上限边界/过期边界

### Fixed

- `tests/test_dashboard_wizard.py`、`tests/test_endpoint_test.py`、`tests/test_auth.py`、`tests/test_export_import.py`：`ContextMemory` mock 路径修复（`memos.engine.memory` → `memos.web.app`）
- `memos.web.models.__init__.py`：补充导出 `ConversationSearchRequest`、`SuggestionFeedbackRequest`、`SuggestionsListRequest`、`DismissAllRequest`

## v0.4.3 "架构重整" — 2026-05-21

### Changed

- **F1: dashboard.py 模块化拆分**
  - 2707 行单体文件拆分为 `dashboard/` 包（14 文件，11 个路由模块）
  - 原 `from memos.dashboard import app` 导入路径保持不变
  - 辅助函数独立为 `services/helpers.py`，请求模型独立为 `models/requests.py`

- **F2: dashboard.html JS 外置化**
  - 3466 行内联 JS 与 HTML 主结构分离
  - HTML 精简为 60 行（`{% include %}` + `<script src>`）
  - 模态框提取为 `_modals.html`，JS 外置为 `static/js/dashboard.js`

### Added

- **F3: status 字段基础设施（Phase 1）**
  - metadata 新增 `status` 字段（默认 `"active"`）
  - `MemoryConfig` 新增 `default_status` 配置项
  - MCP `ALLOWED_METADATA_KEYS` 新增 `"status"`
  - Dashboard API `list_memories()` 新增 `status` 查询参数
  - JSON Schema 同步更新

### Fixed

- `remember()` where 子句修复：多 key（project_id + type）改用 `$and` 包裹，兼容 ChromaDB 新版本

## v0.4.2 "稳定" — 2026-05-20

### Added

- **F1: 记忆导入/导出标准格式 `.memos` v1.0**
  - 定义正式 `.memos` JSONL 格式规范文档
  - 导出增强：`--since`/`--until`/`--review-status` 选择性过滤 + `memory_ids` 按 ID 导出
  - 导入增强：`--dry-run` 预校验、`--strategy`(skip/overwrite/duplicate) 冲突策略、`--preserve-ids`
  - 格式头部注释行（`# {"format_version":"1.0",...}`）+ embedding 维度校验与自动重编码
  - 批量导入（batch_size=500）+ 进度输出

- **F2: 数据备份与恢复 `memos backup/restore`**
  - 新增 `src/memos/backup.py` 核心模块：`backup_memdb`、`restore_backup`、`list_backups`
  - CLI：`memos backup [--target]`、`memos backup --list`、`memos restore <path> [--force]`
  - Dashboard：一键备份按钮（后台线程异步）、状态卡片（上次备份时间/健康状态/过期提醒）
  - 备份锁（`backup.lock`，10 分钟自动过期）+ 完整性校验（文件数+大小，1%容差）
  - 恢复安全机制：强制确认 + `memdb.bak.*` 回退点 + ChromaDB 连接验证
  - 按个数保留（`max_backups`，默认 10），超出自动清理最旧备份
  - `BackupConfig`：`target_dir`/`max_backups`/`remind_after_days`/`verify_after_backup`

- **F3: 系统通知中心**
  - 新增 `src/memos/notifications.py`：JSONL 持久化 + `threading.Lock` 线程安全
  - 三类通知：`extract_complete`(提炼完成)、`conflict_detected`(冲突提醒)、`expiry_alert`(知识过期)
  - Dashboard 铃铛图标（未读数字徽标）+ 按类型计数 + 下拉最近 5 条
  - 通知列表页 `/notifications`：多类型组合过滤、分页、标记已读/忽略
  - 频率限制（同类型 60 分钟不重复）+ 自动清理（30 天已读通知）
  - 过期检查：通知中心页面加载时按需扫描
  - `NotificationConfig`：`retention_days`/`rate_limit_minutes`

- **F4: 单机版压力测试套件**
  - `scripts/benchmark.py`：6 大场景（纯向量检索/混合检索/时间衰减/列表/写入/并发）
  - 数据生成器：20 主题 × 10 变体，真实 SentenceTransformer 编码
  - JSON + Markdown 双格式报告输出（含环境信息/延迟分布/内存占用）
  - `tests/test_benchmark.py`：5 个冒烟验证用例

- **F6-C2: Dashboard 模板拆分**
  - 4083 行单体 `dashboard.html` → 1 主文件 + 5 Jinja2 partial + 1 CSS
  - `_nav.html`(导航)、`_conversation_panel.html`(对话)、`_knowledge_panel.html`(知识)、
    `_daily_review_panel.html`(回顾)、`_prompts_panel.html`(提示词)
  - CSS 外提到 `static/style.css`，零 JS 变更

### Changed

- **F6-C1: `LongTermMemory` → `ContextMemory` 类名重命名**
  - 全局 745 处引用 / 94 个文件替换
  - `__init__.py` 保留 `LongTermMemory = ContextMemory` 向后兼容别名
  - 与后续 v0.5.0 `CodeMemory` 形成 Context/Code 对仗

- **`config.py`**：新增 `BackupConfig` + `NotificationConfig` 子配置，`MemoConfig` 扩展为 10 子模型
- **`extractor.py`**：提炼完成/冲突检测事件触发通知写入
- **`memory.py`**：`import_memories()` 重写（策略模式 + 预校验 + 维度校验）
- **`cli.py`**：新增 backup/restore 子命令；export/import 扩充参数
- **`dashboard.py`**：新增 6 个 API 端点（备份 4 + 通知 3）、过期按需检查
- **`scripts/backup_memdb.py`**：重构为 `from memos.backup import backup_memdb` 轻量 wrapper
- **CLAUDE.md**：`LongTermMemory` → `ContextMemory`；B1/B2 并发限制和故障排查文档增强
- **`etc/config.json`**：`backup.target_dir` 改为 `backups`（修复递归嵌套风险）

### Fixed

- **B3 (高)**: `save_knowledge` MCP 工具间歇性报 "Error finding id"
  - 根因：`recall_with_scores` 返回的 ID 在 ChromaDB 中状态不一致
  - 修复：`delete_memory()` 前增加 try/except，异常时降级为直接写入
  - 涉及：`server.py`、`dashboard.py`

- **B1 (高)**: ChromaDB 多进程并发写入锁冲突
  - `memos doctor` 新增 ChromaDB 并发状态检查项
  - CLAUDE.md 故障排查章节增强（明确禁止 MCP+Dashboard 同时写入）

- **B2 (高)**: Windows safetensors 多线程加载偶发崩溃
  - 四方向评估确认当前方案（`SAFETENSORS_FAST_LOAD=0` + `OMP_NUM_THREADS=1`）稳定
  - `memos doctor` 新增环境变量检查项
  - 评估结论文档：`document/42版本/B2-safetensors稳定性评估结论.md`

- 导出选中按钮不传记忆 ID（`export_memories()` 新增 `memory_ids` 参数 + `_export_by_ids()` 辅助方法）
- 备份目录递归嵌套（`ignore=shutil.ignore_patterns("backups")`）

### Removed

- `LongTermMemory` 类名（替换为 `ContextMemory`，`__init__.py` 保留 `LongTermMemory = ContextMemory` 别名）
- 废弃 `SIMILARITY_THRESHOLD` 模块常量（使用 `_get_similarity_threshold()` 自适应替代）

### Known Issues

- ChromaDB PersistentClient 不支持多进程并发写入（MCP + Dashboard 同时写入可能锁冲突），v0.5.0 HTTP 模式解决
- Windows 下 safetensors 多线程加载需 `SAFETENSORS_FAST_LOAD=0` 环境变量，Hook 命令已内置
- 50K 大规模基准测试待独立运行，性能基线数据待补充
- `_nav.html` 备份/通知 UI 依赖 Bootstrap JS，非纯 SSR 实现
