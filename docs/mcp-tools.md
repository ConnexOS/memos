# MCP 工具参考（12 个）

## 工具列表

| 工具 | 管线 | 说明 |
|------|------|------|
| `remember(text, metadata)` | A | 追加到缓冲区，累积后自动提炼为知识（7 种类型） |
| `save_knowledge(text, type, metadata)` | B | 直接保存知识到知识库，由用户指令触发（7 种类型） |
| `recall(query, top_k, type_filter, ...)` | — | 语义检索相关记忆 |
| `list_memories(type_filter, limit, offset, project_id_override, exclude_types)` | — | 列出当前项目所有知识（分页），默认排除 todo |
| `set_project_id(pid)` | — | 设置当前会话项目 ID，用于记忆隔离 |
| `log_complete_turn(user_message, assistant_message)` | A | 记录一轮完整对话，累积多轮后自动提炼 |
| `update_memory(memory_id, text, metadata)` | — | 更新记忆内容和/或元数据，仅可更新当前项目记忆 |
| `delete_memory(memory_id)` | — | 硬删除指定记忆，仅可删除当前项目记忆 |
| `force_extract()` | A | 强制立即提炼缓冲区中所有内容 |
| `create_todo(content, priority, due_date)` | — | 创建待办（🆕 v0.4.8），写入完整 metadata，返回 JSON |
| `list_todos(todo_status, limit, project_id_override)` | R2 | 查询待办列表，按 todo_status 过滤，返回 JSON |
| `update_todo(memory_id, todo_status)` | R2 | 更新待办状态，自动记录 status_history + 时间戳 |

## 管线说明

- **Pipeline A**: AI 助手写入 → 缓冲区 → LLM 提炼
- **Pipeline B**: 用户指令直写知识库
- **Pipeline C**: Hook 自动采集对话
- **Pipeline D**: Dashboard 人工选对话 → LLM 提炼

## 知识类型约束

### 7 种知识类型

`remember` 和 `save_knowledge` 接受以下 7 种类型：

| 来源 | 类型 | 说明 |
|------|------|------|
| A+B 管线 | `fact` | 事实性知识 |
| | `decision` | 技术决策 |
| | `preference` | 用户偏好 |
| D 管线 | `bug_fix` | Bug 修复经验 |
| | `feature_design` | 功能设计决策 |
| | `code_optimize` | 代码优化经验 |
| | `tech_knowledge` | 技术知识 |

### 类型红线

- **`todo` 不再属于知识类型**：创建待办请用 `create_todo`，查询待办请用 `list_todos`
- **AI 助手禁止使用 Dashboard 专用类型**（红线）已在 `save_knowledge` 类型白名单层面解除限制（所有 7 种均可写），但建议区分使用场景

### 默认排除

`list_memories` 默认 `exclude_types=["todo"]`，如需查询 todo 类型条目，传 `exclude_types=[]` 覆写。

## create_todo 参数

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `content` | str | 是 | — | 待办内容 |
| `priority` | str | 否 | `"medium"` | 优先级: high/medium/low |
| `due_date` | str | 否 | `""` | 到期日 ISO 8601 "YYYY-MM-DD" |

返回 JSON: `{"id": "uuid", "message": "待办已创建"}`

## 归档操作

归档操作已从 MCP 移除（转向 Web 仪表板），`force_extract` 保留供 AI 助手主动触发提炼。
