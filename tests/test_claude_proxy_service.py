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
from src.services.anthropic_billing import (
    CLAUDE_CCH_SEED,
    _json_body_bytes_for_requests,
    _xxhash64,
)
from src.services.claude_oauth_service import ClaudeOAuthService
from src.services.claude_proxy_service import (
    CLAUDE_MESSAGES_URL,
    CLAUDE_PACKAGE_VERSION,
    CLAUDE_USER_AGENT,
    ClaudeProxyService,
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
        self.headers = headers or {"Content-Type": "application/json"}
        self.stream_error = stream_error
        self.closed = False

    def iter_content(self, chunk_size=None):
        del chunk_size
        yield from self._chunks
        if self.stream_error is not None:
            raise self.stream_error

    def close(self) -> None:
        self.closed = True


def build_context(root_path: Path) -> AppContext:
    return AppContext(
        logger=FakeLogger(),
        config_manager=FakeConfigManager(),  # type: ignore[arg-type]
        root_path=root_path,
        flask_app=Flask(__name__),
    )


def write_auth_file(root: Path, name: str, token: str, *, mtime: int) -> None:
    auth_dir = root / "data" / "oauth" / "claude"
    auth_dir.mkdir(parents=True, exist_ok=True)
    path = auth_dir / name
    path.write_text(
        json.dumps(
            {
                "type": "claude",
                "email": f"{name}@example.com",
                "access_token": token,
                "refresh_token": f"refresh-{token}",
                "expired": "2999-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))


class ClaudeProxyServiceTests(unittest.TestCase):
    def test_nonstream_openai_chat_request_uses_claude_oauth_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "claude-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = ClaudeOAuthService(ctx)
            oauth_service.add_model("claude-sonnet-4-5")
            proxy_service = ClaudeProxyService(ctx, oauth_service)
            captured: dict[str, Any] = {}

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                captured["url"] = url
                captured["headers"] = dict(headers or {})
                captured["json"] = dict(json or {})
                captured["stream"] = stream
                captured["timeout"] = timeout
                captured["kwargs"] = dict(kwargs)
                return FakeHTTPResponse(
                    status_code=200,
                    body=json_module_dumps(
                        {
                            "id": "msg_1",
                            "type": "message",
                            "role": "assistant",
                            "model": "claude-sonnet-4-5",
                            "content": [{"type": "text", "text": "ok"}],
                            "stop_reason": "end_turn",
                            "usage": {"input_tokens": 1, "output_tokens": 2},
                        }
                    ),
                )

            with patch("src.services.claude_proxy_service.requests.post", side_effect=fake_post):
                response, status_code, failure = proxy_service.proxy_request(
                    {
                        "model": "claude-sonnet-4-5",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                        "max_tokens": 512,
                    },
                    {"Authorization": "Bearer downstream-token", "Anthropic-Beta": "custom-beta"},
                    resolved_target_format="openai_chat",
                )
            auth_entries = {entry["name"]: entry for entry in oauth_service.list_auth_files()["files"]}
            payload = json.loads(response.get_data(as_text=True))  # type: ignore[union-attr]

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertEqual(CLAUDE_MESSAGES_URL, captured["url"])
        self.assertFalse(captured["stream"])
        self.assertEqual(1200, captured["timeout"])
        self.assertFalse(captured["kwargs"]["verify"])
        self.assertEqual("Bearer access-first", captured["headers"]["Authorization"])
        self.assertEqual("application/json", captured["headers"]["Content-Type"])
        self.assertEqual("2023-06-01", captured["headers"]["Anthropic-Version"])
        self.assertIn("custom-beta", captured["headers"]["Anthropic-Beta"])
        self.assertIn("oauth-2025-04-20", captured["headers"]["Anthropic-Beta"])
        self.assertEqual("cli", captured["headers"]["X-App"])
        self.assertEqual(CLAUDE_USER_AGENT, captured["headers"]["User-Agent"])
        self.assertEqual(CLAUDE_PACKAGE_VERSION, captured["headers"]["X-Stainless-Package-Version"])
        self.assertEqual("claude-sonnet-4-5", captured["json"]["model"])
        self.assertEqual(512, captured["json"]["max_tokens"])
        self.assertFalse(captured["json"]["stream"])
        self.assertEqual("ok", payload["choices"][0]["message"]["content"])
        self.assertEqual(3, payload["usage"]["total_tokens"])
        self.assertEqual("success", auth_entries["claude-first.json"]["usage_status"])

    def test_falls_back_to_next_account_after_auth_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "claude-first.json", "access-first", mtime=2000)
            write_auth_file(root, "claude-second.json", "access-second", mtime=1000)
            ctx = build_context(root)
            oauth_service = ClaudeOAuthService(ctx)
            oauth_service.add_model("claude-sonnet-4-5")
            proxy_service = ClaudeProxyService(ctx, oauth_service)
            authorizations: list[str] = []

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del url, json, stream, timeout, kwargs
                authorization = str((headers or {}).get("Authorization") or "")
                authorizations.append(authorization)
                if authorization == "Bearer access-first":
                    return FakeHTTPResponse(
                        status_code=401,
                        body=b'{"error":{"type":"authentication_error","message":"invalid bearer token"}}',
                    )
                return FakeHTTPResponse(
                    status_code=200,
                    body=json_module_dumps(
                        {
                            "id": "msg_1",
                            "type": "message",
                            "role": "assistant",
                            "model": "claude-sonnet-4-5",
                            "content": [{"type": "text", "text": "ok"}],
                            "stop_reason": "end_turn",
                            "usage": {"input_tokens": 1, "output_tokens": 2},
                        }
                    ),
                )

            with patch("src.services.claude_proxy_service.requests.post", side_effect=fake_post):
                response, status_code, failure = proxy_service.proxy_request(
                    {
                        "model": "claude-sonnet-4-5",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                    },
                    {},
                    resolved_target_format="openai_chat",
                )
                next_candidates = oauth_service.iter_auth_candidates_for_model("claude-sonnet-4-5")
            auth_entries = {entry["name"]: entry for entry in oauth_service.list_auth_files()["files"]}

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)
        self.assertEqual(["Bearer access-first", "Bearer access-second"], authorizations)
        self.assertEqual(["claude-second.json"], [candidate.name for candidate in next_candidates])
        self.assertEqual("auth_failed", auth_entries["claude-first.json"]["availability_status"])
        self.assertEqual("authentication_error", auth_entries["claude-first.json"]["usage_error_type"])
        self.assertEqual("success", auth_entries["claude-second.json"]["usage_status"])

    def test_claude_passthrough_request_resigns_billing_header_like_cpa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "claude-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = ClaudeOAuthService(ctx)
            oauth_service.add_model("claude-sonnet-4-5")
            proxy_service = ClaudeProxyService(ctx, oauth_service)
            captured: dict[str, Any] = {}

            def fake_post(url, headers=None, json=None, stream=None, timeout=None, **kwargs):
                del url, headers, stream, timeout, kwargs
                captured["json"] = dict(json or {})
                return FakeHTTPResponse(
                    status_code=200,
                    body=json_module_dumps(
                        {
                            "id": "msg_1",
                            "type": "message",
                            "role": "assistant",
                            "model": "claude-sonnet-4-5",
                            "content": [{"type": "text", "text": "ok"}],
                            "stop_reason": "end_turn",
                            "usage": {"input_tokens": 1, "output_tokens": 2},
                        }
                    ),
                )

            with patch("src.services.claude_proxy_service.requests.post", side_effect=fake_post):
                response, status_code, failure = proxy_service.proxy_request(
                    {
                        "model": "claude-sonnet-4-5",
                        "system": [
                            {
                                "type": "text",
                                "text": (
                                    "x-anthropic-billing-header: cc_version=2.1.70.abc; cc_entrypoint=cli; cch=00000;"
                                ),
                            }
                        ],
                        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                        "stream": False,
                        "max_tokens": 256,
                    },
                    {},
                    resolved_target_format="claude_chat",
                )

        self.assertIsNone(failure)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)
        signed_text = captured["json"]["system"][0]["text"]
        cch_prefix, cch_suffix = signed_text.split("cch=", 1)
        unsigned_body = dict(captured["json"])
        unsigned_system = [dict(item) for item in unsigned_body["system"]]
        unsigned_system[0]["text"] = f"{cch_prefix}cch=00000;{cch_suffix.split(';', 1)[1]}"
        unsigned_body["system"] = unsigned_system
        expected_cch = _xxhash64(_json_body_bytes_for_requests(unsigned_body), CLAUDE_CCH_SEED) & 0xFFFFF
        self.assertIn(f"cch={expected_cch:05x};", signed_text)
        self.assertNotIn("cch=00000;", signed_text)

    def test_stream_failure_before_first_downstream_chunk_uses_next_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "claude-first.json", "access-first", mtime=2000)
            write_auth_file(root, "claude-second.json", "access-second", mtime=1000)
            ctx = build_context(root)
            oauth_service = ClaudeOAuthService(ctx)
            oauth_service.add_model("claude-sonnet-4-5")
            proxy_service = ClaudeProxyService(ctx, oauth_service)
            authorizations: list[str] = []
            completed_meta: list[dict[str, Any]] = []
            first_response = FakeHTTPResponse(
                status_code=200,
                headers={"Content-Type": "text/event-stream"},
                stream_error=requests.exceptions.ChunkedEncodingError("first stream broke"),
            )
            second_response = FakeHTTPResponse(
                status_code=200,
                headers={"Content-Type": "text/event-stream"},
                chunks=self._successful_claude_stream_chunks(),
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
                    with patch("src.services.claude_proxy_service.requests.post", side_effect=fake_post):
                        response, status_code, failure = proxy_service.proxy_request(
                            self._build_stream_request("openai_chat"),
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
        self.assertEqual("error", auth_entries["claude-first.json"]["usage_status"])
        self.assertEqual("claude_stream_failed", auth_entries["claude-first.json"]["usage_error_type"])
        self.assertEqual("success", auth_entries["claude-second.json"]["usage_status"])
        self.assertEqual(1, len(completed_meta))
        record_failure.assert_called_once()

    def test_stream_precommit_oserror_and_clean_eof_use_next_account(self) -> None:
        for failure_mode in ("connect_oserror", "clean_eof"):
            with self.subTest(failure_mode=failure_mode), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                write_auth_file(root, "claude-first.json", "access-first", mtime=2000)
                write_auth_file(root, "claude-second.json", "access-second", mtime=1000)
                ctx = build_context(root)
                oauth_service = ClaudeOAuthService(ctx)
                oauth_service.add_model("claude-sonnet-4-5")
                proxy_service = ClaudeProxyService(ctx, oauth_service)
                authorizations: list[str] = []
                first_response = FakeHTTPResponse(
                    status_code=200,
                    headers={"Content-Type": "text/event-stream"},
                )
                second_response = FakeHTTPResponse(
                    status_code=200,
                    headers={"Content-Type": "text/event-stream"},
                    chunks=self._successful_claude_stream_chunks(),
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
                        with patch("src.services.claude_proxy_service.requests.post", side_effect=fake_post):
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
                self.assertEqual("error", auth_entries["claude-first.json"]["usage_status"])
                self.assertEqual("success", auth_entries["claude-second.json"]["usage_status"])
                record_failure.assert_called_once()
                if failure_mode == "clean_eof":
                    self.assertTrue(first_response.closed)
                self.assertTrue(second_response.closed)

    def test_nonstream_upstream_read_error_uses_next_account_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "claude-first.json", "access-first", mtime=2000)
            write_auth_file(root, "claude-second.json", "access-second", mtime=1000)
            ctx = build_context(root)
            oauth_service = ClaudeOAuthService(ctx)
            oauth_service.add_model("claude-sonnet-4-5")
            proxy_service = ClaudeProxyService(ctx, oauth_service)
            authorizations: list[str] = []
            first_response = FakeHTTPResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                stream_error=requests.exceptions.ChunkedEncodingError("nonstream read failed"),
            )
            first_response.content = None
            second_response = FakeHTTPResponse(
                status_code=200,
                body=json_module_dumps(
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-sonnet-4-5",
                        "content": [{"type": "text", "text": "ok"}],
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": 1, "output_tokens": 2},
                    }
                ),
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
                with patch("src.services.claude_proxy_service.requests.post", side_effect=fake_post):
                    response, status_code, failure = proxy_service.proxy_request(
                        {
                            "model": "claude-sonnet-4-5",
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
        self.assertEqual("error", auth_entries["claude-first.json"]["usage_status"])
        self.assertEqual("success", auth_entries["claude-second.json"]["usage_status"])
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
                    write_auth_file(root, "claude-first.json", "access-first", mtime=2000)
                    ctx = build_context(root)
                    oauth_service = ClaudeOAuthService(ctx)
                    oauth_service.add_model("claude-sonnet-4-5")
                    proxy_service = ClaudeProxyService(ctx, oauth_service)
                    completed_meta: list[dict[str, Any]] = []
                    fake_response = FakeHTTPResponse(
                        status_code=200,
                        headers={"Content-Type": "text/event-stream"},
                        chunks=[
                            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"claude-sonnet-4-5","content":[],"usage":{"input_tokens":1,"output_tokens":0}}}\n\n',
                            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
                            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"partial"}}\n\n',
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
                            "src.services.claude_proxy_service.requests.post",
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
                    self.assertEqual(1, completed_meta[0]["prompt_tokens"])
                    self.assertEqual("partial", completed_meta[0]["usage_status"])
                    self.assertEqual("error", auth_entry["usage_status"])
                    self.assertEqual("claude_stream_failed", auth_entry["usage_error_type"])

    def test_stream_framing_error_after_terminal_keeps_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "claude-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = ClaudeOAuthService(ctx)
            oauth_service.add_model("claude-sonnet-4-5")
            proxy_service = ClaudeProxyService(ctx, oauth_service)
            completed_meta: list[dict[str, Any]] = []
            fake_response = FakeHTTPResponse(
                status_code=200,
                headers={"Content-Type": "text/event-stream"},
                chunks=self._successful_claude_stream_chunks(),
                stream_error=requests.exceptions.ChunkedEncodingError("trailing framing error"),
            )

            with ctx.flask_app.test_request_context("/v1/chat/completions"):
                with patch("src.services.claude_proxy_service.requests.post", return_value=fake_response):
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

    def test_stream_close_records_partial_usage_without_auth_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "claude-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = ClaudeOAuthService(ctx)
            oauth_service.add_model("claude-sonnet-4-5")
            proxy_service = ClaudeProxyService(ctx, oauth_service)
            completed_meta: list[dict[str, Any]] = []
            fake_response = FakeHTTPResponse(
                status_code=200,
                headers={"Content-Type": "text/event-stream"},
                chunks=[
                    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"claude-sonnet-4-5","content":[],"usage":{"input_tokens":1,"output_tokens":0}}}\n\n',
                    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
                    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"partial"}}\n\n',
                ],
            )

            with ctx.flask_app.test_request_context("/v1/chat/completions"):
                with patch("src.services.claude_proxy_service.requests.post", return_value=fake_response):
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
        self.assertIn(b"assistant", first_chunk)
        self.assertEqual(1, len(completed_meta))
        self.assertEqual(1, completed_meta[0]["prompt_tokens"])
        self.assertEqual("partial", completed_meta[0]["usage_status"])
        self.assertEqual("unknown", auth_entry["usage_status"])
        self.assertTrue(fake_response.closed)

    def test_stream_upstream_error_keeps_existing_callback_and_auth_failure_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_auth_file(root, "claude-first.json", "access-first", mtime=2000)
            ctx = build_context(root)
            oauth_service = ClaudeOAuthService(ctx)
            oauth_service.add_model("claude-sonnet-4-5")
            proxy_service = ClaudeProxyService(ctx, oauth_service)
            completed_meta: list[dict[str, Any]] = []
            fake_response = FakeHTTPResponse(
                status_code=200,
                headers={"Content-Type": "text/event-stream"},
                chunks=[
                    b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"busy"}}\n\n'
                ],
            )

            with ctx.flask_app.test_request_context("/v1/chat/completions"):
                with patch("src.services.claude_proxy_service.requests.post", return_value=fake_response):
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
        self.assertIn(b"busy", streamed)
        self.assertEqual(1, streamed.count(b"data: [DONE]"))
        self.assertEqual(1, len(completed_meta))
        self.assertEqual("error", auth_entry["usage_status"])
        self.assertEqual("claude_stream_failed", auth_entry["usage_error_type"])

    def test_xxhash64_known_vector(self) -> None:
        self.assertEqual(0xEF46DB3751D8E999, _xxhash64(b""))

    @staticmethod
    def _build_stream_request(target_format: str) -> dict[str, Any]:
        if target_format == "openai_responses":
            return {
                "model": "claude-sonnet-4-5",
                "input": "hi",
                "stream": True,
            }
        if target_format == "claude_chat":
            return {
                "model": "claude-sonnet-4-5",
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                "max_tokens": 64,
                "stream": True,
            }
        return {
            "model": "claude-sonnet-4-5",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }

    @staticmethod
    def _successful_claude_stream_chunks() -> list[bytes]:
        return [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"claude-sonnet-4-5","content":[],"usage":{"input_tokens":1,"output_tokens":0}}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":2}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]


def json_module_dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
