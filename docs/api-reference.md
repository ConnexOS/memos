# Dashboard API 参考

## 建议 API（v0.4.4）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/suggestions` | 查询建议列表（分页+状态过滤+过期排除） |
| `GET` | `/api/suggestions/count` | 待处理建议数量（轻量查询） |
| `POST` | `/api/suggestions/{id}/dismiss` | 关闭单条建议 |
| `POST` | `/api/suggestions/dismiss-all` | 批量关闭所有待处理建议 |
| `POST` | `/api/suggestions/{id}/feedback` | 提交反馈（useful/not_useful） |
| `GET` | `/api/suggestions/stats` | 建议统计 |
| `POST` | `/api/suggestions/toggle-pause` | 切换暂停推送状态 |
| `GET` | `/api/suggestions/no-suggestions-status` | 查询免打扰文件状态 |

## 提示词管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/prompts` | 列出所有模板 |
| `POST` | `/api/prompts` | 创建模板 |
| `GET` | `/api/prompts/{id}` | 模板详情 |
| `PUT` | `/api/prompts/{id}` | 更新模板元数据 |
| `PUT` | `/api/prompts/{id}/config` | 保存模板公共属性 |
| `DELETE` | `/api/prompts/{id}` | 删除模板 |
| `DELETE` | `/api/prompts/{id}/versions/{version}` | 删除指定版本 |
| `POST` | `/api/prompts/{id}/draft` | 保存草稿 |
| `POST` | `/api/prompts/{id}/upgrade` | 草稿升级为新版本 |
| `GET` | `/api/prompts/{id}/versions/{v}` | 获取版本内容 |
| `POST` | `/api/prompts/{id}/activate-version/{v}` | 切换活跃版本 |
| `POST` | `/api/prompts/{id}/rollback/{v}` | 回滚到历史版本 |
| `POST` | `/api/prompts/{id}/sync-to-active` | 草稿同步到活跃版本 |
| `GET` | `/api/prompts/{id}/diff?v1&v2` | 版本 diff |
| `GET` | `/api/prompts/for-endpoint/{name}` | 端点专属模板查询 |

## 日报与用量 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/conversations/daily-review` | 生成日报 |
| `POST` | `/api/conversations/daily-review/preview` | 预览日报 |
| `POST` | `/api/conversations/daily-review/save` | 保存日报 |
| `GET` | `/api/usage/stats?period=today&endpoint=all` | LLM 用量统计 |

## 冲突检测 API（v0.4.5）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/conflicts` | 冲突列表（配对返回 `{pairs, total}`） |
| `POST` | `/api/conflicts/{pair_id}/resolve` | 解决冲突（overwrite/keep_both/edit） |
| `POST` | `/api/conflicts/{pair_id}/discard` | 放弃新记忆 |
| `GET` | `/api/conflicts/stats` | 冲突决策统计 |

## 待办 API（v0.4.5）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/todos` | 列表（过滤+分页+排序+分组统计） |
| `POST` | `/api/todos` | 新建（content+priority+due_date?） |
| `PUT` | `/api/todos/{id}` | 编辑 |
| `POST` | `/api/todos/{id}/status` | 状态流转 |
| `DELETE` | `/api/todos/{id}` | 删除 |
