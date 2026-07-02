"""测试共享 fixture 和工具函数。

测试环境配置（在导入 memos 之前设置）：
- MEMOS_HOME 指向项目根目录（保持向后兼容）
- 使用独立 collection "test_suite"，避免污染生产数据
- LLM 端点设为本地回环，快速失败
- 默认禁用认证
"""

import json
import os
from pathlib import Path
from unittest import mock

import pytest

# ── 环境变量（必须在导入 memos 模块之前设置） ──────────────────────────
_project_root = str(Path(__file__).resolve().parent.parent)
os.environ.setdefault("MEMOS_HOME", _project_root)
os.environ.setdefault("MEMOS_TEST_COLLECTION", "test_suite")
os.environ.setdefault("MEMOS_LLM_API_BASE", "http://127.0.0.1:1")
os.environ.setdefault("MEMOS_LLM_ACTIVE", "test")

_test_claude_dir = str(Path(_project_root) / "etc" / "test_claude_project")
os.environ.setdefault("CLAUDE_PROJECT_DIR", _test_claude_dir)

# 限制 PyTorch 线程，防止 Windows 上 safetensors 多线程加载导致访问冲突
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("SAFETENSORS_FAST_LOAD", "0")
os.environ.setdefault("MEMOS_AUTH_DISABLE", "true")

# ── 模拟 LLM 响应 ──────────────────────────────────────────────────────
FAKE_LLM_RESPONSE = json.dumps([
    {"content": "团队决定使用FastAPI框架", "type": "decision"},
    {"content": "数据库选用PostgreSQL", "type": "decision"},
    {"content": "每天早上10点开站会", "type": "fact"},
])


def clean_collection(mem):
    """清空 ChromaDB 测试 collection。"""
    all_ids = mem.store.get()["ids"]
    if all_ids:
        mem.store.delete(ids=all_ids)


def mock_llm(monkeypatch, response_text=None):
    """Mock requests.post 模拟 LLM 调用返回。"""
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
    """Session 级共享 unified app — 避免多次初始化 ChromaDB 导致文件锁冲突。"""
    from memos.server.app import create_unified_app
    return create_unified_app()


@pytest.fixture(scope="session")
def unified_client(unified_app):
    """Session 级共享 TestClient。"""
    from starlette.testclient import TestClient
    with TestClient(unified_app, base_url="http://localhost:8000") as c:
        yield c
