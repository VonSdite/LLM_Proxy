from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.presentation.user_controller import UserController
from src.repositories import LogRepository, UserRepository
from src.services import AuthenticationService, LogService, UserService
from src.utils.database import create_connection_factory


class DummyLogger:
    def info(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def error(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def warning(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def debug(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


class FakeAuthService:
    def is_auth_enabled(self) -> bool:
        return False

    def validate_session(self, session_token: str | None) -> bool:
        del session_token
        return True


class FakeConfigManager:
    def get_raw_config(self) -> dict[str, Any]:
        return {
            "providers": [
                {
                    "name": "demo",
                    "api": "https://example.com/v1/chat/completions",
                    "model_list": ["m1", "m2"],
                }
            ]
        }


class UserBackendSortApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tempdir.name) / "requests.db"
        self.app = Flask(__name__)
        self.ctx = AppContext(
            logger=DummyLogger(),
            config_manager=cast(Any, FakeConfigManager()),
            root_path=Path(self._tempdir.name),
            flask_app=self.app,
        )
        self.connection_factory = create_connection_factory(db_path)
        self.log_repository = LogRepository(self.connection_factory)
        self.user_repository = UserRepository(self.connection_factory)
        self.log_service = LogService(self.ctx, self.log_repository)
        self.user_service = UserService(self.ctx, self.user_repository)
        UserController(
            self.ctx,
            self.user_service,
            cast(AuthenticationService, FakeAuthService()),
        )
        self.client = self.app.test_client()

        self.user_ids = {
            "alice": self.user_repository.create("alice", "10.0.0.1"),
            "bob": self.user_repository.create("bob", "10.0.0.2"),
            "carol": self.user_repository.create("carol", "10.0.0.3"),
        }
        self._log_request("10.0.0.1", 30, datetime(2026, 4, 8, 9, 0, 0))
        self._log_request("10.0.0.2", 10, datetime(2026, 4, 8, 10, 0, 0))
        self._log_request("10.0.0.3", 20, datetime(2026, 4, 8, 11, 0, 0))

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _log_request(
        self,
        ip_address: str,
        total_tokens: int,
        start_time: datetime,
    ) -> None:
        self.log_service.log_request(
            request_model="demo/m1",
            response_model="demo/m1",
            total_tokens=total_tokens,
            prompt_tokens=total_tokens // 2,
            completion_tokens=total_tokens // 2,
            start_time=start_time,
            end_time=start_time,
            ip_address=ip_address,
        )

    def test_users_api_sorts_before_pagination(self) -> None:
        response = self.client.get(
            "/api/users",
            query_string={
                "page": "1",
                "page_size": "1",
                "sort_key": "total_tokens",
                "sort_direction": "asc",
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(3, payload["total"])
        self.assertEqual("bob", payload["users"][0]["username"])
        self.assertEqual(10, payload["users"][0]["total_tokens"])

    def test_users_api_sorts_derived_model_permissions_before_pagination(self) -> None:
        alice_id = self.user_ids["alice"]
        self.assertIsNotNone(alice_id)
        assert alice_id is not None
        self.user_service.update_user(
            alice_id,
            model_permissions_provided=True,
            model_permissions=["demo/m1"],
        )

        response = self.client.get(
            "/api/users",
            query_string={
                "page": "1",
                "page_size": "1",
                "sort_key": "allowed_models_count",
                "sort_direction": "asc",
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("alice", payload["users"][0]["username"])
        self.assertEqual(1, payload["users"][0]["allowed_models_count"])


if __name__ == "__main__":
    unittest.main()
