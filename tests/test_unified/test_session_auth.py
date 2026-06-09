"""测试 SessionAuthStore — 线程安全 session→token 映射"""

import time

from memos.server.mcp import SessionAuthStore


def test_put_and_get():
    store = SessionAuthStore(ttl_seconds=60)
    store.put("session-1", "token-aaa")
    assert store.get("session-1") == "token-aaa"


def test_get_nonexistent():
    store = SessionAuthStore(ttl_seconds=60)
    assert store.get("no-such-session") is None


def test_ttl_expiry():
    store = SessionAuthStore(ttl_seconds=0.05)
    store.put("session-1", "token-aaa")
    time.sleep(0.2)  # 4x TTL，留足余量避免 CI 调度抖动
    assert store.get("session-1") is None


def test_overwrite():
    store = SessionAuthStore(ttl_seconds=60)
    store.put("session-1", "token-old")
    store.put("session-1", "token-new")
    assert store.get("session-1") == "token-new"


def test_cleanup():
    store = SessionAuthStore(ttl_seconds=0.05)
    store.put("s1", "t1")
    time.sleep(0.2)  # 4x TTL
    count = store.cleanup()
    assert count == 1
    assert store.get("s1") is None
