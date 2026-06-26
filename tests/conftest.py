import json
import os
from pathlib import Path
from unittest import mock

import pytest

# 测试环境默认使用项目根目录作为 MEMOS_HOME，保持向后兼容
# 必须在导入 memos 之前设置（conftest.py 在 pytest 收集阶段最先执行）
_project_root = str(Path(__file__).resolve().parent.parent)
if "MEMOS_HOME" not in os.environ:
    os.environ["MEMOS_HOME"] = _project_root

# 测试使用独立 collection，避免污染生产数据
os.environ.setdefault("MEMOS_TEST_COLLECTION", "test_suite")

# 设置 LLM 端点为本地回环地址，让 LLM 调用快速失败（避免 30s 超时）
os.environ.setdefault("MEMOS_LLM_API_BASE", "http://127.0.0.1:1")
os.environ.setdefault("MEMOS_LLM_ACTIVE", "test")

# 设置 CLAUDE_PROJECT_DIR 为测试目录，避免异步消费者线程在 monkeypatch 恢复后丢失
_test_claude_dir = str(Path(_project_root) / "etc" / "test_claude_project")
os.environ.setdefault("CLAUDE_PROJECT_DIR", _test_claude_dir)

# 限制 PyTorch 线程数，防止 Windows 上 safetensors 多线程加载导致内存访问冲突 (access violation)
# 必须在任何 memos 模块导入之前设置
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("SAFETENSORS_FAST_LOAD", "0")  # 禁用 safetensors 并行加载
os.environ.setdefault("MEMOS_AUTH_DISABLE", "true")  # 测试环境默认禁用认证

FAKE_LLM_RESPONSE = json.dumps(
    [
        {"content": "团队决定使用FastAPI框架", "type": "decision"},
        {"content": "数据库选用PostgreSQL", "type": "decision"},
        {"content": "每天早上10点开站会", "type": "fact"},
    ]
)


def clean_collection(mem):
    all_ids = mem.store.get()["ids"]
    if all_ids:
        mem.store.delete(ids=all_ids)


def mock_llm(monkeypatch, response_text=None):
    if response_text is None:
        response_text = FAKE_LLM_RESPONSE
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {"content": response_text}
    resp.text = response_text
    monkeypatch.setattr("memos.engine.extractor.requests.post", lambda *a, **kw: resp)


@pytest.fixture
def fake_llm(monkeypatch):
    mock_llm(monkeypatch)


@pytest.fixture
def fake_memory():
    fm = mock.Mock()
    fm.recall_with_scores.return_value = []
    return fm


@pytest.fixture(scope="session")
def unified_app():
    """Session 级共享 unified app — 避免多次初始化 ChromaDB 导致文件锁冲突。

    所有需要 TestClient 的集成测试应使用此 fixture 或基于它的 client。
    各测试文件不应创建独立的 module-scoped app fixture。
    """
    from memos.server.app import create_unified_app

    return create_unified_app()


@pytest.fixture(scope="session")
def unified_client(unified_app):
    """Session 级共享 TestClient，所有集成测试共用同一个 app 实例。"""
    from starlette.testclient import TestClient

    # 使用 localhost base_url，避免 testserver 的 SSE 安全策略问题
    with TestClient(unified_app, base_url="http://localhost:8000") as c:
        yield c
