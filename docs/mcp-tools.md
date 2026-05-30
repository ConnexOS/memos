# MCP 工具参考（11 个）

## 工具列表

| 工具 | 管线 | 说明 |
|------|------|------|
| `remember(text, metadata)` | A | 追加到缓冲区，累积后自动提炼为知识 |
| `save_knowledge(text, type)` | B | 直接保存知识到知识库，由用户指令触发 |
| `recall(query, top_k, type_filter, days_limit, project_id_override, hybrid, bm25_weight)` | — | 语义检索相关记忆 |
| `list_memories(type_filter, limit, offset, project_id_override)` | — | 列出当前项目所有记忆（分页）|
| `set_project_id(pid)` | — | 设置当前会话项目 ID，用于记忆隔离 |
| `log_complete_turn(user_message, assistant_message)` | A | 记录一轮完整对话，累积多轮后自动提炼 |
| `update_memory(memory_id, text, metadata)` | — | 更新记忆内容和/或元数据，仅可更新当前项目记忆 |
| `delete_memory(memory_id)` | — | 硬删除指定记忆，仅可删除当前项目记忆 |
| `force_extract()` | A | 强制立即提炼缓冲区中所有内容 |
| `list_todos(todo_status, limit, project_id_override)` | R2 | 查询待办列表，按 todo_status 过滤，返回 JSON |
| `update_todo(memory_id, todo_status)` | R2 | 更新待办状态，自动记录 status_history + 时间戳 |

## 管线说明

- **Pipeline A**: AI 助手写入 → 缓冲区 → LLM 提炼
- **Pipeline B**: 用户指令直写知识库
- **Pipeline C**: Hook 自动采集对话
- **Pipeline D**: Dashboard 人工选对话 → LLM 提炼

## 知识类型约束

- A+B 用 `fact`/`decision`/`preference`/`todo`
- D 用 `bug_fix`/`feature_design`/`code_optimize`/`tech_knowledge`
- **红线**: AI 助手禁止使用 Dashboard 专用类型

归档操作已从 MCP 移除（转向 Web 仪表板），`force_extract` 保留供 AI 助手主动触发提炼。
