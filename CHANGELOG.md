# Changelog

## [0.7.2] — 2026-06-30

> v0.7.X 系列最终版本 —「收官」。覆盖信号枢纽（统一收件箱）、遗留功能完善、候选品质打磨三个维度。

#### Added

- **收件箱全页（/inbox）**
  - 三区聚合布局：系统通知 / 待关注 / 待修正
  - `GET /api/inbox/items` — 三区聚合查询
  - `GET /api/inbox/unread-count` — 统一未读数（30s 轮询）
  - `POST /api/inbox/dismiss/{id}` — 忽略/已读单条
  - `POST /api/inbox/dismiss-all` — 批量已读（仅 JSONL 通知区）
  - 顶栏「🔔收件箱」入口，替代旧下拉通知列表
  - action→按钮动态映射表（review/renew/view/retry）
  - 待关注「转为知识」→ 可保存为 L3 知识
  - 待修正「查看」→ 跳转记忆管理并高亮（或降级跳转）

- **通知类型扩展**
  - quality_alert — `save_knowledge()` quality_score < 0.5 触发，60min 限频
  - ttl_warning — lesson 类型过期前 7 天触发
  - watchlist_update — `remember()` 新增 watchlist 时触发
  - dedup_failed — 去重 LLM 判断失败时触发
  - `get_unread_counts()` 去硬编码，动态 JSONL 统计所有类型

- **MCP 写入去重优化**
  - `save_knowledge()` 异步 LLM 判断（30s 超时），MCP 立即返回不阻塞
  - 按类型差异化策略：solution（LLM 判同一问题+quality 覆盖）/ decision（总是覆盖）/ lesson（互补共存）/ process（不调 LLM 直接覆盖）
  - 类型专用去重 Prompt（solution/decision/lesson 三套）
  - LLM 故障降级：decision 覆盖写入，其余跳过写入
  - 去重结果写入通知中心（仅失败时发送 dedup_failed）

- **手工提炼 prompt 更新**
  - 提炼 system prompt 适配新 4 类（solution/decision/lesson/process）
  - `default@extract` 模板同步更新
  - 前端提炼结果展示新类型标签，旧类型标签隐藏

- **任务审计弹窗**
  - `GET /api/tasks/audit?date=YYYY-MM-DD` — 查询指定日期 task done 项
  - 模态弹窗：日期选择器 + 时间线列表（时间+标题+done 项）
  - 仅展示 done 项，不展示 todo/blocked

- **今日回顾右侧栏日报列表**
  - 历史日报列表（日期 + 首行预览），点击加载完整日报
  - 数据源：`document/日报/{项目名}/*.md` 文件

- **统计卡修复**
  - `GET /api/v2/stats/pending-archive` — 真实计算 pending_archive_count
  - 逻辑：`status=forgotten AND forgotten_at + archive_days < now`
  - 无遗忘/均在时间范围内 → 显示 `0`

- **通知 Badge 泛化**
  - `_nav.html` 通知 badge 改为从后端动态渲染
  - 新增通知类型后 badge 自动出现，无需修改前端代码

#### Changed

- **导航重构**
  - 顶栏「🔔下拉」替换为「🔔收件箱」，点击进入 `/inbox`
  - 总览组移除「待修正」入口
  - 「待关注」「待修正」面板从原位置移除，功能迁入收件箱
  - 待关注区和待修正区不再作为独立面板展示

- **导航标签命名优化**
  - 短标签改为完整表述：总览→项目总览，对话→对话管理，记忆→记忆管理，待办→待办事项，配置→配置管理

- **JS 模块化拆分**
  - `dashboard.js` 导航模态框逻辑拆分出 `nav-modals.js`（934 行新文件）
  - `dashboard.js` 净减少 ~948 行，主文件聚焦统计面板和通用逻辑
  - `dashboard.html` 精简 163 行（移除大量内联 JS/CSS）

