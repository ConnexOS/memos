# CLI 命令参考（`memos` 入口）

## 初始化与配置

```
memos init                         首次初始化（创建目录、下载模型、写入配置）
memos init --force                 强制重新初始化（覆盖已有配置）
memos init --migrate-from <path>   从旧目录迁移数据后初始化
memos config show                  查看当前配置（扁平化 key-value）
memos config set <key> <value>     修改配置（如 llm.active=deepseek-ai）
memos config reload                从文件重载配置（刷新全局单例）
memos config validate [--file]     [待讨论] 校验配置文件合法性
memos auth regen                   重新生成 Dashboard 访问 Token
```

## 服务

```
memos server      启动 MEMOS Server（unified 模式：FastAPI 统一服务，Dashboard + MCP + Hook 三合一）
memos dashboard   启动 Web 仪表板（legacy 模式）
```

## 诊断

```
memos status      查看系统状态（模型/ChromaDB/LLM）
memos doctor      诊断系统健康度（依赖/模型/ChromaDB/LLM 连通性）
```

## 记忆管理

```
memos export [--output] [--project-id] [--type]   [待讨论] 导出记忆为 JSON Lines
memos import <file> [--project-id] [--strategy]   [待讨论] 从 JSON Lines 导入记忆
memos backup [--output-dir] [--project-id]        [待讨论] 创建全量备份
memos restore <backup-dir> [--project-id]         [待讨论] 从备份恢复
memos vacuum [--project-id]                       [待讨论] 回收 ChromaDB 磁盘空间
memos reindex [--project-id]                      [待讨论] 强制重建 BM25 索引
```

## MCP 管理

```
memos mcp                 查看 MCP 子命令帮助
memos mcp install         生成带 project_id 的 .mcp.json（SSE 模式）
memos mcp install --server URL    指定 memos server 地址（默认 http://localhost:8000）
```

## 用户管理

```
memos user add <name>             创建用户并生成 Token
memos user list                   列出所有用户
memos user remove <name>          删除用户
memos user token-regen <name>     重新生成 Token
```

## 认证

```
memos login --server URL --token TOKEN   保存凭据到本地
memos logout                             清除本地凭据
```

## 模式迁移

```
memos migrate --to-unified   迁移到 unified 模式（备份配置和数据，设置 server.mode=unified）
```

## Hook 管理

```
memos hook install             安装对话自动采集 Hook（项目级）
memos hook install --global    全局安装 Hook
memos hook status              查看 Hook 安装状态
memos hook uninstall           卸载 Hook
```

## 日报

```
memos today [--date] [--project-id] [--print]  生成今日开发日报
```
