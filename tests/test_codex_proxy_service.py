from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.services.codex_oauth_service import CODEX_USER_AGENT, CodexOAuthService
from src.services.codex_proxy_service import (
    CODEX_BACKEND_RESPONSES_URL,
    CODEX_CLIENT_VERSION,
    CODEX_PROXY_WARNING_ERROR_CODE,
    CODEX_PROXY_WARNING_STATUS_CODE,
    CodexProxyService,
)


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _record(self, level: str, msg: str, *args: Any) -> None:
        try:
            message = msg % args if args else msg
        except TypeError:
            message = f"{msg} {args}"
        self.records.append((level, message))

    def info(self, msg: str, *args: Any) -> None:
        self._record("info", msg, *args)

    def warning(self, msg: str, *args: Any) -> None:
        self._record("warning", msg, *args)

    def error(self, msg: str, *args: Any) -> None:
        self._record("error", msg, *args)

    def debug(self, msg: str, *args: Any) -> None:
        self._record("debug", msg, *args)


class FakeConfigManager:
    def get_oauth_proxy(self) -> None:
        return None

    def is_oauth_verify_ssl_enabled(self) -> bool:
        return False

    def is_llm_request_debug_enabled(self) -> bool:
        return False


class FakeHTTPResponse:
    def __init__(
        self,
        *,
        status_code: int,
        chunks: list[bytes] | None = None,
        body: bytes = b"",
        text: str | None = None,
        headers: dict[str, str] | None = None,
        stream_error: BaseException | None = None,
    ) -> None:
        self.status_code = status_code
        self._chunks = list(chunks or [])
        self.content = body
        self.text = text if text is not None else body.decode("utf-8", errors="replace")
        self.headers = headers or {"Content-Type": "text/event-stream"}
        self.stream_error = stream_error
        self.closed = False

    def iter_content(self, chunk_size=None):
        del chunk_size
        yield from self._chunks
        if self.stream_error is not None:
            raise self.stream_error

    def json(self) -> Any:
        return json.loads(self.text)

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeHTTPResponse]) -> None:
        self._responses = list(responses)
        self.get_calls: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def get(self, url, **kwargs):
        self.get_calls.append((url, dict(kwargs)))
        return self._responses.pop(0)


def build_context(
    root_path: Path,
    config_manager: FakeConfigManager | None = None,
    logger: FakeLogger | None = None,
) -> AppContext:
    return AppContext(
        logger=logger or FakeLogger(),
        config_manager=config_manager or FakeConfigManager(),  # type: ignore[arg-type]
        root_path=root_path,
        flask_app=Flask(__name__),
    )