- **`save_knowledge` 默认评分调整**
  - 默认 `source` 从 `user_instructed` 改为 `auto_save`
  - 默认 `quality_score` 从 `1.0` 降至 `0.8`
  - 默认 `quality_reason` 从 "用户直写" 改为 "Claude 主动保存"
  - 调用方可传入 `metadata` 覆盖上述默认值

- **任务审计前端重构**
  - `parseTaskEval()` 统一解析来自后端原始 `document` 的 TASK_EVAL
  - 后端移除正则解析逻辑，改回传 `document` 和 `goal` 字段
  - 审计时间线配色：暖色调 `[9,12) + [14,18)`（text-warning），冷色调其余时段（text-primary）
  - 日期选择（`#audit-date`）变更自动触发刷新
  - 无 done 项时显示「该日任务无已完成项」

- **apiClient 统一迁移**
  - 今日回顾侧栏列表（`loadDailyReviewSidebar`）改用 `apiClient.request()`
  - 历史日报加载（`loadDailyReview`）改用 `apiClient.request()`
  - 任务审计加载（`loadTaskAudit`）改用 `apiClient.request()`

- **项目切换联动增强**
  - 切换项目时自动刷新今日回顾右侧栏（`loadDailyReviewSidebar`）
  - 记忆卡底部显示记忆 ID（monospace 样式）
  - `_todo_panel.html` 70 行改动（待办面板优化）
  - `_daily_review_panel.html` 30 行改动（回顾面板优化）

#### Fixed

- **`save_knowledge` 异步去重 LLM 调用结构修复**
  - 后端 LLM 调用对齐 `app.py` 调用模式，消除后台线程导入依赖和锁争用
  - `_call_llm` 改用 `urllib` 直接调用，避免引入过多导入依赖

- **统计卡待归档数真实计算**：`forgottenCount > 0` 显示 "?" → 真正扫描 `status=forgotten` 超期记忆
- **MCP 去重异步执行**：不阻塞 MCP 返回，后台线程处理 LLM 判断
- **提炼结果类型展示**：新 4 类标签展示，旧 7 类标签隐藏但数据保留
- **收件箱页面 XSS 安全**：`title`、`text` 字段使用 `escapeHtml()` 转义后渲染
- **通知 action 动态映射**：`view`/`renew`/`retry` 操作的 JS 处理函数实现
  - `view`：查找通知关联的 `memory_id`，跳转记忆管理
  - `renew`/`retry`：降级为 `dismiss` 行为

#### Security

- **安全加固 A**
  - Dashboard 登录会话过期机制（`config.auth.session_ttl`）
  - 写操作 API 端点鉴权覆盖（未登录返回 401）
  - 用户输入点 XSS 转义处理（收件箱 `escapeHtml()`、记忆卡 ID 显示）
  - 配置 API Key 日志遮蔽

#### Removed

- `force_extract()` MCP 工具（管线 A 已重构）
- `log_complete_turn()` MCP 工具（管线 A 已重构）
- `scheduler.py` 中 `_check_ttl_warnings` 多参兼容分支（v0.7.1 遗留）

---

> v0.7.0「进化」之后的打磨版本。不引入架构级变更，聚焦 TTL 遗忘 + 技术债务清理 + Dashboard UX 优化。

#### Added

- **F1: TTL 遗忘**
  - SchedulerThread 定时扫描（默认 30 分钟），按类型覆盖过期阈值（task 48h / briefing 24h / lesson 90 天）
  - `PATCH /api/memories/{id}/restore` 恢复遗忘记忆，重置 `updated_at` 重新计时
  - 新增 `memory` 配置节（Pydantic model + etc/config.json 惰性加载）
  - TTL 首次扫描保护：24h 宽限期内仅记录不执行

- **F5: Task 管理**
  - Task 增加 `pending` 状态，四态流转：`pending → active → completed → archived`
  - 每条 TASK_EVAL 保存为独立记录，形成可追溯的时间线
  - Task 面板移至总览组（不再在记忆管理面板展示）

