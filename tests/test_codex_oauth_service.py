from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.services.codex_oauth_service import (
    CODEX_CLIENT_ID,
    CODEX_MODEL_REFERENCE_URLS,
    CODEX_REDIRECT_URI,
    CodexOAuthService,
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
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return dict(self._payload)


def build_id_token(
    *,
    email: str = "codex@example.com",
    account_id: str = "account-123",
    plan_type: str = "plus",
) -> str:
    header = {"alg": "none"}
    payload = {
        "email": email,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan_type,
        },
    }

    def encode(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


class CodexOAuthServiceTests(unittest.TestCase):
    def _build_service(
        self,
        root_path: Path,
        config_manager: FakeConfigManager | None = None,
    ) -> CodexOAuthService:
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=config_manager,  # type: ignore[arg-type]
            root_path=root_path,
            flask_app=Flask(__name__),
        )
        return CodexOAuthService(ctx)

    def test_start_login_builds_codex_pkce_authorization_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._build_service(Path(tmp_dir))

            result = service.start_login()

        parsed = urlparse(result["authorization_url"])
        query = parse_qs(parsed.query)
        self.assertEqual("https", parsed.scheme)
        self.assertEqual("auth.openai.com", parsed.netloc)
        self.assertEqual("/oauth/authorize", parsed.path)
        self.assertEqual([CODEX_CLIENT_ID], query["client_id"])
        self.assertEqual([CODEX_REDIRECT_URI], query["redirect_uri"])
        self.assertEqual(["code"], query["response_type"])
        self.assertEqual(["S256"], query["code_challenge_method"])
        self.assertEqual(["true"], query["codex_cli_simplified_flow"])
        self.assertTrue(result["state"])

    def test_complete_login_exchanges_code_and_writes_codex_auth_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._build_service(Path(tmp_dir))
            session = service.start_login()
            callback_url = f"{CODEX_REDIRECT_URI}?code=demo-code&state={session['state']}"
            captured: dict[str, Any] = {}

            def fake_post(url, data=None, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                captured["url"] = url
                captured["data"] = dict(data or {})
                captured["headers"] = dict(headers or {})
                captured["timeout"] = timeout
                captured["proxies"] = proxies
                captured["verify"] = verify
                return FakeResponse(
                    {
                        "access_token": "access-demo",
                        "refresh_token": "refresh-demo",
                        "id_token": build_id_token(),
                        "expires_in": 3600,
                    }
                )

            with patch("src.services.codex_oauth_service.requests.post", side_effect=fake_post):
                result = service.complete_login(callback_url)

            auth_file = Path(result["auth_file"]["path"])
            payload = json.loads(auth_file.read_text(encoding="utf-8"))

        self.assertEqual("authorization_code", captured["data"]["grant_type"])
        self.assertIsNone(captured["proxies"])
        self.assertFalse(captured["verify"])
        self.assertEqual("demo-code", captured["data"]["code"])
        self.assertEqual(CODEX_REDIRECT_URI, captured["data"]["redirect_uri"])
        self.assertTrue(captured["data"]["code_verifier"])
        self.assertEqual("codex", payload["type"])
        self.assertEqual("access-demo", payload["access_token"])
        self.assertEqual("refresh-demo", payload["refresh_token"])
        self.assertEqual("account-123", payload["account_id"])
        self.assertEqual("codex@example.com", payload["email"])
        self.assertEqual("plus", payload["plan_type"])
        self.assertEqual("codex-codex@example.com-plus.json", auth_file.name)

    def test_complete_login_overwrites_same_codex_auth_file_like_cpa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._build_service(Path(tmp_dir))
            session = service.start_login()
            callback_url = f"{CODEX_REDIRECT_URI}?code=demo-code&state={session['state']}"

            token_index = 0

            def fake_post(url, data=None, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                nonlocal token_index
                del url, data, headers, timeout, proxies, verify, kwargs
                token_index += 1
                return FakeResponse(
                    {
                        "access_token": f"access-{token_index}",
                        "refresh_token": f"refresh-{token_index}",
                        "id_token": build_id_token(email="codex+same@example.com"),
                        "expires_in": 3600,
                    }
                )

            with patch("src.services.codex_oauth_service.requests.post", side_effect=fake_post):
                first = service.complete_login(callback_url)
                service.record_auth_file_failure(
                    first["auth_file"]["name"],
                    "invalid or expired token",
                    status_code=401,
                    error_type="authentication_error",
                )
                second_session = service.start_login()
                second_callback_url = f"{CODEX_REDIRECT_URI}?code=demo-code-2&state={second_session['state']}"
                second = service.complete_login(second_callback_url)

            first_file = Path(first["auth_file"]["path"])
            second_file = Path(second["auth_file"]["path"])
            payload = json.loads(second_file.read_text(encoding="utf-8"))
            auth_entry = service.list_auth_files()["files"][0]

        self.assertEqual("codex-codex+same@example.com-plus.json", first_file.name)
        self.assertEqual(first_file, second_file)
        self.assertEqual("access-2", payload["access_token"])
        self.assertEqual("refresh-2", payload["refresh_token"])
        self.assertEqual("available", auth_entry["availability_status"])
        self.assertEqual("", auth_entry["usage_status_message"])

    def test_codex_team_auth_file_name_uses_eight_char_account_hash_like_cpa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._build_service(Path(tmp_dir))
            session = service.start_login()
            callback_url = f"{CODEX_REDIRECT_URI}?code=demo-code&state={session['state']}"
            account_id = "team-account-123"
            expected_hash = hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:8]

            def fake_post(url, data=None, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                del url, data, headers, timeout, proxies, verify, kwargs
                return FakeResponse(
                    {
                        "access_token": "access-demo",
                        "refresh_token": "refresh-demo",
                        "id_token": build_id_token(
                            email="codex.team@example.com",
                            account_id=account_id,
                            plan_type="Team",
                        ),
                        "expires_in": 3600,
                    }
                )

            with patch("src.services.codex_oauth_service.requests.post", side_effect=fake_post):
                result = service.complete_login(callback_url)

        self.assertEqual(
            f"codex-{expected_hash}-codex.team@example.com-team.json",
            result["auth_file"]["name"],
        )

    def test_list_auth_files_reports_expired_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            (auth_dir / "codex-demo.json").write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "access_token": "access-demo",
                        "expired": "2000-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)

            result = service.list_auth_files()

        self.assertEqual(1, result["total"])
        self.assertEqual("expired", result["files"][0]["status"])
        self.assertEqual("auth_check_required", result["files"][0]["availability_status"])
        self.assertEqual(
            "待验证：access_token 已过期且缺少 refresh_token，会先用当前 access_token 请求一次",
            result["files"][0]["availability_status_message"],
        )

    def test_list_auth_files_sorts_by_name_and_delete_removes_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            for name in ("codex-b.json", "codex-a.json"):
                (auth_dir / name).write_text(
                    json.dumps(
                        {
                            "type": "codex",
                            "email": f"{name}@example.com",
                            "access_token": "access-demo",
                            "expired": "2999-01-01T00:00:00Z",
                        }
                    ),
                    encoding="utf-8",
                )
            service = self._build_service(root)
            service.record_auth_file_failure("codex-a.json", "bad token", status_code=401)

            before_delete = service.list_auth_files()
            delete_result = service.delete_auth_file("codex-a.json")
            after_delete = service.list_auth_files()

        self.assertEqual(["codex-a.json", "codex-b.json"], [item["name"] for item in before_delete["files"]])
        self.assertEqual("codex-a.json", delete_result["deleted"])
        self.assertEqual(["codex-b.json"], [item["name"] for item in after_delete["files"]])
        self.assertFalse((root / "data" / "oauth" / "codex" / "codex-a.json").exists())

    def test_get_auth_file_quota_uses_access_token_and_account_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            (auth_dir / "codex-demo.json").write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "account_id": "account-123",
                        "access_token": "access-demo",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)
            captured: dict[str, Any] = {}

            def fake_get(url, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                captured["url"] = url
                captured["headers"] = dict(headers or {})
                captured["timeout"] = timeout
                captured["proxies"] = proxies
                captured["verify"] = verify
                return FakeResponse(
                    {
                        "plan_type": "plus",
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 25,
                                "reset_after_seconds": 3600,
                            }
                        },
                    }
                )

            with patch("src.services.codex_oauth_service.requests.get", side_effect=fake_get):
                result = service.get_auth_file_quota("codex-demo.json")
            auth_files = service.list_auth_files()["files"]

        self.assertEqual("Bearer access-demo", captured["headers"]["Authorization"])
        self.assertEqual("account-123", captured["headers"]["Chatgpt-Account-Id"])
        self.assertEqual(20, captured["timeout"])
        self.assertIsNone(captured["proxies"])
        self.assertFalse(captured["verify"])
        self.assertEqual("plus", result["plan_type"])
        self.assertEqual(75.0, result["windows"][0]["remaining_percent"])
        self.assertTrue(result["windows"][0]["reset_at"])
        self.assertEqual(75.0, auth_files[0]["quota"]["windows"][0]["remaining_percent"])
        self.assertEqual("", auth_files[0]["quota_error"])

    def test_get_auth_file_quota_persists_error_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            (auth_dir / "codex-demo.json").write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "access_token": "access-demo",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)

            def fake_get(url, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                del url, headers, timeout, proxies, verify, kwargs
                return FakeResponse(
                    {"error": {"message": "quota failed"}},
                    status_code=429,
                    text="quota failed",
                )

            with patch("src.services.codex_oauth_service.requests.get", side_effect=fake_get):
                with self.assertRaisesRegex(ValueError, "quota failed"):
                    service.get_auth_file_quota("codex-demo.json")
            auth_files = service.list_auth_files()["files"]

        self.assertEqual("codex-demo.json", auth_files[0]["name"])
        self.assertIn("quota failed", auth_files[0]["quota_error"])

    def test_get_auth_file_quota_syncs_memory_cooldown_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            (auth_dir / "codex-demo.json").write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "access_token": "access-demo",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)
            usage_values = [100, 25]

            def fake_get(url, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                del url, headers, timeout, proxies, verify, kwargs
                return FakeResponse(
                    {
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": usage_values.pop(0),
                                "reset_after_seconds": 3600,
                            }
                        }
                    }
                )

            with patch("src.services.codex_oauth_service.requests.get", side_effect=fake_get):
                exhausted_quota = service.get_auth_file_quota("codex-demo.json")
                self.assertIn("codex-demo.json", service._quota_cooldowns)
                available_quota = service.get_auth_file_quota("codex-demo.json")

        self.assertEqual(0.0, exhausted_quota["windows"][0]["remaining_percent"])
        self.assertEqual(75.0, available_quota["windows"][0]["remaining_percent"])
        self.assertNotIn("codex-demo.json", service._quota_cooldowns)

    def test_get_auth_file_quota_skips_duplicate_refresh_for_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            (auth_dir / "codex-demo.json").write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "access_token": "access-demo",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)
            request_started = threading.Event()
            release_request = threading.Event()
            errors: list[BaseException] = []
            first_result: dict[str, Any] = {}
            call_count = 0

            def fake_get(url, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                nonlocal call_count
                del url, headers, timeout, proxies, verify, kwargs
                call_count += 1
                request_started.set()
                release_request.wait(2)
                return FakeResponse(
                    {
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 25,
                                "reset_after_seconds": 3600,
                            }
                        }
                    }
                )

            def run_first_refresh() -> None:
                try:
                    first_result.update(service.get_auth_file_quota("codex-demo.json"))
                except BaseException as exc:
                    errors.append(exc)

            with patch("src.services.codex_oauth_service.requests.get", side_effect=fake_get):
                worker = threading.Thread(target=run_first_refresh)
                worker.start()
                try:
                    self.assertTrue(request_started.wait(1))
                    duplicate_result = service.get_auth_file_quota("codex-demo.json")
                finally:
                    release_request.set()
                    worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, call_count)
        self.assertTrue(duplicate_result["skipped"])
        self.assertEqual("quota_refresh_in_progress", duplicate_result["reason"])
        self.assertEqual(75.0, first_result["windows"][0]["remaining_percent"])

    def test_expired_auth_file_without_refresh_token_is_still_a_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            (auth_dir / "codex-demo.json").write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "access_token": "access-demo",
                        "expired": "2000-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)
            service.add_model("gpt-5.4")

            with patch.object(service, "get_auth_file_quota") as quota_mock:
                candidates = service.iter_auth_candidates_for_model("gpt-5.4")
                quota_mock.assert_not_called()
            auth_file = service.list_auth_files()["files"][0]

        self.assertEqual(["codex-demo.json"], [candidate.name for candidate in candidates])
        self.assertEqual("auth_check_required", auth_file["availability_status"])
        self.assertEqual(
            "待验证：access_token 已过期且缺少 refresh_token，会先用当前 access_token 请求一次",
            auth_file["availability_status_message"],
        )
        self.assertEqual("", auth_file["usage_error_type"])
        self.assertEqual("", auth_file["usage_status_message"])

    def test_auth_file_usage_status_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            (auth_dir / "codex-demo.json").write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "access_token": "access-demo",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)

            service.record_auth_file_failure(
                "codex-demo.json",
                "login expired",
                status_code=401,
                error_type="invalid_grant",
            )
            failed_entry = service.list_auth_files()["files"][0]
            service.record_auth_file_success("codex-demo.json")
            next_service = self._build_service(root)
            success_entry = next_service.list_auth_files()["files"][0]

        self.assertEqual("error", failed_entry["usage_status"])
        self.assertEqual("login expired", failed_entry["usage_status_message"])
        self.assertEqual(401, failed_entry["usage_status_code"])
        self.assertEqual("invalid_grant", failed_entry["usage_error_type"])
        self.assertEqual("auth_failed", failed_entry["availability_status"])
        self.assertEqual("success", success_entry["usage_status"])
        self.assertEqual("success", success_entry["usage_status_message"])
        self.assertEqual("available", success_entry["availability_status"])

    def test_expired_auth_file_refresh_failure_records_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            (auth_dir / "codex-demo.json").write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "access_token": "access-demo",
                        "refresh_token": "refresh-demo",
                        "plan_type": "pro",
                        "expired": "2000-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)
            service.add_model("gpt-5.4")

            def fake_post(url, data=None, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                del url, data, headers, timeout, proxies, verify, kwargs
                return FakeResponse(
                    {"error": "invalid_grant"},
                    status_code=400,
                    text="invalid_grant",
                )

            with patch("src.services.codex_oauth_service.requests.post", side_effect=fake_post):
                candidates = service.iter_auth_candidates_for_model("gpt-5.4")
            auth_file = service.list_auth_files()["files"][0]

        self.assertEqual([], candidates)
        self.assertEqual("error", auth_file["usage_status"])
        self.assertIn("invalid_grant", auth_file["usage_status_message"])
        self.assertEqual("token_refresh_failed", auth_file["usage_error_type"])
        self.assertEqual("auth_failed", auth_file["availability_status"])
        self.assertEqual(
            "认证失败：access_token 过期后使用 refresh_token 刷新失败，请重新登录",
            auth_file["availability_status_message"],
        )

    def test_quota_refresh_can_recover_persisted_auth_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            auth_file = auth_dir / "codex-demo.json"
            auth_file.write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "account_id": "account-123",
                        "access_token": "old-access",
                        "refresh_token": "refresh-demo",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(root)
            service.add_model("gpt-5.4")
            service.record_auth_file_failure(
                "codex-demo.json",
                "invalid or expired token",
                status_code=401,
                error_type="authentication_error",
            )
            before = service.list_auth_files()["files"][0]
            skipped_candidates = service.iter_auth_candidates_for_model("gpt-5.4")
            captured_authorizations: list[str] = []

            def fake_get(url, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                del url, timeout, proxies, verify, kwargs
                captured_authorizations.append(str((headers or {}).get("Authorization") or ""))
                if len(captured_authorizations) == 1:
                    return FakeResponse(
                        {"error": {"message": "invalid or expired token"}},
                        status_code=401,
                        text="invalid or expired token",
                    )
                return FakeResponse(
                    {
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 10,
                                "reset_after_seconds": 3600,
                            }
                        }
                    }
                )

            def fake_post(url, data=None, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                del url, data, headers, timeout, proxies, verify, kwargs
                return FakeResponse(
                    {
                        "access_token": "new-access",
                        "refresh_token": "refresh-next",
                        "id_token": build_id_token(),
                        "expires_in": 3600,
                    }
                )

            with patch("src.services.codex_oauth_service.requests.get", side_effect=fake_get):
                with patch("src.services.codex_oauth_service.requests.post", side_effect=fake_post):
                    quota = service.get_auth_file_quota("codex-demo.json")
            after = service.list_auth_files()["files"][0]
            next_payload = json.loads(auth_file.read_text(encoding="utf-8"))

        self.assertEqual("auth_failed", before["availability_status"])
        self.assertEqual([], skipped_candidates)
        self.assertEqual(["Bearer old-access", "Bearer new-access"], captured_authorizations)
        self.assertEqual(90.0, quota["windows"][0]["remaining_percent"])
        self.assertEqual("new-access", next_payload["access_token"])
        self.assertEqual("refresh-next", next_payload["refresh_token"])
        self.assertEqual("available", after["availability_status"])
        self.assertEqual("", after["usage_status_message"])
        self.assertEqual("", after["usage_error_type"])

    def test_quota_request_uses_oauth_network_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            (auth_dir / "codex-demo.json").write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "codex@example.com",
                        "access_token": "access-demo",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            service = self._build_service(
                root,
                FakeConfigManager(
                    oauth_proxy="http://127.0.0.1:7890",
                    oauth_verify_ssl=True,
                ),
            )
            captured: dict[str, Any] = {}

            def fake_get(url, headers=None, timeout=None, proxies=None, verify=None, **kwargs):
                captured["proxies"] = proxies
                captured["verify"] = verify
                del url, headers, timeout, kwargs
                return FakeResponse({})

            with patch("src.services.codex_oauth_service.requests.get", side_effect=fake_get):
                service.get_auth_file_quota("codex-demo.json")

        self.assertEqual(
            {
                "http": "http://127.0.0.1:7890",
                "https": "http://127.0.0.1:7890",
            },
            captured["proxies"],
        )
        self.assertTrue(captured["verify"])

    def test_list_models_uses_default_manual_codex_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self._build_service(Path(tmp_dir))

            result = service.list_models()

        model_ids = [model["id"] for model in result["models"]]
        self.assertEqual([], model_ids)
        self.assertNotIn("source", result)
        self.assertNotIn("updated_at", result)
        self.assertNotIn("tiers", result)
        self.assertEqual(list(CODEX_MODEL_REFERENCE_URLS), result["reference_urls"])
        self.assertEqual((), service.list_model_names())

    def test_add_and_delete_models_persists_manual_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            service = self._build_service(root)

            added = service.add_model("gpt-custom")
            models_file = root / "data" / "oauth" / "codex" / "models.json"
            added_payload = json.loads(models_file.read_text(encoding="utf-8"))
            deleted = service.delete_model("gpt-custom")
            deleted_payload = json.loads(models_file.read_text(encoding="utf-8"))

        self.assertIn("gpt-custom", [model["id"] for model in added["models"]])
        self.assertEqual([], [model["id"] for model in deleted["models"]])
        self.assertEqual(["gpt-custom"], added_payload)
        self.assertEqual([], deleted_payload)

    def test_iter_auth_candidates_does_not_precheck_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            first = auth_dir / "codex-first.json"
            second = auth_dir / "codex-second.json"
            first.write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "first@example.com",
                        "access_token": "access-first",
                        "plan_type": "pro",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            second.write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "second@example.com",
                        "access_token": "access-second",
                        "plan_type": "pro",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            os.utime(second, (1000, 1000))
            os.utime(first, (2000, 2000))
            service = self._build_service(root)
            service.add_model("gpt-5.4")

            with patch.object(service, "get_auth_file_quota") as quota_mock:
                candidates = service.iter_auth_candidates_for_model("gpt-5.4")
                quota_mock.assert_not_called()

        self.assertEqual(
            ["codex-first.json", "codex-second.json"],
            [candidate.name for candidate in candidates],
        )

    def test_iter_auth_candidates_skips_quota_cooling_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            auth_dir = root / "data" / "oauth" / "codex"
            auth_dir.mkdir(parents=True)
            first = auth_dir / "codex-first.json"
            second = auth_dir / "codex-second.json"
            first.write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "first@example.com",
                        "access_token": "access-first",
                        "plan_type": "pro",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            second.write_text(
                json.dumps(
                    {
                        "type": "codex",
                        "email": "second@example.com",
                        "access_token": "access-second",
                        "plan_type": "pro",
                        "expired": "2999-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            os.utime(second, (1000, 1000))
            os.utime(first, (2000, 2000))
            service = self._build_service(root)
            service.add_model("gpt-5.4")
            service.mark_auth_file_quota_exhausted("codex-first.json", retry_after_seconds=60)

            with patch.object(service, "get_auth_file_quota") as quota_mock:
                candidates = service.iter_auth_candidates_for_model("gpt-5.4")
                quota_mock.assert_not_called()

        self.assertEqual(["codex-second.json"], [candidate.name for candidate in candidates])


if __name__ == "__main__":
    unittest.main()
