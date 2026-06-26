# memos setup 参数优化设计

**日期**：2026-06-09 | **版本**：v0.5.1 | **决策**：方案一 — 直接参数重映射

## 目标

优化 `memos setup` 参数语义，使其更符合直觉：`--server` 支持省略 `http://` 前缀、`--project` 改为目标目录路径、新增 `--name` 指定项目名。

## 参数定义

| 参数 | 必填 | 类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--server` | 是 | str | — | `host:port` 或完整 URL，无 `://` 前缀时自动补 `http://` |
| `--token` | 是 | str | — | 用户 Token |
| `--project` | 否 | str | CWD | 目标项目目录路径，配置在此目录下生成 |
| `--name` | 否 | str | 目录名 | 项目显示名称 |

### `--server` 解析规则

```
if "://" not in server:
    server_url = f"http://{server}"
else:
    server_url = server
```

### `--project` / `--name` 解析规则

```
target_dir = resolve(--project or CWD)
project_name = --name or target_dir.name
project_id = md5(project_name)[:8]
```

## 执行流程

1. **解析参数**：归一化 server_url、target_dir、project_name、project_id
2. **写入 `.memos-project`**（覆盖）：`{target_dir}/.memos-project` → `{"id": "...", "name": "..."}`
3. **写入凭据**（覆盖）：`{target_dir}/.claude/memos-credentials.json`
4. **写入 `.mcp.json`**（覆盖）：`{target_dir}/.mcp.json`，含 SSE URL + token
5. **安装 Hook**（覆盖 memos 相关项，保留 settings.json 中其他配置）：`{target_dir}/.claude/settings.json`

全部覆盖无交互。

## 影响范围

| 文件 | 改动 |
|------|------|
| `src/memos/cli/dispatch.py` | argparse 参数定义 + `_cmd_setup_lazy` 调用 |
| `src/memos/cli/setup.py` | `cmd_setup` 核心逻辑重写 |
| `tests/test_unified/test_setup.py` | 适配新参数语义 |

## 向后兼容

硬切换，不保留 v0.4.x `--project` 作为项目名的旧行为。v0.5.x 已是 breaking 版本。

## 测试要点

- `--server` 无前缀 + 有前缀 + https 前缀
- `--project` 指定路径 + 省略（默认 CWD）
- `--name` 指定 + 省略（取目录名）
- 全量覆盖验证：`.memos-project`、`.mcp.json`、凭据、Hook 均覆盖旧值
- Hook 安装时保留 settings.json 中非 memos 的配置项