- **F9: SSE 韧性增强**
  - 60s 空闲主动健康探测
  - 连续 3 次失败才触发降级（之前 1 次即降级）
  - 降级后每 10s 周期性重连，成功后切回 SSE

- **简报聚合视图**
  - 简报历史列表 + 详情 API
  - 计数注入（task_done/task_todo/new_knowledge/session）
  - Git 收集器（git log + git diff）作为数据源
  - 知识型提示词模板替换基础设施模板
  - 质量门禁 `_has_substance` 内容信号检测

- **配置惰性化**
  - `get_config()` 替代模块级 `load_config()`，消除导入时副作用
  - 新增 `memory` 配置节，向前兼容（缺失自动补全）

#### Changed

- **B1: 导航重构**
  - 5 组 17 子面板：总览 / 对话 / 记忆 / 跟进 / 配置
  - 一级导航移入顶栏，顶栏精简为 4 项工具
  - 更名：记忆流 → 事件看板，注入监控 → 监控面板，手工建议 → 用户建议
  - hash 路由支持 URL 定位二级面板

- **B2: UX 修复**
  - P5 空状态提示 — 所有面板统一 empty-state 样式
  - P6 骨架屏 — 统一加载态，替换零散 spinner
  - P7 时间戳格式切换（relative/absolute + localStorage）
  - P8 修复 Tab 闪烁（inline script 预置 active 类）
  - P9 URL hash 二级面板定位
  - P10 首次加载直接调用首个面板加载函数

- **监控面板**
  - GET /api/v2/monitor/overview 聚合端点（4 卡片 + 注入时间线 + 指令面板）
  - 数据源：injected_records + activity_log + list_memories

#### Fixed

- **Dashboard 指令面板 TASK_EVAL 状态**：`task_eval_injected` 改用 `project_id` 判定（之前用 `task_status == "active"` 错误推断）
- **冷启动文件路径**：统一三处定义为 `get_memos_home()/etc/.cold_start_done_{project_id}`
- **简报注入**：范围扩至最近 5 天，去除兜底生成路径
- **UI 多稿修复**：
  - 记忆管理面板单列全文显示 + 面板独立滚动
  - 简报工作台标题 h6 字号
  - 允许注入按钮式 toggle（统一为按钮样式）
  - 任务模式按钮靠左
  - 项目切换联动记忆管理面板
  - 用户建议卡片开关/详情/编辑
  - 恢复顶栏工具栏图标按钮
  - 语言切换移至顶部工具栏

#### Removed

- 工具栏时间格式按钮（统一归口到通用设置）
- `GeneralConfig.timezone` 设置
- `time_format` 设置（`timeAgo()` 固定为相对时间）
- 通用设置面板（语言移至工具栏）
- 旧版简报 prompt 模板

---

## [0.7.0] — 2026-06-20

> Dashboard 新旧集成融合 + 质量提升 + 能力完善。系统开始从反馈中学习，用户可以手工提炼知识、遗忘记忆、旧管线遗迹清理干净。

#### Added

- **F5: 记忆元数据治理**
  - status 三层分离模型：`active` / `forgotten` / `archived`
  - `inactive_reason` 枚举：`ttl_expired` / `user_archived` / `replaced` / `feedback_negative`
  - `superseded_by` 引用链支持

- **F6: 记忆管理面板重建**
  - 基于新 6 类体系的完整 CRUD 面板
  - 全文展示 + 独立滚动
  - 手工提炼（D 管线）：选定对话记录 → MEMOS LLM 结构化 → 类型归类

- **F7: 主动遗忘**
  - 记忆手动遗忘 / 恢复 / 归档
  - 30 天自动归档（`forgotten` → `archived`）

- **F8: 简报质量策略**
  - 语义化数据源替换基础设施数据
  - 质量门控：`_has_substance` 内容信号检测
  - 三种质量等级：full / simple / none

- **F9: SSE 实时推送**
  - EventBus 发件箱模式
  - 前端 EventSource 自动订阅
  - 降级轮询机制

