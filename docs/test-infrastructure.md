# 测试基础设施

## 测试分组

集成测试（`test_integration_all.py`）按 7 组运行，每组自动创建临时 ChromaDB collection 隔离：

| 分组 | 覆盖 | 用例数 |
|------|------|--------|
| A (CRUD) | 增删改查、归档恢复、分页、类型过滤 | 12 |
| B (Buffer/Extract) | 缓冲累积、触发、限速、去重、截断 | 9 |
| C (MCP) | MCP 工具全链路 | 10 |
| D (Project Isolation) | 项目隔离、跨项目检索、MCP 切换 | 6 |
| E (Hybrid/Decay) | 混合检索、时间衰减排序 | 5 |
| F (Cross-Session) | 持久化验证、多 collection 隔离 | 4 |
| G (Exceptions) | LLM 不可用、非 JSON、空文本、并发 | 6 |

## 关键 Fixture（conftest.py）

| Fixture | 用途 |
|---------|------|
| `fake_llm` | 模拟 LLM 提炼接口，返回预置 JSON（3 条 `decision`/`fact`），避免真实 HTTP 调用 |
| `fake_memory` | 模拟 `ContextMemory`，`recall_with_scores` 返回空列表，用于隔离测试提炼引擎 |
| `FAKE_LLM_RESPONSE` | 常量（模块级），LLM Mock 默认返回的 3 条结构化知识 |
| `clean_collection(mem)` | 函数（非 fixture），清空指定 memory 实例的全部记录 |

## 测试模式

- **纯单元测试**（`test_buffer.py`, `test_extract_mock.py` 等）：依赖 `fake_llm`/`fake_memory` mock
- **集成测试**（`test_integration_all.py`）：临时 ChromaDB collection 隔离，分 7 组独立 setup/teardown
- **真实 LLM 测试**（`test_integration.py`）：标记 `real`，默认被 `-k "not real"` 排除

## 常用测试命令

```powershell
.\venv\Scripts\python -m pytest tests/ -v
.\venv\Scripts\python -m pytest tests/ -v -k "not real"           # 排除真实 LLM
.\venv\Scripts\python -m pytest tests/test_buffer.py -v           # 单文件
.\venv\Scripts\python -m pytest tests/test_integration_all.py -v -k "TestGroupA"  # 集成分组
```
