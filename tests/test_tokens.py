from memos.engine.extractor import _estimate_tokens


class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 0

    def test_english_only(self):
        assert _estimate_tokens("hello world") == int(11 / 0.75)

    def test_chinese_only(self):
        assert _estimate_tokens("你好世界") == int(4 / 0.75)

    def test_mixed_content(self):
        assert _estimate_tokens("hello world 你好") == int(14 / 0.75)

    def test_long_text(self):
        text = "a" * 3000
        assert _estimate_tokens(text) == int(3000 / 0.75)

    def test_special_chars(self):
        assert _estimate_tokens("\n\t!@#$%^&*()") == int(12 / 0.75)
