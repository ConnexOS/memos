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

# 限制 PyTorch 线程数，防止 Windows 上 safetensors 多线程加载导致内存访问冲突 (access violation)
# 必须在任何 memos 模块导入之前设置
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("SAFETENSORS_FAST_LOAD", "0")  # 禁用 safetensors 并行加载

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