def write_auth_file(root: Path, name: str, token: str, *, mtime: int) -> None:
    auth_dir = root / "data" / "oauth" / "codex"
    auth_dir.mkdir(parents=True, exist_ok=True)
    path = auth_dir / name
    path.write_text(
        json.dumps(
            {
                "type": "codex",
                "email": f"{name}@example.com",
                "account_id": f"account-{token}",
                "access_token": token,
                "plan_type": "pro",
                "expired": "2999-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))


class CodexProxyServiceTests(unittest.TestCase):
    def test_codex_body_defaults_normalize_responses_payload(self) -> None:
        body: dict[str, Any] = {
            "model": "ignored",
            "input": "hello",
            "stream": False,
            "store": True,
            "parallel_tool_calls": False,
            "include": ["output_text"],
            "max_output_tokens": 100,
            "max_completion_tokens": 100,
            "temperature": 0.7,
            "top_p": 0.9,
            "truncation": "auto",
            "context_management": {"type": "auto"},
            "user": "downstream-user",
            "service_tier": "auto",
        }

        CodexProxyService._apply_codex_body_defaults(body, "gpt-5.4")

        self.assertEqual("gpt-5.4", body["model"])
        self.assertTrue(body["stream"])
        self.assertFalse(body["store"])
        self.assertTrue(body["parallel_tool_calls"])
        self.assertEqual(["reasoning.encrypted_content"], body["include"])
        self.assertEqual(
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                }
            ],
            body["input"],
        )
        for field in (
            "max_output_tokens",
            "max_completion_tokens",
            "temperature",
            "top_p",
            "truncation",
            "context_management",
            "user",
            "service_tier",
        ):
            self.assertNotIn(field, body)

    def test_codex_body_defaults_keep_allowed_tier_and_drop_unsupported_fields(self) -> None:
        body: dict[str, Any] = {
            "input": [
                {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": "rules"}],
                }
            ],
            "service_tier": "fast",
            "prompt_cache_retention": "24h",
            "safety_identifier": "user-123",
            "generate": {"summary": "auto"},
            "stream_options": {"include_usage": True},
        }

        CodexProxyService._apply_codex_body_defaults(body, "gpt-5.4")

        self.assertEqual("developer", body["input"][0]["role"])
        self.assertEqual("fast", body["service_tier"])
        self.assertNotIn("prompt_cache_retention", body)
        self.assertNotIn("safety_identifier", body)
        self.assertNotIn("generate", body)
        self.assertNotIn("stream_options", body)

        priority_body: dict[str, Any] = {"service_tier": "priority"}
        CodexProxyService._apply_codex_body_defaults(priority_body, "gpt-5.4")
        self.assertEqual("priority", priority_body["service_tier"])

    def test_codex_body_defaults_normalize_builtin_tool_aliases(self) -> None:
        body: dict[str, Any] = {
            "tools": [
                {"type": "web_search_preview"},
                {"type": "web_search_preview_2025_03_11"},
                {"type": "function", "name": "demo"},
            ],
            "tool_choice": {
                "type": "allowed_tools",
                "tools": [
                    {"type": "web_search_preview"},
                    {"type": "web_search_preview_2025_03_11"},
                ],
            },
        }

        CodexProxyService._apply_codex_body_defaults(body, "gpt-5.4")

        self.assertEqual("web_search", body["tools"][0]["type"])
        self.assertEqual("web_search", body["tools"][1]["type"])
        self.assertEqual("function", body["tools"][2]["type"])
        self.assertEqual("allowed_tools", body["tool_choice"]["type"])
        self.assertEqual("web_search", body["tool_choice"]["tools"][0]["type"])
        self.assertEqual("web_search", body["tool_choice"]["tools"][1]["type"])

        direct_choice_body: dict[str, Any] = {"tool_choice": {"type": "web_search_preview_2025_03_11"}}
        CodexProxyService._apply_codex_body_defaults(direct_choice_body, "gpt-5.4")
        self.assertEqual("web_search", direct_choice_body["tool_choice"]["type"])

    def test_codex_body_defaults_inject_image_generation_tool_with_default_model(self) -> None:
        body: dict[str, Any] = {
            "tools": [
                {"type": "function", "name": "lookup"},
            ],
        }

        CodexProxyService._apply_codex_body_defaults(
            body,
            "gpt-5.4",
            image_generation_model="gpt-image-2",
        )

        self.assertEqual("function", body["tools"][0]["type"])
        self.assertEqual(
            {
                "type": "image_generation",
                "output_format": "png",
                "model": "gpt-image-2",
            },
            body["tools"][1],
        )

        existing_tool_body: dict[str, Any] = {"tools": [{"type": "image_generation"}]}
        CodexProxyService._apply_codex_body_defaults(
            existing_tool_body,
            "gpt-5.4",
            image_generation_model="gpt-image-2",
        )
        self.assertEqual(1, len(existing_tool_body["tools"]))
        self.assertEqual("gpt-image-2", existing_tool_body["tools"][0]["model"])

    def test_codex_body_defaults_can_skip_image_generation_tool(self) -> None:
        body: dict[str, Any] = {}

        CodexProxyService._apply_codex_body_defaults(
            body,
            "gpt-5.4",
            image_generation_model="gpt-image-2",
            allow_image_generation=False,
        )

        self.assertNotIn("tools", body)

    def test_nonstream_request_falls_back_to_next_account_after_upstream_400(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            write_auth_file(root, "codex-second.json", "access-second", mtime=1000)
            first_auth_file = root / "data" / "oauth" / "codex" / "codex-first.json"
            first_payload = json.loads(first_auth_file.read_text(encoding="utf-8"))
            first_payload["expired"] = "2000-01-01T00:00:00Z"
            first_auth_file.write_text(json.dumps(first_payload), encoding="utf-8")
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            captured_authorizations: list[str] = []

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del json, timeout, kwargs
                self.assertEqual(CODEX_BACKEND_RESPONSES_URL, url)
                self.assertTrue(stream)
                authorization = str((headers or {}).get("Authorization") or "")
                captured_authorizations.append(authorization)
                if authorization == "Bearer access-first":
                    return FakeHTTPResponse(
                        status_code=400,
                        body=b'{"error":{"type":"invalid_request_error","message":"bad auth file"}}',
                        headers={"Content-Type": "application/json"},
                    )
                return FakeHTTPResponse(
                    status_code=200,
                    chunks=[
                        b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
                    ],
                )

            with patch.object(oauth_service, "get_auth_file_quota") as quota_mock:
                with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                    response, status_code, failure = proxy_service.proxy_request(
                        {
                            "model": "gpt-5.4",
                            "messages": [{"role": "user", "content": "hi"}],
                            "stream": False,
                            "store": True,
                            "include": ["output_text"],
                            "max_tokens": 200,
                            "temperature": 0.8,
                            "top_p": 0.9,
                            "user": "downstream-user",
                            "service_tier": "default",
                        },
                        {"Authorization": "Bearer downstream-token"},
                        resolved_target_format="openai_chat",
                    )
                quota_mock.assert_not_called()

            self.assertIsNone(failure)
            self.assertEqual(200, status_code)
            self.assertIsNotNone(response)
            payload = json.loads(response.get_data(as_text=True))  # type: ignore[union-attr]
            auth_entries = {entry["name"]: entry for entry in oauth_service.list_auth_files()["files"]}

        self.assertEqual(
            ["Bearer access-first", "Bearer access-second"],
            captured_authorizations,
        )
        self.assertEqual("ok", payload["choices"][0]["message"]["content"])
        self.assertEqual("error", auth_entries["codex-first.json"]["usage_status"])
        self.assertEqual(
            "bad auth file",
            auth_entries["codex-first.json"]["usage_status_message"],
        )
        self.assertEqual("success", auth_entries["codex-second.json"]["usage_status"])

    def test_upstream_400_logs_error_summary_for_claude_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-only.json", "access-only", mtime=2000)
            logger = FakeLogger()
            ctx = build_context(root, logger=logger)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del headers, json, timeout, kwargs
                self.assertEqual(CODEX_BACKEND_RESPONSES_URL, url)
                self.assertTrue(stream)
                return FakeHTTPResponse(
                    status_code=400,
                    body=b'{"detail":"model is not available for this Codex account"}',
                    headers={"Content-Type": "application/json", "X-Request-Id": "req_123"},
                )

            with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                response, status_code, failure = proxy_service.proxy_request(
                    {
                        "model": "gpt-5.4",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                    },
                    {"Authorization": "Bearer downstream-token"},
                    resolved_target_format="claude_chat",
                    trace_id="trace-codex-400",
                    route_name="messages",
                    client_ip="203.0.113.9",
                )

        self.assertIsNone(response)
        self.assertEqual(400, status_code)
        self.assertIsNotNone(failure)
        self.assertEqual("model is not available for this Codex account", failure.message)
        self.assertEqual(
            {"upstream_status": 400},
            failure.details,
        )

        warning_logs = "\n".join(message for level, message in logger.records if level == "warning")
        self.assertIn("Codex upstream error", warning_logs)
        self.assertIn("route=messages", warning_logs)
        self.assertIn("target_format=claude_chat", warning_logs)
        self.assertIn("model=gpt-5.4", warning_logs)
        self.assertIn("auth_file=codex-only.json", warning_logs)
        self.assertIn("model is not available for this Codex account", warning_logs)
        self.assertNotIn('{"detail"', warning_logs)

    def test_claude_downstream_codex_request_sanitizes_thinking_and_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-only.json", "access-only", mtime=2000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            long_tool_name = "mcp__server__" + ("tool_" * 20)
            long_call_id = "toolu_" + ("a" * 90)
            captured_body: dict[str, Any] = {}

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del headers, timeout, kwargs
                self.assertEqual(CODEX_BACKEND_RESPONSES_URL, url)
                self.assertTrue(stream)
                captured_body.update(dict(json or {}))
                return FakeHTTPResponse(
                    status_code=200,
                    chunks=[
                        b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
                    ],
                )

            with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                response, status_code, failure = proxy_service.proxy_request(
                    {
                        "model": "gpt-5.4",
                        "system": [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}],
                        "messages": [
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
                            },
                            {
                                "role": "assistant",
                                "content": [
                                    {"type": "thinking", "thinking": "hidden", "signature": "Eclaude-signature"},
                                    {"type": "tool_use", "id": long_call_id, "name": long_tool_name, "input": {"x": 1}},
                                ],
                            },
                            {
                                "role": "user",
                                "content": [{"type": "tool_result", "tool_use_id": long_call_id, "content": "ok"}],
                            },
                        ],
                        "tools": [
                            {
                                "name": long_tool_name,
                                "description": "lookup",
                                "input_schema": {
                                    "$schema": "http://json-schema.org/draft-07/schema#",
                                    "type": "object",
                                    "properties": {"x": {"type": "number"}},
                                },
                                "cache_control": {"type": "ephemeral"},
                                "defer_loading": True,
                            }
                        ],
                        "metadata": {"user_id": "claude-code-user"},
                        "tool_choice": {"type": "tool", "name": long_tool_name},
                        "stream": False,
                    },
                    {"Authorization": "Bearer downstream-token"},
                    resolved_target_format="claude_chat",
                )

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)

        input_items = captured_body["input"]
        self.assertFalse(any(item.get("type") == "reasoning" for item in input_items if isinstance(item, dict)))
        function_call = next(item for item in input_items if item.get("type") == "function_call")
        function_output = next(item for item in input_items if item.get("type") == "function_call_output")
        self.assertLessEqual(len(function_call["call_id"]), 64)
        self.assertEqual(function_call["call_id"], function_output["call_id"])
        self.assertLessEqual(len(function_call["name"]), 64)
        self.assertEqual(function_call["name"], captured_body["tools"][0]["name"])
        self.assertEqual(function_call["name"], captured_body["tool_choice"]["name"])
        self.assertFalse(captured_body["tools"][0]["strict"])
        self.assertNotIn("$schema", captured_body["tools"][0]["parameters"])
        self.assertNotIn("metadata", captured_body)
        self.assertNotIn("cache_control", json.dumps(captured_body))

    def test_upstream_400_usage_limit_is_treated_as_quota_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-only.json", "access-only", mtime=2000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del headers, json, timeout, kwargs
                self.assertEqual(CODEX_BACKEND_RESPONSES_URL, url)
                self.assertTrue(stream)
                return FakeHTTPResponse(
                    status_code=400,
                    body=b'{"error":{"type":"usage_limit_reached","message":"usage limit","resets_in_seconds":120}}',
                    headers={"Content-Type": "application/json"},
                )

            with patch.object(oauth_service, "refresh_auth_file_quota_snapshot") as refresh_mock:
                with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                    response, status_code, failure = proxy_service.proxy_request(
                        {
                            "model": "gpt-5.4",
                            "messages": [{"role": "user", "content": "hi"}],
                            "stream": False,
                        },
                        {"Authorization": "Bearer downstream-token"},
                        resolved_target_format="claude_chat",
                    )
                refresh_mock.assert_called_once_with("codex-only.json")

        self.assertIsNone(response)
        self.assertEqual(429, status_code)
        self.assertIsNotNone(failure)
        self.assertEqual("codex_quota_exhausted", failure.error_code)
        self.assertEqual({"Retry-After": 120.0}, failure.response_headers)

    def test_nonstream_request_falls_back_to_next_account_after_quota_429(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            write_auth_file(root, "codex-second.json", "access-second", mtime=1000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            captured_headers: list[dict[str, str]] = []
            captured_bodies: list[dict[str, Any]] = []
            quota_get_calls: list[dict[str, Any]] = []

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                self.assertEqual(CODEX_BACKEND_RESPONSES_URL, url)
                self.assertTrue(stream)
                self.assertEqual(1200, timeout)
                self.assertFalse(kwargs["verify"])
                captured_headers.append(dict(headers or {}))
                captured_bodies.append(dict(json or {}))
                if headers and headers.get("Authorization") == "Bearer access-first":
                    return FakeHTTPResponse(
                        status_code=429,
                        body=b'{"error":{"type":"usage_limit_reached","resets_in_seconds":60}}',
                        headers={"Content-Type": "application/json"},
                    )
                return FakeHTTPResponse(
                    status_code=200,
                    chunks=[
                        b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
                    ],
                )

            class FakeQuotaSession:
                def get(self, url, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                    quota_get_calls.append(
                        {
                            "url": url,
                            "headers": dict(headers or {}),
                            "timeout": timeout,
                            "proxies": proxies,
                            "verify": verify,
                            "kwargs": dict(kwargs),
                        }
                    )
                    return FakeHTTPResponse(
                        status_code=200,
                        body=b'{"plan_type":"plus","rate_limit":{"primary_window":{"used_percent":100,"reset_after_seconds":3600}}}',
                    )

                def close(self) -> None:
                    pass

            with patch("src.services.codex_oauth_service.requests.Session", side_effect=FakeQuotaSession):
                with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                    response, status_code, failure = proxy_service.proxy_request(
                        {
                            "model": "gpt-5.4",
                            "messages": [{"role": "user", "content": "hi"}],
                            "stream": False,
                        },
                        {"Authorization": "Bearer downstream-token"},
                        resolved_target_format="openai_chat",
                    )

            self.assertIsNone(failure)
            self.assertEqual(200, status_code)
            self.assertIsNotNone(response)
            payload = json.loads(response.get_data(as_text=True))  # type: ignore[union-attr]
            auth_entries = {entry["name"]: entry for entry in oauth_service.list_auth_files()["files"]}

        self.assertEqual(
            ["Bearer access-first", "Bearer access-second"],
            [headers["Authorization"] for headers in captured_headers],
        )
        self.assertEqual("account-access-second", captured_headers[1]["Chatgpt-Account-Id"])
        self.assertTrue(all(body["stream"] is True for body in captured_bodies))
        self.assertTrue(all(body["store"] is False for body in captured_bodies))
        self.assertTrue(all(body["parallel_tool_calls"] is True for body in captured_bodies))
        self.assertTrue(all(headers.get("Version", "") == CODEX_CLIENT_VERSION for headers in captured_headers))
        self.assertTrue(all("codex-tui/0.135.0" in headers["User-Agent"] for headers in captured_headers))
        self.assertTrue(all(headers["Originator"] == "codex-tui" for headers in captured_headers))
        self.assertTrue(all(headers["Session_id"] for headers in captured_headers))
        self.assertTrue(all(body["include"] == ["reasoning.encrypted_content"] for body in captured_bodies))
        self.assertTrue(all("max_output_tokens" not in body for body in captured_bodies))
        self.assertTrue(all("temperature" not in body for body in captured_bodies))
        self.assertTrue(all("top_p" not in body for body in captured_bodies))
        self.assertTrue(all("user" not in body for body in captured_bodies))
        self.assertTrue(all("service_tier" not in body for body in captured_bodies))
        self.assertEqual("chat.completion", payload["object"])
        self.assertEqual("ok", payload["choices"][0]["message"]["content"])
        self.assertEqual(3, payload["usage"]["total_tokens"])
        self.assertEqual("error", auth_entries["codex-first.json"]["usage_status"])
        self.assertEqual(
            "usage_limit_reached",
            auth_entries["codex-first.json"]["usage_status_message"],
        )
        self.assertEqual(1, len(quota_get_calls))
        self.assertEqual("Bearer access-first", quota_get_calls[0]["headers"]["Authorization"])
        self.assertEqual(0.0, auth_entries["codex-first.json"]["quota"]["windows"][0]["remaining_percent"])
        self.assertEqual("", auth_entries["codex-first.json"]["quota_error"])
        self.assertEqual("quota_cooldown", auth_entries["codex-first.json"]["availability_status"])
        self.assertEqual("success", auth_entries["codex-second.json"]["usage_status"])

    def test_codex_headers_sanitize_downstream_user_agent_and_keep_codex_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            captured_headers: dict[str, str] = {}

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del url, json, stream, timeout, kwargs
                captured_headers.update(dict(headers or {}))
                return FakeHTTPResponse(
                    status_code=200,
                    chunks=[
                        b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
                    ],
                )

            with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                response, status_code, failure = proxy_service.proxy_request(
                    {
                        "model": "gpt-5.4",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                    },
                    {
                        "Version": "0.115.0-alpha.27",
                        "User-Agent": "custom-codex/9.9",
                        "Originator": "custom-origin",
                        "X-Codex-Beta-Features": "responses",
                        "X-Codex-Turn-Metadata": "turn-meta",
                        "X-Client-Request-Id": "request-id",
                        "Cookie": "session=downstream",
                        "Host": "example.invalid",
                    },
                    resolved_target_format="openai_chat",
                )

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)
        self.assertEqual("0.115.0-alpha.27", captured_headers["Version"])
        self.assertEqual(CODEX_USER_AGENT, captured_headers["User-Agent"])
        self.assertEqual("custom-origin", captured_headers["Originator"])
        self.assertEqual("responses", captured_headers["X-Codex-Beta-Features"])
        self.assertEqual("turn-meta", captured_headers["X-Codex-Turn-Metadata"])
        self.assertEqual("request-id", captured_headers["X-Client-Request-Id"])
        self.assertNotIn("Cookie", captured_headers)
        self.assertNotIn("Host", captured_headers)

    def test_image_generation_request_uses_codex_tool_and_returns_images_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            captured_body: dict[str, Any] = {}
            captured_headers: dict[str, str] = {}
            completed_event = {
                "type": "response.completed",
                "response": {
                    "id": "resp_image",
                    "created_at": 1770000000,
                    "model": "gpt-5.4",
                    "tool_usage": {
                        "image_gen": {
                            "input_tokens": 4,
                            "output_tokens": 6,
                            "total_tokens": 10,
                        }
                    },
                    "output": [
                        {
                            "type": "image_generation_call",
                            "output_format": "png",
                            "result": "aGVsbG8=",
                            "revised_prompt": "draw a tidy diagram",
                        }
                    ],
                },
            }
            completed_chunk = f"data: {json.dumps(completed_event)}\n\n".encode("utf-8")

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del timeout, kwargs
                self.assertEqual(CODEX_BACKEND_RESPONSES_URL, url)
                self.assertTrue(stream)
                captured_headers.update(dict(headers or {}))
                captured_body.update(dict(json or {}))
                return FakeHTTPResponse(
                    status_code=200,
                    chunks=[completed_chunk],
                )

            complete_meta: dict[str, Any] = {}
            with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                response, status_code, failure = proxy_service.proxy_image_request(
                    {
                        "prompt": "draw",
                        "model": "gpt-image-2",
                        "response_format": "url",
                        "size": "1024x1024",
                    },
                    {"Authorization": "Bearer downstream-token"},
                    action="generate",
                    on_complete=complete_meta.update,
                )
            payload = json.loads(response.get_data(as_text=True))  # type: ignore[union-attr]

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertEqual("Bearer access-first", captured_headers["Authorization"])
        self.assertEqual({"type": "image_generation"}, captured_body["tool_choice"])
        self.assertEqual("image_generation", captured_body["tools"][0]["type"])
        self.assertEqual("generate", captured_body["tools"][0]["action"])
        self.assertEqual("gpt-image-2", captured_body["tools"][0]["model"])
        self.assertEqual("1024x1024", captured_body["tools"][0]["size"])
        self.assertEqual("draw", captured_body["input"][0]["content"][0]["text"])
        self.assertEqual(1770000000, payload["created"])
        self.assertEqual("data:image/png;base64,aGVsbG8=", payload["data"][0]["url"])
        self.assertEqual("draw a tidy diagram", payload["data"][0]["revised_prompt"])
        self.assertEqual(10, payload["usage"]["total_tokens"])
        self.assertEqual("gpt-image-2", complete_meta["response_model"])
        self.assertEqual(10, complete_meta["total_tokens"])

    def test_proxy_warning_confirmation_failure_returns_confirmation_url_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            write_auth_file(root, "codex-second.json", "access-second", mtime=1000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            confirmation_url = "http://114.114.114.114:9421/proxycontrolwarn/httpwarning_3355.html?ori_url=demo"
            captured_authorizations: list[str] = []
            fake_session = FakeSession(
                [
                    FakeHTTPResponse(status_code=200, text="<html></html>"),
                ]
            )

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del json, timeout
                self.assertEqual(CODEX_BACKEND_RESPONSES_URL, url)
                self.assertTrue(stream)
                self.assertFalse(kwargs["allow_redirects"])
                captured_authorizations.append(str((headers or {}).get("Authorization") or ""))
                return FakeHTTPResponse(
                    status_code=302,
                    headers={
                        "Server": "netentsec",
                        "Location": confirmation_url,
                    },
                )

            with patch.object(oauth_service, "get_auth_file_quota") as quota_mock:
                with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                    with patch("src.utils.proxy_warning.requests.Session", return_value=fake_session):
                        response, status_code, failure = proxy_service.proxy_request(
                            {
                                "model": "gpt-5.4",
                                "messages": [{"role": "user", "content": "hi"}],
                                "stream": False,
                            },
                            {"Authorization": "Bearer downstream-token"},
                            resolved_target_format="openai_chat",
                        )
                quota_mock.assert_not_called()

        self.assertIsNone(response)
        self.assertEqual(CODEX_PROXY_WARNING_STATUS_CODE, status_code)
        self.assertIsNotNone(failure)
        self.assertEqual(CODEX_PROXY_WARNING_ERROR_CODE, failure.error_code)
        self.assertEqual(confirmation_url, failure.details["confirmation_url"])  # type: ignore[index]
        self.assertIn("auto_confirm_error", failure.details)  # type: ignore[operator]
        self.assertIn(confirmation_url, failure.message)  # type: ignore[union-attr]
        self.assertEqual(["Bearer access-first"], captured_authorizations)

    def test_proxy_warning_auto_confirm_retries_same_account_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            write_auth_file(root, "codex-second.json", "access-second", mtime=1000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            confirmation_url = (
                "http://114.114.114.114:9421/proxycontrolwarn/"
                "httpwarning_3355.html?ori_url=aHR0cHM6Ly9jaGF0Z3B0LmNvbS8="
            )
            warning_html = """
                <input id="sessionid" value="session-123" />
                <input id="pid" value="3355" />
                <input id="uid" value="0" />
            """
            fake_session = FakeSession(
                [
                    FakeHTTPResponse(status_code=200, text=warning_html),
                    FakeHTTPResponse(status_code=200, text="ok"),
                ]
            )
            captured_authorizations: list[str] = []

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del json, timeout
                self.assertEqual(CODEX_BACKEND_RESPONSES_URL, url)
                self.assertTrue(stream)
                self.assertFalse(kwargs["allow_redirects"])
                captured_authorizations.append(str((headers or {}).get("Authorization") or ""))
                if len(captured_authorizations) == 1:
                    return FakeHTTPResponse(
                        status_code=302,
                        headers={
                            "Server": "netentsec",
                            "Location": confirmation_url,
                        },
                    )
                return FakeHTTPResponse(
                    status_code=200,
                    chunks=[
                        b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
                    ],
                )

            with patch.object(oauth_service, "get_auth_file_quota") as quota_mock:
                with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                    with patch("src.utils.proxy_warning.requests.Session", return_value=fake_session):
                        response, status_code, failure = proxy_service.proxy_request(
                            {
                                "model": "gpt-5.4",
                                "messages": [{"role": "user", "content": "hi"}],
                                "stream": False,
                            },
                            {"Authorization": "Bearer downstream-token"},
                            resolved_target_format="openai_chat",
                        )
                quota_mock.assert_not_called()
            payload = json.loads(response.get_data(as_text=True))  # type: ignore[union-attr]

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertEqual("ok", payload["choices"][0]["message"]["content"])
        self.assertEqual(["Bearer access-first", "Bearer access-first"], captured_authorizations)
        self.assertEqual(2, len(fake_session.get_calls))
        self.assertEqual(confirmation_url, fake_session.get_calls[0][0])
        self.assertTrue(fake_session.get_calls[1][0].startswith("http://114.114.114.114:9421/proxycontrolwarn/check?"))
        self.assertFalse(fake_session.get_calls[0][1]["allow_redirects"])
        self.assertFalse(fake_session.get_calls[1][1]["allow_redirects"])

    def test_authentication_error_marks_auth_file_unavailable_and_skips_next_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            write_auth_file(root, "codex-second.json", "access-second", mtime=1000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            captured_authorizations: list[str] = []

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del json, timeout, kwargs
                self.assertEqual(CODEX_BACKEND_RESPONSES_URL, url)
                self.assertTrue(stream)
                authorization = str((headers or {}).get("Authorization") or "")
                captured_authorizations.append(authorization)
                if authorization == "Bearer access-first":
                    return FakeHTTPResponse(
                        status_code=401,
                        body=b'{"error":{"type":"authentication_error","message":"invalid or expired token"}}',
                        headers={"Content-Type": "application/json"},
                    )
                return FakeHTTPResponse(
                    status_code=200,
                    chunks=[
                        b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
                    ],
                )

            with patch.object(oauth_service, "get_auth_file_quota") as quota_mock:
                with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                    response, status_code, failure = proxy_service.proxy_request(
                        {
                            "model": "gpt-5.4",
                            "messages": [{"role": "user", "content": "hi"}],
                            "stream": False,
                        },
                        {"Authorization": "Bearer downstream-token"},
                        resolved_target_format="openai_chat",
                    )
                next_candidates = oauth_service.iter_auth_candidates_for_model("gpt-5.4")
                quota_mock.assert_not_called()
            auth_entries = {entry["name"]: entry for entry in oauth_service.list_auth_files()["files"]}

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)
        self.assertEqual(
            ["Bearer access-first", "Bearer access-second"],
            captured_authorizations,
        )
        self.assertEqual(["codex-second.json"], [candidate.name for candidate in next_candidates])
        self.assertEqual("auth_failed", auth_entries["codex-first.json"]["availability_status"])
        self.assertIn("认证失败：上游返回", auth_entries["codex-first.json"]["availability_status_message"])
        self.assertIn("invalid or expired token", auth_entries["codex-first.json"]["availability_status_message"])
        self.assertEqual("authentication_error", auth_entries["codex-first.json"]["usage_error_type"])
        self.assertEqual(
            "invalid or expired token",
            auth_entries["codex-first.json"]["usage_status_message"],
        )

    def test_authentication_error_refreshes_current_auth_file_and_retries_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True, exist_ok=True)
            auth_file = auth_dir / "codex-first.json"
            auth_file.write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "account_id": "account-old",
                        "access_token": "old-access",
                        "refresh_token": "refresh-old",
                        "plan_type": "pro",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            captured_authorizations: list[str] = []
            refresh_requests: list[dict[str, Any]] = []

            def fake_codex_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del json, timeout, kwargs
                self.assertEqual(CODEX_BACKEND_RESPONSES_URL, url)
                self.assertTrue(stream)
                authorization = str((headers or {}).get("Authorization") or "")
                captured_authorizations.append(authorization)
                if authorization == "Bearer old-access":
                    return FakeHTTPResponse(
                        status_code=401,
                        body=b'{"error":{"type":"authentication_error","message":"invalid or expired token"}}',
                        headers={"Content-Type": "application/json"},
                    )
                return FakeHTTPResponse(
                    status_code=200,
                    chunks=[
                        b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
                    ],
                )

            class FakeOAuthSession:
                def post(self, url, data=None, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                    del headers, timeout, proxies, verify, kwargs
                    refresh_requests.append({"url": url, "data": dict(data or {})})
                    return FakeHTTPResponse(
                        status_code=200,
                        body=b'{"access_token":"new-access","refresh_token":"refresh-new","expires_in":3600}',
                    )

                def close(self) -> None:
                    pass

            with patch.object(oauth_service, "get_auth_file_quota") as quota_mock:
                with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_codex_post):
                    with patch("src.services.codex_oauth_service.requests.Session", side_effect=FakeOAuthSession):
                        response, status_code, failure = proxy_service.proxy_request(
                            {
                                "model": "gpt-5.4",
                                "messages": [{"role": "user", "content": "hi"}],
                                "stream": False,
                            },
                            {"Authorization": "Bearer downstream-token"},
                            resolved_target_format="openai_chat",
                        )
                quota_mock.assert_not_called()
            next_payload = json.loads(auth_file.read_text(encoding="utf-8"))
            auth_entries = {entry["name"]: entry for entry in oauth_service.list_auth_files()["files"]}

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)
        self.assertEqual(["Bearer old-access", "Bearer new-access"], captured_authorizations)
        self.assertEqual("refresh_token", refresh_requests[0]["data"]["grant_type"])
        self.assertEqual("refresh-old", refresh_requests[0]["data"]["refresh_token"])
        self.assertEqual("new-access", next_payload["access_token"])
        self.assertEqual("refresh-new", next_payload["refresh_token"])
        self.assertEqual("success", auth_entries["codex-first.json"]["usage_status"])

    def test_stream_failure_before_first_downstream_chunk_uses_next_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            write_auth_file(root, "codex-second.json", "access-second", mtime=1000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            authorizations: list[str] = []
            completed_meta: list[dict[str, Any]] = []
            first_response = FakeHTTPResponse(
                status_code=200,
                stream_error=requests.exceptions.ChunkedEncodingError("first stream broke"),
            )
            second_response = FakeHTTPResponse(
                status_code=200,
                chunks=[
                    b'data: {"type":"response.created","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000}}\n\n',
                    b'data: {"type":"response.output_text.delta","item_id":"msg_1","output_index":0,"delta":"ok"}\n\n',
                    b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n',
                ],
            )

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del url, json, stream, timeout, kwargs
                authorizations.append(str((headers or {}).get("Authorization") or ""))
                return first_response if len(authorizations) == 1 else second_response

            with patch.object(
                oauth_service,
                "record_auth_file_failure",
                wraps=oauth_service.record_auth_file_failure,
            ) as record_failure:
                with ctx.flask_app.test_request_context("/v1/chat/completions"):
                    with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                        response, status_code, failure = proxy_service.proxy_request(
                            {
                                "model": "gpt-5.4",
                                "messages": [{"role": "user", "content": "hi"}],
                                "stream": True,
                            },
                            {},
                            on_complete=lambda meta: completed_meta.append(dict(meta)),
                            resolved_target_format="openai_chat",
                        )
                    streamed = b"".join(response.response)  # type: ignore[union-attr]
            auth_entries = {entry["name"]: entry for entry in oauth_service.list_auth_files()["files"]}

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertEqual(["Bearer access-first", "Bearer access-second"], authorizations)
        self.assertIn(b"ok", streamed)
        self.assertTrue(first_response.closed)
        self.assertTrue(second_response.closed)
        self.assertEqual("error", auth_entries["codex-first.json"]["usage_status"])
        self.assertEqual("codex_stream_failed", auth_entries["codex-first.json"]["usage_error_type"])
        self.assertEqual("success", auth_entries["codex-second.json"]["usage_status"])
        self.assertEqual(1, len(completed_meta))
        record_failure.assert_called_once()

    def test_stream_precommit_oserror_and_clean_eof_use_next_account(self) -> None:
        for failure_mode in ("connect_oserror", "clean_eof"):
            with self.subTest(failure_mode=failure_mode), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
                write_auth_file(root, "codex-second.json", "access-second", mtime=1000)
                ctx = build_context(root)
                oauth_service = CodexOAuthService(ctx)
                oauth_service.add_model("gpt-5.4")
                proxy_service = CodexProxyService(ctx, oauth_service)
                authorizations: list[str] = []
                first_response = FakeHTTPResponse(status_code=200)
                second_response = FakeHTTPResponse(
                    status_code=200,
                    chunks=[
                        b'data: {"type":"response.created","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000}}\n\n',
                        b'data: {"type":"response.output_text.delta","item_id":"msg_1","output_index":0,"delta":"ok"}\n\n',
                        b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n',
                    ],
                )

                def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                    del url, json, stream, timeout, kwargs
                    authorizations.append(str((headers or {}).get("Authorization") or ""))
                    if len(authorizations) == 1:
                        if failure_mode == "connect_oserror":
                            raise OSError("socket failed")
                        return first_response
                    return second_response

                with patch.object(
                    oauth_service,
                    "record_auth_file_failure",
                    wraps=oauth_service.record_auth_file_failure,
                ) as record_failure:
                    with ctx.flask_app.test_request_context("/v1/chat/completions"):
                        with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                            response, status_code, failure = proxy_service.proxy_request(
                                self._build_stream_request("openai_chat"),
                                {},
                                resolved_target_format="openai_chat",
                            )
                        streamed = b"".join(response.response)  # type: ignore[union-attr]
                auth_entries = {entry["name"]: entry for entry in oauth_service.list_auth_files()["files"]}

                self.assertIsNone(failure)
                self.assertEqual(200, status_code)
                self.assertEqual(["Bearer access-first", "Bearer access-second"], authorizations)
                self.assertIn(b"ok", streamed)
                self.assertEqual("error", auth_entries["codex-first.json"]["usage_status"])
                self.assertEqual("success", auth_entries["codex-second.json"]["usage_status"])
                record_failure.assert_called_once()
                if failure_mode == "clean_eof":
                    self.assertTrue(first_response.closed)
                self.assertTrue(second_response.closed)

    def test_nonstream_upstream_read_error_uses_next_account_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            write_auth_file(root, "codex-second.json", "access-second", mtime=1000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            authorizations: list[str] = []
            first_response = FakeHTTPResponse(
                status_code=200,
                stream_error=requests.exceptions.ChunkedEncodingError("nonstream read failed"),
            )
            second_response = FakeHTTPResponse(
                status_code=200,
                chunks=[
                    b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
                ],
            )

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del url, json, stream, timeout, kwargs
                authorizations.append(str((headers or {}).get("Authorization") or ""))
                return first_response if len(authorizations) == 1 else second_response

            with patch.object(
                oauth_service,
                "record_auth_file_failure",
                wraps=oauth_service.record_auth_file_failure,
            ) as record_failure:
                with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                    response, status_code, failure = proxy_service.proxy_request(
                        {
                            "model": "gpt-5.4",
                            "messages": [{"role": "user", "content": "hi"}],
                            "stream": False,
                        },
                        {},
                        resolved_target_format="openai_chat",
                    )
            payload = json.loads(response.get_data(as_text=True))  # type: ignore[union-attr]
            auth_entries = {entry["name"]: entry for entry in oauth_service.list_auth_files()["files"]}

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertEqual(["Bearer access-first", "Bearer access-second"], authorizations)
        self.assertEqual("ok", payload["choices"][0]["message"]["content"])
        self.assertTrue(first_response.closed)
        self.assertTrue(second_response.closed)
        self.assertEqual("error", auth_entries["codex-first.json"]["usage_status"])
        self.assertEqual("success", auth_entries["codex-second.json"]["usage_status"])
        record_failure.assert_called_once()

    def test_stream_transport_error_after_start_emits_target_error_without_completion_callback(self) -> None:
        target_cases = {
            "openai_chat": (b'"type": "upstream_stream_error"', b"data: [DONE]"),
            "openai_responses": (b"event: response.failed", None),
            "claude_chat": (b"event: error", None),
        }
        for failure_mode in ("transport_error", "clean_eof"):
            for target_format, (expected, absent) in target_cases.items():
                with (
                    self.subTest(failure_mode=failure_mode, target_format=target_format),
                    tempfile.TemporaryDirectory() as tmp_dir,
                ):
                    root = Path(tmp_dir)
                    write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
                    ctx = build_context(root)
                    oauth_service = CodexOAuthService(ctx)
                    oauth_service.add_model("gpt-5.4")
                    proxy_service = CodexProxyService(ctx, oauth_service)
                    completed_meta: list[dict[str, Any]] = []
                    fake_response = FakeHTTPResponse(
                        status_code=200,
                        chunks=[
                            b'data: {"type":"response.created","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000}}\n\n',
                            b'data: {"type":"response.output_text.delta","item_id":"msg_1","output_index":0,"delta":"partial"}\n\n',
                        ]
                        + ([b"data: [DONE]\n\n"] if failure_mode == "clean_eof" else []),
                        stream_error=(
                            requests.exceptions.ChunkedEncodingError("stream broke after output")
                            if failure_mode == "transport_error"
                            else None
                        ),
                    )

                    with ctx.flask_app.test_request_context("/v1/chat/completions"):
                        with patch(
                            "src.services.codex_proxy_service.requests.post",
                            return_value=fake_response,
                        ):
                            response, status_code, failure = proxy_service.proxy_request(
                                self._build_stream_request(target_format),
                                {},
                                on_complete=lambda meta: completed_meta.append(dict(meta)),
                                resolved_target_format=target_format,
                            )
                        streamed = b"".join(response.response)  # type: ignore[union-attr]
                    auth_entry = oauth_service.list_auth_files()["files"][0]

                    self.assertIsNone(failure)
                    self.assertEqual(200, status_code)
                    self.assertIn(expected, streamed)
                    if absent is None:
                        self.assertNotIn(b"data: [DONE]", streamed)
                    else:
                        self.assertEqual(1, streamed.count(absent))
                    self.assertIn(b"partial", streamed)
                    self.assertTrue(fake_response.closed)
                    self.assertEqual(1, len(completed_meta))
                    self.assertEqual("unknown", completed_meta[0]["usage_status"])
                    self.assertEqual("error", auth_entry["usage_status"])
                    self.assertEqual("codex_stream_failed", auth_entry["usage_error_type"])

    def test_nonstream_response_done_succeeds_and_cancelled_fails(self) -> None:
        terminal_cases = {
            "response.done": None,
            "response.cancelled": "cancelled upstream",
        }
        for event_type, error_message in terminal_cases.items():
            with self.subTest(event_type=event_type), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
                ctx = build_context(root)
                oauth_service = CodexOAuthService(ctx)
                oauth_service.add_model("gpt-5.4")
                proxy_service = CodexProxyService(ctx, oauth_service)
                completed_meta: list[dict[str, Any]] = []
                response_payload: dict[str, Any] = {
                    "id": "resp_1",
                    "model": "gpt-5.4",
                    "created_at": 1770000000,
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                }
                if error_message is not None:
                    response_payload["error"] = {
                        "type": "cancelled",
                        "message": error_message,
                    }
                event_payload = {"type": event_type, "response": response_payload}
                fake_response = FakeHTTPResponse(
                    status_code=200,
                    chunks=[f"data: {json.dumps(event_payload)}\n\n".encode()],
                )

                with patch("src.services.codex_proxy_service.requests.post", return_value=fake_response):
                    response, status_code, failure = proxy_service.proxy_request(
                        {
                            "model": "gpt-5.4",
                            "messages": [{"role": "user", "content": "hi"}],
                            "stream": False,
                        },
                        {},
                        on_complete=lambda meta: completed_meta.append(dict(meta)),
                        resolved_target_format="openai_chat",
                    )
                auth_entry = oauth_service.list_auth_files()["files"][0]

                if event_type == "response.done":
                    self.assertIsNone(failure)
                    self.assertEqual(200, status_code)
                    payload = json.loads(response.get_data(as_text=True))  # type: ignore[union-attr]
                    self.assertEqual("ok", payload["choices"][0]["message"]["content"])
                    self.assertEqual(1, len(completed_meta))
                    self.assertEqual("success", auth_entry["usage_status"])
                else:
                    self.assertIsNone(response)
                    self.assertEqual(502, status_code)
                    self.assertEqual("codex_stream_incomplete", failure.error_code)  # type: ignore[union-attr]
                    self.assertEqual([], completed_meta)
                    self.assertEqual("error", auth_entry["usage_status"])
                self.assertTrue(fake_response.closed)

    def test_stream_framing_error_after_terminal_keeps_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            completed_meta: list[dict[str, Any]] = []
            fake_response = FakeHTTPResponse(
                status_code=200,
                chunks=[
                    b'data: {"type":"response.created","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000}}\n\n',
                    b'data: {"type":"response.output_text.delta","item_id":"msg_1","output_index":0,"delta":"ok"}\n\n',
                    b'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000,"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n',
                ],
                stream_error=requests.exceptions.ChunkedEncodingError("trailing framing error"),
            )

            with ctx.flask_app.test_request_context("/v1/chat/completions"):
                with patch("src.services.codex_proxy_service.requests.post", return_value=fake_response):
                    response, status_code, failure = proxy_service.proxy_request(
                        self._build_stream_request("openai_chat"),
                        {},
                        on_complete=lambda meta: completed_meta.append(dict(meta)),
                        resolved_target_format="openai_chat",
                    )
                streamed = b"".join(response.response)  # type: ignore[union-attr]
            auth_entry = oauth_service.list_auth_files()["files"][0]

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertEqual(1, streamed.count(b"data: [DONE]"))
        self.assertNotIn(b"upstream_stream_error", streamed)
        self.assertEqual(1, len(completed_meta))
        self.assertEqual(3, completed_meta[0]["total_tokens"])
        self.assertEqual("success", auth_entry["usage_status"])
        self.assertTrue(fake_response.closed)

    def test_stream_close_records_unknown_usage_without_auth_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            completed_meta: list[dict[str, Any]] = []
            fake_response = FakeHTTPResponse(
                status_code=200,
                chunks=[
                    b'data: {"type":"response.created","response":{"id":"resp_1","model":"gpt-5.4","created_at":1770000000}}\n\n',
                    b'data: {"type":"response.output_text.delta","item_id":"msg_1","output_index":0,"delta":"partial"}\n\n',
                    b'data: {"type":"response.output_text.delta","item_id":"msg_1","output_index":0,"delta":"more"}\n\n',
                ],
            )

            with ctx.flask_app.test_request_context("/v1/chat/completions"):
                with patch("src.services.codex_proxy_service.requests.post", return_value=fake_response):
                    response, status_code, failure = proxy_service.proxy_request(
                        self._build_stream_request("openai_chat"),
                        {},
                        on_complete=lambda meta: completed_meta.append(dict(meta)),
                        resolved_target_format="openai_chat",
                    )
                first_chunk = next(iter(response.response))  # type: ignore[union-attr]
                response.close()  # type: ignore[union-attr]
            auth_entry = oauth_service.list_auth_files()["files"][0]

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertIn(b"partial", first_chunk)
        self.assertEqual(1, len(completed_meta))
        self.assertEqual("unknown", completed_meta[0]["usage_status"])
        self.assertEqual("unknown", auth_entry["usage_status"])
        self.assertTrue(fake_response.closed)

    def test_stream_top_level_error_is_forwarded_and_marks_auth_file_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)
            completed_meta: list[dict[str, Any]] = []

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del url, headers, json, stream, timeout, kwargs
                return FakeHTTPResponse(
                    status_code=200,
                    chunks=[
                        b'data: {"type":"error","error":{"type":"invalid_request_error","code":"context_too_large","message":"too many tokens"}}\n\n'
                    ],
                )

            with ctx.flask_app.test_request_context("/v1/chat/completions"):
                with patch("src.services.codex_proxy_service.requests.post", side_effect=fake_post):
                    response, status_code, failure = proxy_service.proxy_request(
                        {
                            "model": "gpt-5.4",
                            "messages": [{"role": "user", "content": "hi"}],
                            "stream": True,
                        },
                        {},
                        on_complete=lambda meta: completed_meta.append(dict(meta)),
                        resolved_target_format="openai_chat",
                    )
                streamed = b"".join(response.response)  # type: ignore[union-attr]
            auth_entries = {entry["name"]: entry for entry in oauth_service.list_auth_files()["files"]}

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertIn(b'"error"', streamed)
        self.assertIn(b"too many tokens", streamed)
        self.assertEqual("error", auth_entries["codex-first.json"]["usage_status"])
        self.assertEqual("too many tokens", auth_entries["codex-first.json"]["usage_status_message"])
        self.assertEqual("codex_stream_failed", auth_entries["codex-first.json"]["usage_error_type"])
        self.assertEqual(1, len(completed_meta))

    @staticmethod
    def _build_stream_request(target_format: str) -> dict[str, Any]:
        if target_format == "openai_responses":
            return {
                "model": "gpt-5.4",
                "input": "hi",
                "stream": True,
            }
        if target_format == "claude_chat":
            return {
                "model": "gpt-5.4",
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                "max_tokens": 64,
                "stream": True,
            }
        return {
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }


if __name__ == "__main__":
    unittest.main()
