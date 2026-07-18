from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.presentation.oauth_controller import OAuthController


class FakeLogger:
    def info(self, msg: str, *args: Any) -> None:
        del msg, args

    def warning(self, msg: str, *args: Any) -> None:
        del msg, args

    def error(self, msg: str, *args: Any) -> None:
        del msg, args

    def debug(self, msg: str, *args: Any) -> None:
        del msg, args


class FakeAuthService:
    def is_auth_enabled(self) -> bool:
        return False

    def validate_session(self, session_token: str | None) -> bool:
        del session_token
        return True


class FakeOAuthService:
    def __init__(self) -> None:
        self.enabled_calls: list[tuple[str, bool]] = []
        self.error: Exception | None = None

    def set_auth_file_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        self.enabled_calls.append((name, enabled))
        if self.error is not None:
            raise self.error
        return {
            "status": "ok",
            "name": name,
            "enabled": enabled,
            "disabled": not enabled,
            "auth_file": {
                "name": name,
                "enabled": enabled,
                "disabled": not enabled,
            },
        }


class OAuthControllerAuthFileEnabledRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        self.codex_service = FakeOAuthService()
        self.claude_service = FakeOAuthService()
        self.controller = OAuthController(
            ctx,
            self.codex_service,  # type: ignore[arg-type]
            self.claude_service,  # type: ignore[arg-type]
            FakeAuthService(),  # type: ignore[arg-type]
        )
        self.client = app.test_client()

    def test_codex_enable_and_disable_routes_update_auth_file_state(self) -> None:
        disable_response = self.client.post("/api/oauth/codex/auth-files/codex-demo.json/disable")
        enable_response = self.client.post("/api/oauth/codex/auth-files/codex-demo.json/enable")

        self.assertEqual(200, disable_response.status_code)
        self.assertFalse(disable_response.get_json()["enabled"])
        self.assertEqual(200, enable_response.status_code)
        self.assertTrue(enable_response.get_json()["enabled"])
        self.assertEqual(
            [("codex-demo.json", False), ("codex-demo.json", True)],
            self.codex_service.enabled_calls,
        )

    def test_claude_enable_and_disable_routes_update_auth_file_state(self) -> None:
        disable_response = self.client.post("/api/oauth/claude/auth-files/claude-demo.json/disable")
        enable_response = self.client.post("/api/oauth/claude/auth-files/claude-demo.json/enable")

        self.assertEqual(200, disable_response.status_code)
        self.assertTrue(disable_response.get_json()["disabled"])
        self.assertEqual(200, enable_response.status_code)
        self.assertFalse(enable_response.get_json()["disabled"])
        self.assertEqual(
            [("claude-demo.json", False), ("claude-demo.json", True)],
            self.claude_service.enabled_calls,
        )

    def test_enabled_route_returns_bad_request_for_unknown_auth_file(self) -> None:
        self.codex_service.error = ValueError("Auth file not found")

        response = self.client.post("/api/oauth/codex/auth-files/missing.json/disable")

        self.assertEqual(400, response.status_code)
        self.assertEqual({"error": "Auth file not found"}, response.get_json())


if __name__ == "__main__":
    unittest.main()
