# 常见问题排查

## ChromaDB 锁冲突

PersistentClient 不支持多进程并发写入（基于 SQLite，文件级锁）。**严禁 MCP Server (stdio) 和 Dashboard 同时对同一项目写入**——会导致 SQLite 锁冲突甚至数据损坏。

- 测试隔离通过临时 collection 名实现
- 并发诊断：`memos doctor`
- 长期方案：v0.5.0 HTTP 模式

## LLM 提炼无返回

检查 LLM 服务健康状态（`{api_base}/health`），查看 `hook_prompt.log` 中 LLM 响应原文。

## Hook 不触发

运行 `memos hook status` 检查安装状态，确认 `python -m memos.hook_proxy --hook` 可正常执行。

## Windows safetensors 线程错误

Hook 超时/崩溃，日志含 `RuntimeError` 关于线程安全。Hook 命令已内置 `SAFETENSORS_FAST_LOAD=0` 环境变量强制降级为串行加载。若问题仍存在，可手动在终端设置该变量后测试。

## 关键导入

`__init__.py` 惰性加载导出：`ContextMemory`, `MemoryExtractor`, `SIMILARITY_THRESHOLD`, `DEFAULT_DECAY_LAMBDA`, `_estimate_tokens`, `format_conversation`, `mcp`, `_detect_project_id`, `app`, `main`, `__version__`

---

# v0.7.X 常见问题

## 收件箱页面空白

确认 Dashboard 已重启（`memos server`），收件箱页面依赖 `/inbox` 路由和 `/api/inbox/*` 端点。

## 通知类型不显示

v0.7.2 新增了 quality_alert / ttl_warning / watchlist_update / dedup_failed 通知类型。如果 Dashboard 顶栏收件箱按钮未出现，检查 `_nav.html` 是否为最新版本。

## 去重 LLM 判断超时

`save_knowledge()` 触发去重时，后台线程调用 MEMOS LLM（30s 超时）。如频繁出现 dedup_failed 通知：
1. 检查 LLM 端点是否可用：`memos doctor`
2. 确认 LLM 响应速度（建议 < 3s）
3. decision 类型会自动降级为覆盖写入，不影响写入流程

## 统计卡待归档显示异常

总览统计卡「待归档」数基于 `status=forgotten AND forgotten_at + archive_days < now` 计算。
- 无遗忘记忆 → 显示 `0`
- 遗忘记忆未满 30 天 → 显示 `0`
- 遗忘记忆已满 30 天 → 显示正确数字
- 如始终显示 `0` 但有遗忘记忆，检查 `forgotten_at` 是否为 `0`（旧数据未迁移）

## 性能基准测试

v0.7.2 新增 `tests/performance/test_perf_v072.py` 性能压力测试文件：

```bash
.\venv\Scripts\python -m pytest tests/performance/test_perf_v072.py -v --timeout=120
```

涵盖 6 项基准：recall 冷/热缓存、list_memories 翻页、写入吞吐、BM25 惰性重建、SSE 推送延迟。
数据规模 10K 和 50K 双规模验证。
