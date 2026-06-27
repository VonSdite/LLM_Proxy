from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from flask import Flask, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.presentation.proxy_controller import ProxyController
from src.repositories import ApiKeyRepository, LogRepository
from src.services import ApiKeyService, LogService, ModelCatalogService
from src.services.proxy_service import ProxyErrorInfo
from src.utils.database import create_connection_factory


class FakeLogger:
    def info(self, msg: str, *args: object) -> None:
        del msg, args

    def warning(self, msg: str, *args: object) -> None:
        del msg, args

    def error(self, msg: str, *args: object) -> None:
        del msg, args

    def debug(self, msg: str, *args: object) -> None:
        del msg, args


class FakeConfigManager:
    def __init__(self, *, api_key_enabled: bool = True, whitelist_enabled: bool = False) -> None:
        self._api_key_enabled = api_key_enabled
        self._whitelist_enabled = whitelist_enabled

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

    def is_api_key_management_enabled(self) -> bool:
        return self._api_key_enabled

    def is_chat_whitelist_enabled(self) -> bool:
        return self._whitelist_enabled

    def is_real_client_ip_enabled(self) -> bool:
        return False

    def get_real_client_ip_header(self) -> str:
        return "X-Forwarded-For"


class FakeOAuthModelService:
    def __init__(self, model_names: tuple[str, ...]) -> None:
        self._model_names = model_names

    def list_model_names(self) -> tuple[str, ...]:
        return self._model_names


class FakeUserService:
    def get_user_by_ip(
        self,
        ip_address: str,
        require_whitelist_access: bool = True,
    ) -> dict[str, Any] | None:
        del ip_address, require_whitelist_access
        return {"username": "tester", "model_permissions": "*", "model_permissions_mode": "all"}

    def can_user_access_model(
        self,
        user: dict[str, Any] | None,
        model_name: str,
        available_models=None,
    ) -> bool:
        del user, available_models
        return model_name == "demo/m1"

    def get_accessible_models_for_user(
        self,
        user: dict[str, Any] | None,
        available_models=None,
    ) -> list[str]:
        del user
        return [model_name for model_name in list(available_models or []) if model_name == "demo/m1"]


class FakeProviderManager:
    def list_model_names(self) -> tuple[str, ...]:
        return ("demo/m1", "demo/m2")

    def get_provider_for_model(self, model_name: str):
        if model_name in {"demo/m1", "demo/m2"}:
            return type(
                "Provider",
                (),
                {
                    "name": "demo",
                    "target_formats": ("openai_chat",),
                },
            )()
        return None

    def get_provider_view(self, provider_name: str):
        if provider_name != "demo":
            return None
        return type(
            "ProviderView",
            (),
            {
                "name": "demo",
                "source_format": "openai_chat",
                "target_formats": ("openai_chat",),
            },
        )()


class CompletingProxyService:
    def __init__(self) -> None:
        self.forwarded_headers: dict[str, str] = {}

    def proxy_request(self, *args: Any, **kwargs: Any) -> tuple[Any, int, ProxyErrorInfo | None]:
        self.forwarded_headers = dict(args[2])
        on_complete = kwargs.get("on_complete")
        if on_complete:
            on_complete(
                {
                    "response_model": "m1",
                    "total_tokens": 9,
                    "prompt_tokens": 4,
                    "completion_tokens": 5,
                }
            )
        return jsonify({"id": "ok", "object": "chat.completion"}), 200, None


class ApiKeyManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root_path = Path(self._tempdir.name)
        self.app = Flask(__name__)
        self.ctx = AppContext(
            logger=FakeLogger(),
            config_manager=cast(Any, FakeConfigManager()),
            root_path=self.root_path,
            flask_app=self.app,
        )
        self.connection_factory = create_connection_factory(self.root_path / "requests.db")
        self.api_key_repository = ApiKeyRepository(self.connection_factory)
        self.log_repository = LogRepository(self.connection_factory)
        self.api_key_service = ApiKeyService(self.ctx, self.api_key_repository)
        self.log_service = LogService(self.ctx, self.log_repository)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _register_proxy(self, *, whitelist_enabled: bool = False) -> CompletingProxyService:
        proxy_ctx = AppContext(
            logger=self.ctx.logger,
            config_manager=cast(Any, FakeConfigManager(api_key_enabled=True, whitelist_enabled=whitelist_enabled)),
            root_path=self.root_path,
            flask_app=self.app,
        )
        proxy_service = CompletingProxyService()
        ProxyController(
            proxy_ctx,
            proxy_service,
            cast(Any, FakeUserService()),
            self.log_service,
            cast(Any, FakeProviderManager()),
            api_key_service=self.api_key_service,
        )
        return proxy_service

    def test_create_api_key_persists_plaintext_and_authenticates_hash(self) -> None:
        created = self.api_key_service.create_api_key(
            name="client-a",
            model_permissions=["demo/m1"],
            token_limit_k=2,
        )

        self.assertTrue(created["api_key"].startswith("sk-"))
        self.assertEqual("client-a", created["name"])
        self.assertEqual(["demo/m1"], created["model_permissions"])
        self.assertEqual(2, created["token_limit_k"])
        self.assertEqual(2000, created["token_limit_tokens"])
        self.assertEqual(2000, created["token_limit_remaining"])

        stored = self.api_key_service.get_api_key_by_id(int(created["id"]))
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(created["api_key"], stored["api_key"])

        listed = self.api_key_service.get_api_keys()
        self.assertEqual(created["api_key"], listed[0]["api_key"])

        authenticated = self.api_key_service.authenticate_api_key(created["api_key"])
        self.assertIsNotNone(authenticated)
        assert authenticated is not None
        self.assertEqual(created["id"], authenticated["id"])
        self.assertNotIn("api_key", authenticated)

    def test_update_api_key_can_change_permissions_limit_and_status(self) -> None:
        created = self.api_key_service.create_api_key(
            name="client-a",
            model_permissions=["demo/m1"],
            token_limit_k=2,
        )

        updated = self.api_key_service.update_api_key(
            int(created["id"]),
            name="client-b",
            enabled=False,
            model_permissions_provided=True,
            model_permissions=["demo/m2"],
            token_limit_k_provided=True,
            token_limit_k=3,
        )

        self.assertTrue(updated)
        stored = self.api_key_service.get_api_key_by_id(int(created["id"]))
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual("client-b", stored["name"])
        self.assertFalse(stored["enabled"])
        self.assertEqual(["demo/m2"], stored["model_permissions"])
        self.assertEqual(3, stored["token_limit_k"])
        self.assertEqual(3000, stored["token_limit_tokens"])
        self.assertEqual(3000, stored["token_limit_remaining"])

    def test_api_key_permissions_support_oauth_models(self) -> None:
        model_catalog = ModelCatalogService(
            self.ctx,
            codex_oauth_service=FakeOAuthModelService(("gpt-5-codex",)),
            claude_oauth_service=FakeOAuthModelService(("claude-sonnet-4-5",)),
        )
        service = ApiKeyService(self.ctx, self.api_key_repository, model_catalog)

        created = service.create_api_key(
            name="client-a",
            model_permissions=["gpt-5-codex", "claude-sonnet-4-5"],
        )

        self.assertIn("gpt-5-codex", service.get_available_models())
        self.assertIn("claude-sonnet-4-5", service.get_available_models())
        self.assertEqual(["gpt-5-codex", "claude-sonnet-4-5"], created["model_permissions"])
        self.assertTrue(service.can_api_key_access_model(created, "gpt-5-codex"))
        self.assertTrue(service.can_api_key_access_model(created, "claude-sonnet-4-5"))

    def test_api_key_token_limit_must_be_at_least_one_k(self) -> None:
        with self.assertRaises(ValueError):
            self.api_key_service.create_api_key(
                name="client-a",
                model_permissions=["demo/m1"],
                token_limit_k=0,
            )

        created = self.api_key_service.create_api_key(
            name="client-a",
            model_permissions=["demo/m1"],
        )
        with self.assertRaises(ValueError):
            self.api_key_service.update_api_key(
                int(created["id"]),
                token_limit_k_provided=True,
                token_limit_k=0,
            )

    def test_list_models_requires_valid_api_key_and_filters_models(self) -> None:
        created = self.api_key_service.create_api_key(
            name="client-a",
            model_permissions=["demo/m1"],
        )
        self._register_proxy()
        client = self.app.test_client()

        missing_response = client.get("/v1/models")
        self.assertEqual(401, missing_response.status_code)
        self.assertEqual("missing_api_key", missing_response.get_json()["error"]["code"])

        invalid_response = client.get("/v1/models", headers={"Authorization": "Bearer sk-invalid"})
        self.assertEqual(401, invalid_response.status_code)
        self.assertEqual("invalid_api_key", invalid_response.get_json()["error"]["code"])

        response = client.get("/v1/models", headers={"Authorization": f"Bearer {created['api_key']}"})
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(["demo/m1"], [item["id"] for item in payload["data"]])

    def test_api_key_and_user_model_permissions_intersect_for_models(self) -> None:
        created = self.api_key_service.create_api_key(
            name="client-a",
            model_permissions=["demo/m1", "demo/m2"],
        )
        self._register_proxy(whitelist_enabled=True)
        client = self.app.test_client()

        response = client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {created['api_key']}"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(["demo/m1"], [item["id"] for item in payload["data"]])

    def test_chat_completion_records_usage_to_api_key_and_drops_downstream_key_header(self) -> None:
        created = self.api_key_service.create_api_key(
            name="client-a",
            model_permissions=["demo/m1"],
        )
        proxy_service = self._register_proxy()
        client = self.app.test_client()

        response = client.post(
            "/v1/chat/completions",
            json={"model": "demo/m1", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": f"Bearer {created['api_key']}",
                "X-API-Key": created["api_key"],
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertNotIn("Authorization", proxy_service.forwarded_headers)
        self.assertNotIn("X-API-Key", proxy_service.forwarded_headers)

        stored = self.api_key_repository.get_by_id(int(created["id"]))
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(1, stored["total_request_count"])
        self.assertEqual(9, stored["total_tokens"])
        self.assertEqual(4, stored["prompt_tokens"])
        self.assertEqual(5, stored["completion_tokens"])

    def test_chat_completion_rejects_api_key_after_total_token_limit_is_reached(self) -> None:
        created = self.api_key_service.create_api_key(
            name="client-a",
            model_permissions=["demo/m1"],
            token_limit_k=1,
        )
        self.api_key_repository.record_usage(
            int(created["id"]),
            total_tokens=1000,
            prompt_tokens=500,
            completion_tokens=500,
        )
        self._register_proxy()
        client = self.app.test_client()

        response = client.post(
            "/v1/chat/completions",
            json={"model": "demo/m1", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {created['api_key']}"},
        )

        self.assertEqual(429, response.status_code)
        self.assertEqual("api_key_token_limit_exceeded", response.get_json()["error"]["code"])


if __name__ == "__main__":
    unittest.main()
