"""Phase 2: _request_with_retry 按错误码区分重试策略测试"""

import time
from unittest import mock

import pytest

from memos.engine.extractor import MemoryExtractor

MOCK_PATH = "memos.engine.extractor.requests.post"


class TestRetryByStatusCode:
    """验证 _request_with_retry 按错误码区分重试策略"""

    def setup_method(self):
        self.ext = MemoryExtractor()

    def _mock_resp(self, status_code=200, text="ok"):
        m = mock.Mock()
        m.status_code = status_code
        m.text = text
        m.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return m

    # --- 200: 正常返回 ---

    def test_200_returns_response(self):
        """200 直接返回，不重试"""
        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.return_value = self._mock_resp(200)
            result = self.ext._request_with_retry(
                {"messages": [{"role": "user", "content": "hi"}]},
                max_retries=3, base_delay=0.1,
            )
        assert result is not None
        assert result.status_code == 200
        assert mock_post.call_count == 1

    # --- 400: 不重试 ---

    def test_400_no_retry(self):
        """400 请求体错误，立即放弃不重试"""
        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.return_value = self._mock_resp(400, '{"error":"missing model"}')
            result = self.ext._request_with_retry(
                {"messages": [{"role": "user", "content": "hi"}]},
                max_retries=3, base_delay=0.1,
            )
        assert result is None
        assert mock_post.call_count == 1, "400 不应重试"

    def test_400_logs_payload_info(self):
        """400 日志输出 payload.model 和 messages 数量"""
        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.return_value = self._mock_resp(400, '{"error":"bad request"}')
            with mock.patch("memos.engine.extractor.logger") as mock_log:
                self.ext._request_with_retry(
                    {"messages": [{"role": "user", "content": "hi"}], "model": "test-model"},
                    max_retries=1, base_delay=0.1,
                )
            # 验证 error 级别的日志包含 payload 信息
            error_logs = [args for args, _ in mock_log.error.call_args_list]
            assert any("400" in str(a) for a in error_logs)
            assert any("test-model" in str(a) for a in error_logs)

    # --- 429: 激进退避 ---

    def test_429_aggressive_backoff(self):
        """429 限流时使用激进退避（4^attempt）"""
        call_times = []

        def _slow_post(*args, **kwargs):
            call_times.append(time.time())
            return self._mock_resp(429, "rate limited")

        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.side_effect = _slow_post
            # 2 次 429 后第 3 次成功
            success_resp = self._mock_resp(200)

            def _side(*args, **kwargs):
                call_times.append(time.time())
                if len(call_times) < 3:
                    return self._mock_resp(429, "rate limited")
                return success_resp

            mock_post.side_effect = _side
            result = self.ext._request_with_retry(
                {"messages": [{"role": "user", "content": "hi"}]},
                max_retries=3, base_delay=0.1,
            )
        assert result is not None
        assert result.status_code == 200
        # 验证退避时间递增
        if len(call_times) >= 3:
            gap1 = call_times[1] - call_times[0]
            gap2 = call_times[2] - call_times[1]
            assert gap2 > gap1, "429 退避时间应递增"

    def test_429_all_fail(self):
        """连续 429 达到重试上限后返回 None"""
        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.return_value = self._mock_resp(429, "rate limited")
            result = self.ext._request_with_retry(
                {"messages": [{"role": "user", "content": "hi"}]},
                max_retries=2, base_delay=0.05,
            )
        assert result is None
        assert mock_post.call_count == 2

    # --- 413: 截断重试 ---

    def test_413_truncates_and_retries(self):
        """413 payload 超限时截断 user 消息再重试"""
        original_content = "x" * 10000
        payload = {
            "messages": [
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": original_content},
            ]
        }

        sent_contents = []

        def _capture_and_respond(*args, **kwargs):
            sent = kwargs["json"]["messages"][1]["content"]
            sent_contents.append(sent)
            if len(sent_contents) == 1:
                return self._mock_resp(413, "too large")
            return self._mock_resp(200)

        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.side_effect = _capture_and_respond
            result = self.ext._request_with_retry(
                payload, max_retries=2, base_delay=0.05,
            )

        assert result is not None
        assert result.status_code == 200
        assert mock_post.call_count == 2
        # 第 1 次发送原始内容，第 2 次发送截断后内容
        assert sent_contents[0] == original_content
        assert len(sent_contents[1]) < len(original_content)
        assert "[内容被截断...]" in sent_contents[1]

    def test_413_no_user_message(self):
        """413 时没有 user 消息可截断 → 返回 None"""
        payload = {
            "messages": [
                {"role": "system", "content": "be helpful"},
            ]
        }

        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.return_value = self._mock_resp(413, "too large")
            result = self.ext._request_with_retry(
                payload, max_retries=2, base_delay=0.05,
            )
        assert result is None
        assert mock_post.call_count == 1, "无可截断消息时应立即放弃"

    # --- 5xx: 标准退避 ---

    def test_500_standard_backoff_then_succeed(self):
        """500 标准退避重试，最后成功"""
        call_count = [0]

        def _side(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                return self._mock_resp(500, "server error")
            return self._mock_resp(200)

        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.side_effect = _side
            result = self.ext._request_with_retry(
                {"messages": [{"role": "user", "content": "hi"}]},
                max_retries=3, base_delay=0.05,
            )
        assert result is not None
        assert result.status_code == 200
        assert call_count[0] == 3

    def test_500_all_fail(self):
        """连续 500 达到重试上限后返回 None"""
        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.return_value = self._mock_resp(500, "server error")
            result = self.ext._request_with_retry(
                {"messages": [{"role": "user", "content": "hi"}]},
                max_retries=2, base_delay=0.05,
            )
        assert result is None
        assert mock_post.call_count == 2

    # --- 异常: 连接错误等 ---

    def test_exception_retry(self):
        """网络异常时重试"""
        call_count = [0]

        def _side(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("connection refused")
            return self._mock_resp(200)

        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.side_effect = _side
            result = self.ext._request_with_retry(
                {"messages": [{"role": "user", "content": "hi"}]},
                max_retries=2, base_delay=0.05,
            )
        assert result is not None
        assert result.status_code == 200
        assert call_count[0] == 2

    def test_exception_all_fail(self):
        """一直异常达到重试上限"""
        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.side_effect = ConnectionError("always fail")
            result = self.ext._request_with_retry(
                {"messages": [{"role": "user", "content": "hi"}]},
                max_retries=2, base_delay=0.05,
            )
        assert result is None
        assert mock_post.call_count == 2

    # --- 混合场景 ---

    def test_mixed_status_codes(self):
        """多种状态码混合：400 直接放弃不重试"""
        with mock.patch(MOCK_PATH) as mock_post:
            mock_post.return_value = self._mock_resp(400)
            result = self.ext._request_with_retry(
                {"messages": [{"role": "user", "content": "hi"}]},
                max_retries=5, base_delay=0.1,
            )
        assert result is None
        assert mock_post.call_count == 1, "400 不重试，即使 max_retries=5"
