from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext, Logger
from src.presentation.web_controller import WebController
from src.repositories.log_repository import LogRepository
from src.repositories.user_repository import UserRepository
from src.services import AuthenticationService, LogService, SettingsService
from src.utils.database import create_connection_factory


class FakeLogger:
    def info(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None:
        del msg, args, exc_info, stack_info, stacklevel, extra

    def warning(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None:
        del msg, args, exc_info, stack_info, stacklevel, extra

    def error(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None:
        del msg, args, exc_info, stack_info, stacklevel, extra

    def debug(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None:
        del msg, args, exc_info, stack_info, stacklevel, extra


class FakeAuthService:
    def is_auth_enabled(self) -> bool:
        return False

    def validate_session(self, session_token: str | None) -> bool:
        del session_token
        return True

    def get_session_username(self, session_token: str | None) -> str:
        del session_token
        return ""


class FakeSettingsService:
    def get_system_settings(self) -> dict:
        return {}

    def update_system_settings(self, payload: dict) -> dict:
        del payload
        raise RuntimeError("Settings service is not configured for this test")

    def update_basic_settings(self, payload: dict) -> dict:
        del payload
        raise RuntimeError("Settings service is not configured for this test")

    def update_debug_settings(self, payload: dict) -> dict:
        del payload
        raise RuntimeError("Settings service is not configured for this test")


class DashboardFilterApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root_path = Path(__file__).resolve().parents[1]
        self.db_path = self.root_path / f"dashboard-filters-{uuid4().hex}.db"
        self.app = Flask(__name__)
        self.ctx = AppContext(
            logger=cast(Logger, FakeLogger()),
            config_manager=None,  # type: ignore[arg-type]
            root_path=self.root_path,
            flask_app=self.app,
        )
        self.connection_factory = create_connection_factory(self.db_path)
        self.log_repository = LogRepository(self.connection_factory)
        self.user_repository = UserRepository(self.connection_factory)
        self.log_service = LogService(self.ctx, self.log_repository)
        WebController(
            self.ctx,
            self.log_service,
            cast(SettingsService, FakeSettingsService()),
            cast(AuthenticationService, FakeAuthService()),
        )
        self.client = self.app.test_client()

        self.user_repository.create("alice", "10.0.0.1")
        self.user_repository.create("bob", "10.0.0.2")
        self.user_repository.create("carol", "10.0.0.3")

        self._log_request(
            "model-a", "resp-a", 10, "10.0.0.1", datetime(2026, 4, 8, 9, 0, 0)
        )
        self._log_request(
            "model-c", "resp-c", 20, "10.0.0.1", datetime(2026, 4, 8, 10, 0, 0)
        )
        self._log_request(
            "model-b", "resp-b", 30, "10.0.0.2", datetime(2026, 4, 8, 11, 0, 0)
        )
        self._log_request(
            "model-a", "resp-a", 40, "10.0.0.3", datetime(2026, 4, 8, 12, 0, 0)
        )

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def _log_request(
        self,
        request_model: str,
        response_model: str,
        total_tokens: int,
        ip_address: str,
        start_time: datetime,
    ) -> None:
        self.log_service.log_request(
            request_model=request_model,
            response_model=response_model,
            total_tokens=total_tokens,
            prompt_tokens=total_tokens // 2,
            completion_tokens=total_tokens // 2,
            start_time=start_time,
            end_time=start_time,
            ip_address=ip_address,
        )

    def test_statistics_api_supports_multi_value_filters(self) -> None:
        response = self.client.get(
            "/api/statistics",
            query_string=[
                ("username", "alice"),
                ("username", "bob"),
                ("request_model", "model-a"),
                ("request_model", "model-b"),
            ],
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(
            {("alice", "model-a"), ("bob", "model-b")},
            {(item["username"], item["request_model"]) for item in payload},
        )

    def test_request_logs_api_supports_multi_value_filters(self) -> None:
        response = self.client.get(
            "/api/request-logs",
            query_string=[
                ("page", "1"),
                ("page_size", "50"),
                ("username", "alice"),
                ("username", "bob"),
                ("request_model", "model-a"),
                ("request_model", "model-b"),
            ],
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(2, payload["total"])
        self.assertEqual(
            {("alice", "model-a"), ("bob", "model-b")},
            {(item["username"], item["request_model"]) for item in payload["logs"]},
        )


if __name__ == "__main__":
    unittest.main()
