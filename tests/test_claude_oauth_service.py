from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.services.claude_oauth_service import (
    CLAUDE_CLIENT_ID,
    CLAUDE_REDIRECT_URI,
    ClaudeOAuthService,
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
    def __init__(self, *, oauth_proxy: str | None = None, oauth_verify_ssl: bool = False) -> None:
        self._oauth_proxy = oauth_proxy
        self._oauth_verify_ssl = oauth_verify_ssl

    def get_oauth_proxy(self) -> str | None:
        return self._oauth_proxy

    def is_oauth_verify_ssl_enabled(self) -> bool:
        return self._oauth_verify_ssl


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any],
        *,
        status_code: int = 200,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload)
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return dict(self._payload)

    def close(self) -> None:
        pass


class FakeRequestsSession:
    def __init__(self, *, post: Any = None) -> None:
        self._post = post
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        if self._post is None:
            raise AssertionError(f"Unexpected POST request: {url}")
        return self._post(url, **kwargs)

    def close(self) -> None:
        self.closed = True


def patch_requests_session(*, post: Any = None) -> Any:
    return patch(
        "src.services.claude_oauth_service.requests.Session",
        side_effect=lambda: FakeRequestsSession(post=post),
    )


class ClaudeOAuthServiceTests(unittest.TestCase):
    def _build_service(
        self,
        root_path: Path,
        config_manager: FakeConfigManager | None = None,
    ) -> ClaudeOAuthService:
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=config_manager,  # type: ignore[arg-type]
            root_path=root_path,
            flask_app=Flask(__name__),
        )
        return ClaudeOAuthService(ctx)

    def test_start_login_builds_claude_pkce_authorization_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._build_service(Path(tmp_dir))

            result = service.start_login()

        parsed = urlparse(result["authorization_url"])
        query = parse_qs(parsed.query)
        self.assertEqual("https", parsed.scheme)
        self.assertEqual("claude.ai", parsed.netloc)
        self.assertEqual("/oauth/authorize", parsed.path)
        self.assertEqual([CLAUDE_CLIENT_ID], query["client_id"])
        self.assertEqual([CLAUDE_REDIRECT_URI], query["redirect_uri"])
        self.assertEqual(["code"], query["response_type"])
        self.assertEqual(["true"], query["code"])
        self.assertEqual(["S256"], query["code_challenge_method"])
        self.assertIn("user:inference", query["scope"][0])
        self.assertTrue(result["state"])

    def test_complete_login_exchanges_json_body_and_writes_claude_auth_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._build_service(Path(tmp_dir))
            session = service.start_login()
            callback_url = f"{CLAUDE_REDIRECT_URI}?code=demo-code&state={session['state']}"
            captured: dict[str, Any] = {}

            def fake_post(url, json=None, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                captured["url"] = url
                captured["json"] = dict(json or {})
                captured["headers"] = dict(headers or {})
                captured["timeout"] = timeout
                captured["proxies"] = proxies
                captured["verify"] = verify
                return FakeResponse(
                    {
                        "access_token": "access-demo",
                        "refresh_token": "refresh-demo",
                        "expires_in": 3600,
                        "account": {
                            "uuid": "account-123",
                            "email_address": "claude@example.com",
                        },
                        "organization": {
                            "uuid": "org-123",
                            "name": "Demo Org",
                        },
                    }
                )

            with patch_requests_session(post=fake_post):
                result = service.complete_login(callback_url)

            auth_file = Path(result["auth_file"]["path"])
            payload = json.loads(auth_file.read_text(encoding="utf-8"))

        self.assertEqual("authorization_code", captured["json"]["grant_type"])
        self.assertIsNone(captured["proxies"])
        self.assertFalse(captured["verify"])
        self.assertEqual("demo-code", captured["json"]["code"])
        self.assertEqual(CLAUDE_REDIRECT_URI, captured["json"]["redirect_uri"])
        self.assertTrue(captured["json"]["code_verifier"])
        self.assertEqual("claude", payload["type"])
        self.assertEqual("access-demo", payload["access_token"])
        self.assertEqual("refresh-demo", payload["refresh_token"])
        self.assertEqual("claude@example.com", payload["email"])
        self.assertEqual("account-123", payload["account_uuid"])
        self.assertEqual("Demo Org", payload["organization_name"])
        self.assertEqual("claude-claude@example.com.json", auth_file.name)

    def test_complete_login_accepts_fragment_callback_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._build_service(Path(tmp_dir))
            session = service.start_login()
            callback_url = f"{CLAUDE_REDIRECT_URI}#code=demo-code&state={session['state']}"

            def fake_post(url, json=None, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                del url, headers, timeout, proxies, verify, kwargs
                self.assertEqual("demo-code", dict(json or {})["code"])
                return FakeResponse(
                    {
                        "access_token": "access-demo",
                        "refresh_token": "refresh-demo",
                        "expires_in": 3600,
                        "account": {
                            "email_address": "claude@example.com",
                        },
                    }
                )

            with patch_requests_session(post=fake_post):
                result = service.complete_login(callback_url)

        self.assertEqual("claude-claude@example.com.json", result["auth_file"]["name"])

    def test_complete_login_accepts_raw_code_state_callback_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._build_service(Path(tmp_dir))
            session = service.start_login()
            callback_url = f"demo-code#{session['state']}"

            def fake_post(url, json=None, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                del url, headers, timeout, proxies, verify, kwargs
                self.assertEqual("demo-code", dict(json or {})["code"])
                return FakeResponse(
                    {
                        "access_token": "access-demo",
                        "refresh_token": "refresh-demo",
                        "expires_in": 3600,
                        "account": {
                            "email_address": "claude@example.com",
                        },
                    }
                )

            with patch_requests_session(post=fake_post):
                result = service.complete_login(callback_url)

        self.assertEqual("claude-claude@example.com.json", result["auth_file"]["name"])

    def test_list_and_delete_auth_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "claude"
            auth_dir.mkdir(parents=True)
            auth_file = auth_dir / "claude-a@example.com.json"
            auth_file.write_text(
                json.dumps(
                    {
                        "type": "claude",
                        "access_token": "access-a",
                        "refresh_token": "refresh-a",
                        "email": "a@example.com",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)

            listed = service.list_auth_files()
            deleted = service.delete_auth_file("claude-a@example.com.json")

        self.assertEqual(1, listed["total"])
        self.assertEqual("available", listed["files"][0]["availability_status"])
        self.assertEqual("claude-a@example.com.json", deleted["deleted"])
        self.assertFalse(auth_file.exists())

    def test_models_file_is_not_listed_as_auth_file_and_controls_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "claude"
            auth_dir.mkdir(parents=True)
            auth_file = auth_dir / "claude-a@example.com.json"
            auth_file.write_text(
                json.dumps(
                    {
                        "type": "claude",
                        "access_token": "access-a",
                        "refresh_token": "refresh-a",
                        "email": "a@example.com",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)

            service.add_model("claude-sonnet-4-5")
            listed = service.list_auth_files()
            model_names = service.list_model_names()
            candidates = service.iter_auth_candidates_for_model("claude-sonnet-4-5")
            service.record_auth_file_failure("claude-a@example.com.json", "invalid bearer", status_code=401)
            skipped_candidates = service.iter_auth_candidates_for_model("claude-sonnet-4-5")
            service.record_auth_file_success("claude-a@example.com.json")
            recovered_candidates = service.iter_auth_candidates_for_model("claude-sonnet-4-5")

        self.assertEqual(["claude-a@example.com.json"], [item["name"] for item in listed["files"]])
        self.assertEqual(("claude-sonnet-4-5",), model_names)
        self.assertEqual(["claude-a@example.com.json"], [candidate.name for candidate in candidates])
        self.assertEqual([], [candidate.name for candidate in skipped_candidates])
        self.assertEqual(["claude-a@example.com.json"], [candidate.name for candidate in recovered_candidates])

    def test_expired_auth_candidate_refreshes_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "claude"
            auth_dir.mkdir(parents=True)
            auth_file = auth_dir / "claude-a@example.com.json"
            auth_file.write_text(
                json.dumps(
                    {
                        "type": "claude",
                        "access_token": "old-access",
                        "refresh_token": "refresh-a",
                        "email": "a@example.com",
                        "expired": "2000-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)
            service.add_model("claude-sonnet-4-5")
            captured: dict[str, Any] = {}

            def fake_post(url, json=None, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                del headers, timeout, proxies, verify, kwargs
                captured["url"] = url
                captured["json"] = dict(json or {})
                return FakeResponse(
                    {
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "expires_in": 3600,
                        "account": {
                            "email_address": "a@example.com",
                        },
                    }
                )

            with patch_requests_session(post=fake_post):
                candidates = service.iter_auth_candidates_for_model("claude-sonnet-4-5")
            payload = json.loads(auth_file.read_text(encoding="utf-8"))

        self.assertEqual("refresh_token", captured["json"]["grant_type"])
        self.assertEqual("refresh-a", captured["json"]["refresh_token"])
        self.assertEqual(["new-access"], [candidate.access_token for candidate in candidates])
        self.assertEqual("new-access", payload["access_token"])
        self.assertEqual("new-refresh", payload["refresh_token"])


if __name__ == "__main__":
    unittest.main()
