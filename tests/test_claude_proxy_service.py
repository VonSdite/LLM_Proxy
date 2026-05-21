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
from src.services.claude_oauth_service import ClaudeOAuthService
from src.services.claude_proxy_service import (
    CLAUDE_CCH_SEED,
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
    ) -> None:
        self.status_code = status_code
        self._chunks = list(chunks or [])
        self.content = body
        self.text = text if text is not None else body.decode("utf-8", errors="replace")
        self.headers = headers or {"Content-Type": "application/json"}
        self.closed = False

    def iter_content(self, chunk_size=None):
        del chunk_size
        yield from self._chunks

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
                                    "x-anthropic-billing-header: cc_version=2.1.70.abc; "
                                    "cc_entrypoint=cli; cch=00000;"
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
        expected_cch = (
            ClaudeProxyService._xxhash64(
                ClaudeProxyService._json_body_bytes_for_requests(unsigned_body),
                CLAUDE_CCH_SEED,
            )
            & 0xFFFFF
        )
        self.assertIn(f"cch={expected_cch:05x};", signed_text)
        self.assertNotIn("cch=00000;", signed_text)

    def test_xxhash64_known_vector(self) -> None:
        self.assertEqual(0xEF46DB3751D8E999, ClaudeProxyService._xxhash64(b""))


def json_module_dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
