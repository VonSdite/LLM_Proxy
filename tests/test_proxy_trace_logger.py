from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.proxy_trace_logger import ProxyTraceLogger


class ProxyTraceLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config: dict = {"auth_groups": []}
        self.config_manager = Mock()
        self.config_manager.is_llm_request_debug_enabled.return_value = True
        self.config_manager.get.side_effect = self.config.get
        self.logger = Mock()
        self.trace = ProxyTraceLogger(self.config_manager, self.logger)

    def _log(self, stage: str, headers: dict) -> str:
        self.trace.log_entry(
            stage=stage,
            trace_id="trace-redaction",
            start_line="POST /v1/chat/completions HTTP/1.1",
            headers=headers,
            payload={"model": "demo/model", "messages": []},
        )
        return self.logger.info.call_args.args[0]

    def test_sensitive_headers_are_masked_in_all_trace_stages(self) -> None:
        names = (
            "Authorization",
            "Proxy-Authorization",
            "X-API-Key",
            "API-Key",
            "X-Goog-API-Key",
            "Ocp-Apim-Subscription-Key",
            "Cookie",
            "Set-Cookie",
            "X-Auth-Token",
            "X-Access-Token",
            "X-CSRF-Token",
            "X-XSRF-Token",
        )
        headers = {name.swapcase(): f"secret-{index}" for index, name in enumerate(names)}
        headers["Content-Type"] = "application/json"
        headers["X-Request-Id"] = "request-123"
        original = copy.deepcopy(headers)
        for stage in ("downstream_request", "upstream_request", "upstream_response", "downstream_response"):
            with self.subTest(stage=stage):
                output = self._log(stage, headers)
                for name in names:
                    self.assertIn(f"{name.title()}: [REDACTED]", output)
                self.assertNotIn("secret-", output)
                self.assertIn("Content-Type: application/json", output)
                self.assertIn("X-Request-Id: request-123", output)
                self.assertIn('"model": "demo/model"', output)
                self.assertEqual(original, headers)

    def test_sensitive_header_values_are_masked_before_string_conversion(self) -> None:
        secret_value = MagicMock()
        output = self._log(
            "upstream_response",
            {
                " Authorization ": secret_value,
                "X-API-Key": b"binary-secret",
                "Set-Cookie": ["session=first-secret", "session=second-secret"],
            },
        )
        self.assertEqual(3, output.count(": [REDACTED]"))
        self.assertNotIn("secret", output)
        secret_value.__str__.assert_not_called()

    def test_custom_auth_group_header_names_follow_current_config(self) -> None:
        for stage in ("downstream_request", "upstream_request", "upstream_response", "downstream_response"):
            with self.subTest(stage=stage):
                self.config["auth_groups"] = [
                    {
                        "name": "pool",
                        "entries": [
                            {"headers": {"X-Vendor-Credential": "configured-secret"}},
                            {"enabled": False, "headers": {"X-Disabled-Credential": "disabled-secret"}},
                        ],
                    }
                ]
                headers = {
                    "x-vendor-credential": "overridden-secret",
                    "X-Disabled-Credential": "disabled-secret",
                    "X-Request-Id": "request-123",
                }
                output = self._log(stage, headers)
                self.assertIn("X-Vendor-Credential: [REDACTED]", output)
                self.assertIn("X-Disabled-Credential: [REDACTED]", output)
                self.assertNotIn("secret", output)
                self.assertIn("X-Request-Id: request-123", output)

                self.config["auth_groups"][0]["entries"].append({"headers": {"X-New-Credential": "new-secret"}})
                output = self._log(stage, {"X-New-Credential": "new-secret"})
                self.assertIn("X-New-Credential: [REDACTED]", output)
                self.assertNotIn("new-secret", output)

    def test_disabled_trace_does_not_read_auth_groups_or_emit_logs(self) -> None:
        self.config_manager.is_llm_request_debug_enabled.return_value = False
        self.trace.log_entry(
            stage="upstream_request",
            trace_id="trace-disabled",
            start_line="POST /v1/chat/completions HTTP/1.1",
            headers={"Authorization": "Bearer secret"},
            payload={},
        )
        self.config_manager.get.assert_not_called()
        self.logger.info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