- **F10: 反馈反哺**
  - `useful_feedback_count` / `not_useful_feedback_count` → 统一 `reuse_count`
  - 排序加成：`math.log2(reuse_count + 1) * 0.15`
  - 建议卡片显示「已被提升 N 次」

- **F11: 提示词管理**
  - PromptManager + admin-only 管理
  - `default@briefing` 模板类型

- **F12: 注入监控**
  - injected_records 文件持久化
  - Dashboard 注入详情时间线展示

- **F13: 行为引导**
  - `behavior_guide.json` 独立文件化管理
  - L3 行为引导文本注入

- **F14: 测试清理**
  - 全链路集成测试方案（P0-1/P1-1 全部通过）
  - 46 → 0 测试回归修复

#### Changed

- **F2: Dashboard 导航重构**
  - 两套 Dashboard 合并为统一 Dashboard
  - 5 组导航：总览 / 对话 / 记忆 / 跟进 / 配置
  - 全面更名使命名符合新体系

- **F1: AI 引用检测**
  - Stop Hook 回检注入的记忆是否被 AI 引用
  - 引用数据写入 injected_records

- **F3: 旧管线代码清理**
  - 移除 v0.5.1 时代的三管道建议代码
  - `_get_layered_context_v2()` → `_build_layered_context()` 统一路径

- **F4: 数据迁移**
  - `memos migrate types` CLI 命令（dry-run + 执行）
  - 旧 7 类 → 新 6 类映射迁移

#### Fixed

- `recall(include_archived=True)` 参数错位修复
- `_migrate_memory_to_suggestion` 覆盖新默认值修复
- schema 缓存 MD5 哈希策略替代手动删除

#### Removed

- 所有旧 7 类 UI 代码
- `force_extract()` / `log_complete_turn()` 废弃函数
- `lock_kb_refresh` / `show_all_facts` 等废弃属性

---

## [0.6.0] — 2026-06-14

> 新架构的第一次完整落地。五层架构 + 新 6 类知识体系 + Claude Code 主动写记忆。

#### Added

- **五层架构首次落地**
  - L1 原始记录层：Hook 自动采集 user_input / assistant_output
  - L2 上下文层：task（会话级四态） / briefing（日频）
  - L3 知识层：solution / decision / lesson / process — Claude 自写
  - L4 交互层：todo / manual_suggestion / daily_report
  - L5 行为层：Dashboard 六面板展示

- **知识类型重构**
  - 旧 7 类 → 新 6 类体系（solution / decision / lesson / process / task / briefing）
  - 按层划分生命周期和注入策略
  - watchlist 独立类型（`remember()` 语义变更）

- **L5 Dashboard v2**
  - 记忆流面板（recall/写入/注入 三类事件时间线）
  - 待关注面板（watchlist，30 天未处理自动归档）
  - 待修正面板（冲突检测 + 4 选项解决 + 批处理）
  - 活动日志查看

- **MEMOS LLM**
  - 多端点支持，OpenAI chat/completions 格式
  - 职责限定：只做结构化加工，不做被动扫描
  - 简报定时生成 / 兜底生成 / 手工触发

- **管线A 重构**
  - `remember()` → watchlist 直写 ChromaDB
  - 缓冲管理（5 轮触发）
  - LLM 调用 + JSON 三级回退 + 去重

- **活动日志系统**
  - etc/activity_log_YYYY-MM-DD.jsonl，按天轮转保留 30 天
  - 记录 recall / 写入 / 注入 三类事件

#### Changed

- 产品定位根本调整：从"人类知识管理"转向"智能体的记忆伙伴"
- 旧 7 类查询兼容：`type_filter` 参数自动映射到新 6 类
- `remember()` MCP 工具语义：记忆提炼 → watchlist 写入

#### Fixed

- BM25 索引惰性重建（写入时失效，查询时重建）
- 项目隔离 ChromaDB where.project_id 过滤
- 混合检索 BM25 + 向量加权融合正确性

---

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
  - `LLMEndpoint.api_base` 默认值：`<internal-server>:8080` → `localhost:11434`
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
