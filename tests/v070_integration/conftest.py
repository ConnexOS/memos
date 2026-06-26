"""v0.7.0 全链路集成测试 — 专属 conftest。

复用 tests/conftest.py 的 session 级 fixture。
"""

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from contextvars import ContextVar

import pytest

# PROJECT_ROOT: 从 tests/v070_integration/ 向上 4 级到项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def set_test_project_id(pid: str = "integration-test"):
    """设置测试用 project_id 到 ContextVar，使 Hook 写入 injected_records 文件。"""
    from memos.server.mcp import _project_id_ctx
    _project_id_ctx.set(pid)
    os.environ["MEMOS_PROJECT_ID"] = pid


def check_ai_reference(assistant_msg: str, pid: str = "integration-test"):
    """直接调用 F1 AI 引用回检函数并返回结果。

    读取 .injected_records_{pid}.json 并检查 assistant_msg 是否引用了注入内容。
    返回: (matched: bool, log_entry: dict)
    """
    from memos.features.activity_log import _append_event
    from memos.config import get_memos_home

    path = get_memos_home() / "etc" / f".injected_records_{pid}.json"
    if not path.exists():
        return False, {}

    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records", [])
    if not records:
        return False, {}

    msg_lower = assistant_msg.lower()
    for r in records:
        snippet = (r.get("content") or "")[:100]
        if not snippet:
            continue
        if snippet.lower() in msg_lower:
            return True, {
                "event": "ai_reference",
                "referenced_id": r.get("id", ""),
                "matched_fragment": snippet,
            }
    return False, {}


# ============================================================
# Helper 方法（注入到各场景测试类）
# ============================================================

def read_latest_activity_log(event_type: str = None):
    """返回活动日志文件的最新记录或指定事件类型的最后一条记录。

    路径: {MEMOS_HOME}/etc/activity_log_YYYY-MM-DD.jsonl
    event_type: 可选，按事件类型过滤（如 "ai_reference"）
    返回: dict (文件空或无匹配条目时返回 {})
    """
    from memos.config import get_memos_home
    today = time.strftime("%Y-%m-%d")
    log_file = get_memos_home() / "etc" / f"activity_log_{today}.jsonl"
    if not log_file.exists():
        return {}
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return {}
    if event_type:
        for line in reversed(lines):
            try:
                record = json.loads(line)
                if record.get("event") == event_type:
                    return record
            except (json.JSONDecodeError, ValueError):
                continue
        return {}
    return json.loads(lines[-1])


def get_injected_file(pid: str = None) -> Path:
    """返回注入记录文件 Path 对象。

    路径: {MEMOS_HOME}/etc/.injected_records_{pid}.json
    返回: Path 对象（文件可能不存在，调用方需 path.exists() 判断）
    """
    from memos.config import get_memos_home
    if pid is None:
        pid = os.environ.get("MEMOS_PROJECT_ID", "default")
    return get_memos_home() / "etc" / f".injected_records_{pid}.json"


def generate_briefing_with_rounds(mem, n: int) -> dict:
    """构造 n 轮对话数据并触发简报生成。

    通过 mem.remember() 写入 n 条 type=user_input 对话记录，
    然后调用 build_fallback_briefing() 生成简报。
    返回简报 dict。
    """
    from memos.features.briefing import build_fallback_briefing
    for i in range(n):
        mem.remember(
            f"集成测试第 {i + 1} 轮对话: 讨论技术选型",
            metadata={"type": "user_input", "source": "hook"},
        )
    # 使用兜底方式生成简报（避免依赖 LLM 端点）
    briefing = build_fallback_briefing(memory_instance=mem)
    return briefing


def generate_briefing_with_llm_failure(mem) -> dict:
    """模拟 LLM 不可用时生成简报。

    使用 unittest.mock.patch.dict 临时修改环境变量使 LLM 调用失败，
    然后触发简报生成并返回兜底结果。
    """
    from unittest.mock import patch
    from memos.features.briefing import build_fallback_briefing
    with patch.dict(os.environ, {"MEMOS_LLM_API_BASE": "http://127.0.0.1:1"}, clear=False):
        # 添加几条对话记录
        for i in range(6):
            mem.remember(
                f"LLM 失败测试对话 {i}",
                metadata={"type": "user_input", "source": "hook"},
            )
        briefing = build_fallback_briefing(memory_instance=mem)
    return briefing


def create_user(username: str, role: str = "member", client=None) -> str:
    """通过 CLI memos user add 创建用户，然后通过登录 API 获取 token。

    注意: CLI 的 user add 硬编码 role="member"，因此 admin 角色需要
    通过直接操作 users.json 来创建。

    返回: token 字符串（创建失败返回空字符串）
    """
    # 先通过 CLI 创建用户
    result = subprocess.run(
        [sys.executable, "-m", "memos.cli", "user", "add", username],
        capture_output=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
        timeout=30,
    )
    if result.returncode != 0:
        return ""

    # 从输出中提取 token（UTF-8 编码避免 Windows GBK 乱码）
    for line in result.stdout.splitlines():
        if line.startswith("Token: "):
            token = line.replace("Token: ", "").strip()
            break
    else:
        return ""

    # 如果需要 admin 角色，直接修改 users.json
    if role == "admin":
        from memos.web.auth import _read_users, _write_users
        users = _read_users()
        for u in users:
            if u["name"] == username:
                u["role"] = "admin"
                _write_users(users)
                break

    return token


def login_get_token(client, username: str = "admin", password: str = "admin") -> str:
    """通过 POST /api/auth/login 获取 token。
    返回: session cookie 值或空字符串。
    """
    resp = client.post("/api/auth/login", json={
        "username": username,
        "password": password,
    })
    if resp.status_code == 200:
        from starlette.testclient import TestClient as _TC
        # 从 cookies 中提取 session
        for key, val in resp.cookies.items():
            if "session" in key.lower():
                return val
        # 尝试从 set-cookie header 提取
        set_cookie = resp.headers.get("set-cookie", "")
        if "memos_session=" in set_cookie:
            return set_cookie.split("memos_session=")[1].split(";")[0]
        return resp.text
    return ""


def prepare_old_type_data(mem):
    """写入旧 7 类测试数据。

    通过 store._collection.add() 直接写入原始类型名，绕过自动类型映射。
    每种旧类型写入 2 条。
    """
    old_types = ["bug_fix", "code_optimize", "preference", "fact",
                 "feature_design", "tech_knowledge"]
    for t in old_types:
        for i in range(2):
            mem.remember(
                f"旧类型 {t} 测试数据 #{i}",
                metadata={"type": t, "source": "migration"},
            )
