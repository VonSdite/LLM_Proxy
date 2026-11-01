import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.proxy_trace_logger import ProxyTraceLogger


class FakeConfigManager:
    def __init__(self, *, llm_request_debug_enabled: bool = True) -> None:
        self._llm_request_debug_enabled = llm_request_debug_enabled

    def is_llm_request_debug_enabled(self) -> bool:
        return self._llm_request_debug_enabled


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, msg: str, *args) -> None:
        self.messages.append(msg)


class ProxyTraceLoggerRedactionTest(unittest.TestCase):
    def test_auth_and_cookie_headers_are_redacted(self) -> None:
        logger = FakeLogger()
        trace = ProxyTraceLogger(FakeConfigManager(), logger)
        trace.log_entry(
            stage="upstream_request",
            trace_id="trace-1",
            start_line="POST https://example.org/v1/chat",
            headers={
                "Authorization": "Bearer secret-token",
                "X-Api-Key": "provider-key",
                "Cookie": "session=abc",
                "Content-Type": "application/json",
            },
            payload={},
        )
        self.assertEqual(1, len(logger.messages))
        message = logger.messages[0]
        self.assertNotIn("secret-token", message)
        self.assertNotIn("provider-key", message)
        self.assertNotIn("session=abc", message)
        self.assertIn("Authorization: ***REDACTED***", message)
        self.assertIn("X-Api-Key: ***REDACTED***", message)
        self.assertIn("Cookie: ***REDACTED***", message)
        self.assertIn("Content-Type: application/json", message)

    def test_set_cookie_redacted_in_response_trace(self) -> None:
        logger = FakeLogger()
        trace = ProxyTraceLogger(FakeConfigManager(), logger)
        trace.log_entry(
            stage="upstream_response",
            trace_id="trace-1",
            start_line="HTTP/1.1 200 OK",
            headers={"Set-Cookie": "session=xyz", "Content-Type": "application/json"},
            payload={},
        )
        message = logger.messages[0]
        self.assertNotIn("session=xyz", message)
        self.assertIn("Set-Cookie: ***REDACTED***", message)


if __name__ == "__main__":
    unittest.main()
