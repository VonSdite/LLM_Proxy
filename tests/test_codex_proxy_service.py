from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

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
    def info(self, msg: str, *args: Any) -> None:
        del msg, args

    def warning(self, msg: str, *args: Any) -> None:
        del msg, args

    def error(self, msg: str, *args: Any) -> None:
        del msg, args

    def debug(self, msg: str, *args: Any) -> None:
        del msg, args


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
    ) -> None:
        self.status_code = status_code
        self._chunks = list(chunks or [])
        self.content = body
        self.text = text if text is not None else body.decode("utf-8", errors="replace")
        self.headers = headers or {"Content-Type": "text/event-stream"}
        self.closed = False

    def iter_content(self, chunk_size=None):
        del chunk_size
        yield from self._chunks

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
) -> AppContext:
    return AppContext(
        logger=FakeLogger(),
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

    def test_codex_body_defaults_keep_allowed_tiers_fields_and_developer_role(self) -> None:
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
        }

        CodexProxyService._apply_codex_body_defaults(body, "gpt-5.4")

        self.assertEqual("developer", body["input"][0]["role"])
        self.assertEqual("fast", body["service_tier"])
        self.assertEqual("24h", body["prompt_cache_retention"])
        self.assertEqual("user-123", body["safety_identifier"])

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
                quota_mock.assert_not_called()

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

    def test_stream_top_level_error_is_forwarded_and_marks_auth_file_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "codex-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = CodexOAuthService(ctx)
            oauth_service.add_model("gpt-5.4")
            proxy_service = CodexProxyService(ctx, oauth_service)

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


if __name__ == "__main__":
    unittest.main()
