import time
import threading
from memos.engine.extractor import (
    MemoryExtractor,
    MAX_BUFFER_TOKENS,
    TRUNCATE_TARGET_TOKENS,
    TRIGGER_ROUNDS,
    RATE_LIMIT_SECONDS,
)


class TestTruncateBuffer:
    def setup_method(self):
        self.ext = MemoryExtractor()

    def test_no_truncate_when_under_limit(self):
        self.ext.conversation_buffer = ["user: hello"] * 5
        before = list(self.ext.conversation_buffer)
        self.ext._truncate_buffer()
        assert self.ext.conversation_buffer == before

    def test_truncate_when_over_limit(self):
        long_turn = "user: " + "x" * 5000
        self.ext.conversation_buffer = [long_turn, "user: y"]
        self.ext._truncate_buffer()
        merged = "\n".join(self.ext.conversation_buffer)
        assert "[前面部分截断]" in merged or len(merged) < len(long_turn)

    def test_truncate_preserves_recent_content(self):
        early = "user: early " + "x" * 2500
        recent = "user: recent content kept here"
        self.ext.conversation_buffer = [early, recent]
        self.ext._truncate_buffer()
        merged = "\n".join(self.ext.conversation_buffer)
        assert "recent content kept here" in merged

    def test_empty_buffer(self):
        self.ext.conversation_buffer = []
        self.ext._truncate_buffer()
        assert self.ext.conversation_buffer == []


class TestCanExtract:
    def setup_method(self):
        self.ext = MemoryExtractor()

    def test_first_call(self):
        self.ext._last_extract_time = 0
        assert self.ext._can_extract() is True

    def test_recently_extracted(self):
        self.ext._last_extract_time = time.time()
        assert self.ext._can_extract() is False

    def test_cooldown_elapsed(self):
        self.ext._last_extract_time = time.time() - RATE_LIMIT_SECONDS
        assert self.ext._can_extract() is True


class TestAppendConversation:
    def setup_method(self):
        self.ext = MemoryExtractor()
        self.ext._last_extract_time = time.time()

    def test_append_single_turn(self):
        result = self.ext.append_conversation("user", "hello")
        assert len(self.ext.conversation_buffer) == 1
        assert result is False

    def test_append_multi_turn_below_threshold(self):
        for i in range(TRIGGER_ROUNDS - 1):
            self.ext.append_conversation("user", f"msg {i}")
        assert len(self.ext.conversation_buffer) == TRIGGER_ROUNDS - 1

    def test_append_trigger_auto_extract(self):
        self.ext._last_extract_time = 0
        for i in range(TRIGGER_ROUNDS):
            self.ext.append_conversation("user", f"msg {i}")
        assert len(self.ext.conversation_buffer) == 0

    def test_role_assistant_format(self):
        self.ext.append_conversation("assistant", "ok")
        assert self.ext.conversation_buffer[0] == "assistant: ok"

    def test_concurrent_append(self):
        errors = []

        def append_thread():
            try:
                for _ in range(20):
                    self.ext.append_conversation("user", "test")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=append_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0


class TestForceExtract:
    def setup_method(self):
        self.ext = MemoryExtractor()

    def test_force_extract_with_content(self, monkeypatch):
        monkeypatch.setattr(self.ext, "extract_and_store", lambda merged: 0)
        self.ext.append_conversation("user", "hello")
        self.ext.append_conversation("user", "world")
        self.ext.append_conversation("user", "test")
        count = self.ext.force_extract()
        assert count == 0
        assert len(self.ext.conversation_buffer) == 0

    def test_force_extract_empty_buffer(self):
        self.ext.conversation_buffer.clear()
        count = self.ext.force_extract()
        assert count == 0

    def test_force_extract_sequential(self, monkeypatch):
        monkeypatch.setattr(self.ext, "extract_and_store", lambda merged: 0)
        self.ext.append_conversation("user", "first batch")
        self.ext.force_extract()
        assert len(self.ext.conversation_buffer) == 0
        self.ext.append_conversation("user", "second batch")
        assert len(self.ext.conversation_buffer) == 1
        count = self.ext.force_extract()
        assert count == 0
        assert len(self.ext.conversation_buffer) == 0

    def test_force_extract_during_rate_limit(self, monkeypatch):
        monkeypatch.setattr(self.ext, "extract_and_store", lambda merged: 0)
        self.ext._last_extract_time = time.time()
        self.ext.append_conversation("user", "test content")
        count = self.ext.force_extract()
        assert count == 0
