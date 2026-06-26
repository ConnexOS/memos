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
