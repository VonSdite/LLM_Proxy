from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.proxy_warning import (
    ProxyWarningRequired,
    request_with_proxy_warning_retry,
)


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append((url, dict(kwargs)))
        return self._responses.pop(0)

    def close(self) -> None:
        self.closed = True


class ProxyWarningTests(unittest.TestCase):
    def test_request_with_proxy_warning_retry_confirms_and_retries_once(self) -> None:
        confirmation_url = (
            "http://114.114.114.114:9421/proxycontrolwarn/httpwarning_3355.html?ori_url=aHR0cHM6Ly9jaGF0Z3B0LmNvbS8="
        )
        warning_response = FakeResponse(
            status_code=302,
            headers={"Location": confirmation_url},
        )
        success_response = FakeResponse(status_code=200)
        sent_responses = [warning_response, success_response]
        confirm_session = FakeSession(
            [
                FakeResponse(
                    status_code=200,
                    text="""
                        <input id="sessionid" value="session-123" />
                        <input id="pid" value="3355" />
                        <input id="uid" value="0" />
                    """,
                ),
                FakeResponse(status_code=200),
            ]
        )

        def send_request() -> FakeResponse:
            return sent_responses.pop(0)

        response = request_with_proxy_warning_retry(
            send_request,
            request_options={"proxies": None, "verify": False},
            session_factory=lambda: confirm_session,
        )

        self.assertIs(success_response, response)
        self.assertTrue(warning_response.closed)
        self.assertTrue(confirm_session.closed)
        self.assertEqual(2, len(confirm_session.get_calls))
        self.assertEqual(confirmation_url, confirm_session.get_calls[0][0])
        self.assertTrue(
            confirm_session.get_calls[1][0].startswith("http://114.114.114.114:9421/proxycontrolwarn/check?")
        )
        self.assertFalse(confirm_session.get_calls[0][1]["allow_redirects"])
        self.assertFalse(confirm_session.get_calls[1][1]["allow_redirects"])

    def test_request_with_proxy_warning_retry_raises_details_on_confirm_failure(self) -> None:
        confirmation_url = "http://114.114.114.114:9421/proxycontrolwarn/httpwarning_3355.html?ori_url=demo"
        confirm_session = FakeSession(
            [
                FakeResponse(status_code=200, text="<html></html>"),
            ]
        )

        def send_request() -> FakeResponse:
            return FakeResponse(
                status_code=302,
                headers={"Location": confirmation_url},
            )

        with self.assertRaises(ProxyWarningRequired) as raised:
            request_with_proxy_warning_retry(
                send_request,
                session_factory=lambda: confirm_session,
            )

        details = raised.exception.to_details()
        self.assertEqual(confirmation_url, details["confirmation_url"])
        self.assertEqual(302, details["upstream_status"])
        self.assertIn("missing hidden field", details["auto_confirm_error"])


if __name__ == "__main__":
    unittest.main()
