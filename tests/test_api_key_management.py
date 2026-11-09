from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import gevent
from flask import Flask, Response, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.presentation.api_key_controller import ApiKeyController
from src.presentation.proxy_controller import ProxyController
from src.repositories import ApiKeyRepository, LogRepository
from src.services import ApiKeyService, LogService, ModelCatalogService
from src.services.api_key_service import ApiKeyRequestError
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
                    "target_formats": ("openai_chat", "openai_responses", "claude"),
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

    def test_chat_completion_records_usage_to_api_key_and_drops_downstream_authorization_header(self) -> None:
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
        forwarded_api_key = next(
            value for key, value in proxy_service.forwarded_headers.items() if key.lower() == "x-api-key"
        )
        self.assertEqual(created["api_key"], forwarded_api_key)

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

    def test_request_limits_round_trip_and_partial_update_through_management_api(self) -> None:
        ApiKeyController(self.ctx, self.api_key_service, cast(Any, SimpleNamespace(is_auth_enabled=lambda: False)))
        client = self.app.test_client()
        response = client.post(
            "/api/api-keys",
            json={"name": "limited", "max_concurrency": 2, "request_quota_per_minute": "3", "request_quota_per_day": 8},
        )
        self.assertEqual(201, response.status_code)
        created = response.get_json()["api_key"]
        url = f"/api/api-keys/{created['id']}"
        self.assertEqual(3, created["request_quota_per_minute"])
        self.assertEqual(200, client.put(url, json={"enabled": False}).status_code)
        self.assertEqual(200, client.put(url, json={"max_concurrency": None}).status_code)
        stored = client.get(url).get_json()
        self.assertIsNone(stored["max_concurrency"])
        self.assertEqual(3, stored["request_quota_per_minute"])
        self.assertEqual(8, stored["request_quota_per_day"])
        for field in ApiKeyService.REQUEST_LIMIT_FIELDS:
            for invalid in (0, -1, True, 1.5, "1.5", "abc", 2147483648):
                with self.subTest(field=field, invalid=invalid):
                    self.assertEqual(400, client.post("/api/api-keys", json={field: invalid}).status_code)
                    self.assertEqual(400, client.put(url, json={field: invalid}).status_code)
        self.assertEqual(1, self.api_key_service.get_total_api_keys_count())
        self.assertEqual(
            200, client.put(url, json={field: "" for field in ApiKeyService.REQUEST_LIMIT_FIELDS}).status_code
        )
        self.assertTrue(all(client.get(url).get_json()[field] is None for field in ApiKeyService.REQUEST_LIMIT_FIELDS))

    def test_existing_database_migration_preserves_key_and_defaults_to_unlimited(self) -> None:
        factory = create_connection_factory(self.root_path / "legacy.db")
        with factory() as conn:
            conn.execute(
                "CREATE TABLE api_keys (id INTEGER PRIMARY KEY, name TEXT, key_hash TEXT, "
                "key_prefix TEXT, key_suffix TEXT, enabled INTEGER, created_at TEXT, updated_at TEXT)"
            )
            conn.execute(
                "INSERT INTO api_keys VALUES (1, 'legacy', ?, 'sk-old', 'key', 1, '2026-01-01', '2026-01-01')",
                (ApiKeyService.hash_api_key("sk-old-key"),),
            )
        repository = ApiKeyRepository(factory)
        service = ApiKeyService(self.ctx, repository)
        key = service.authenticate_api_key("sk-old-key")
        self.assertIsNotNone(key)
        assert key is not None
        self.assertTrue(all(key[field] is None for field in ApiKeyService.REQUEST_LIMIT_FIELDS))
        service.acquire_request(key)()
        ApiKeyRepository(factory)
        self.assertEqual(1, len(repository.get()))

    def test_request_limits_are_atomic_and_rejections_do_not_consume_quota(self) -> None:
        key = self.api_key_service.create_api_key(request_limits={"max_concurrency": 3, "request_quota_per_minute": 4})

        def acquire():
            try:
                return self.api_key_service.acquire_request(key)
            except ApiKeyRequestError as exc:
                return exc.code

        with patch("src.services.api_key_service.now_local_datetime", return_value=datetime(2026, 11, 7, 12)):
            tasks = [gevent.spawn(acquire) for _ in range(12)]
            gevent.joinall(tasks, raise_error=True)
            admitted = [task.value for task in tasks if callable(task.value)]
            self.assertEqual(3, len(admitted))
            self.assertEqual(9, sum(task.value == "api_key_concurrency_limit_exceeded" for task in tasks))
            for release in admitted:
                release()
                release()
            self.api_key_service.acquire_request(key)()
            with self.assertRaises(ApiKeyRequestError) as error:
                self.api_key_service.acquire_request(key)
            self.assertEqual("api_key_minute_limit_exceeded", error.exception.code)
            other = self.api_key_service.create_api_key(request_limits={"max_concurrency": 1})
            self.api_key_service.acquire_request(other)()

    def test_request_periods_survive_restart_and_reset_at_local_boundaries(self) -> None:
        key = self.api_key_service.create_api_key(
            request_limits={"request_quota_per_minute": 1, "request_quota_per_day": 2}
        )
        with patch("src.services.api_key_service.now_local_datetime") as clock:
            clock.return_value = datetime(2026, 11, 7, 23, 58, 30)
            self.api_key_service.acquire_request(key)()
            restarted = ApiKeyService(self.ctx, ApiKeyRepository(self.connection_factory))
            with self.assertRaises(ApiKeyRequestError) as error:
                restarted.acquire_request(key)
            self.assertEqual("api_key_minute_limit_exceeded", error.exception.code)
            self.assertEqual(30, error.exception.retry_after)
            clock.return_value = datetime(2026, 11, 7, 23, 59)
            restarted.acquire_request(key)()
            restarted.update_api_key(key["id"], request_limits={"request_quota_per_minute": None})
            with self.assertRaises(ApiKeyRequestError) as error:
                restarted.acquire_request(key)
            self.assertEqual("api_key_day_limit_exceeded", error.exception.code)
            self.assertEqual(60, error.exception.retry_after)
            clock.return_value = datetime(2026, 11, 8)
            restarted.acquire_request(key)()

    def test_current_limits_and_disabled_state_override_authenticated_snapshot(self) -> None:
        key = self.api_key_service.create_api_key()
        first = self.api_key_service.acquire_request(key)
        self.api_key_service.update_api_key(key["id"], request_limits={"max_concurrency": 1})
        with self.assertRaises(ApiKeyRequestError) as error:
            self.api_key_service.acquire_request(key)
        self.assertEqual("api_key_concurrency_limit_exceeded", error.exception.code)
        first()
        self.api_key_service.acquire_request(key)()
        self.api_key_service.update_api_key(key["id"], enabled=False)
        with self.assertRaises(ApiKeyRequestError) as error:
            self.api_key_service.acquire_request(key)
        self.assertEqual(401, error.exception.status_code)

    def test_proxy_rejects_over_quota_across_formats_without_counting_invalid_requests(self) -> None:
        key = self.api_key_service.create_api_key(
            model_permissions=["demo/m1"], request_limits={"request_quota_per_day": 1}
        )
        proxy = self._register_proxy()
        client = self.app.test_client()
        headers = {"Authorization": f"Bearer {key['api_key']}"}
        with patch.object(proxy, "proxy_request", wraps=proxy.proxy_request) as forward:
            self.assertEqual(200, client.get("/v1/models", headers=headers).status_code)
            self.assertEqual(400, client.post("/v1/chat/completions", json={}, headers=headers).status_code)
            self.assertEqual(
                403, client.post("/v1/chat/completions", json={"model": "demo/m2"}, headers=headers).status_code
            )
            self.assertEqual(
                200, client.post("/v1/chat/completions", json={"model": "demo/m1"}, headers=headers).status_code
            )
            for route in ("/v1/chat/completions", "/v1/responses", "/v1/messages"):
                with self.subTest(route=route):
                    response = client.post(route, json={"model": "demo/m1"}, headers=headers)
                    self.assertEqual(429, response.status_code)
                    self.assertGreater(int(response.headers["Retry-After"]), 0)
                    self.assertEqual("rate_limit_error", response.get_json()["error"]["type"])
                    if route == "/v1/messages":
                        self.assertEqual("error", response.get_json()["type"])
                    else:
                        self.assertEqual("api_key_day_limit_exceeded", response.get_json()["error"]["code"])
            self.assertEqual(1, forward.call_count)

    def test_proxy_failure_and_exception_release_concurrency_but_consume_request_quota(self) -> None:
        key = self.api_key_service.create_api_key(request_limits={"max_concurrency": 1, "request_quota_per_day": 2})
        proxy = self._register_proxy()
        client = self.app.test_client()
        headers = {"Authorization": f"Bearer {key['api_key']}"}
        with patch.object(proxy, "proxy_request", return_value=(None, 502, None)):
            self.assertEqual(
                502, client.post("/v1/chat/completions", json={"model": "demo/m1"}, headers=headers).status_code
            )
        with patch.object(proxy, "proxy_request", side_effect=RuntimeError("upstream failed")):
            self.assertEqual(
                500, client.post("/v1/chat/completions", json={"model": "demo/m1"}, headers=headers).status_code
            )
        response = client.post("/v1/chat/completions", json={"model": "demo/m1"}, headers=headers)
        self.assertEqual("api_key_day_limit_exceeded", response.get_json()["error"]["code"])

    def test_compact_and_image_routes_share_the_same_key_limits(self) -> None:
        key = self.api_key_service.create_api_key(request_limits={"max_concurrency": 1, "request_quota_per_day": 3})
        calls = []

        def forward(*args, **kwargs):
            calls.append(kwargs["route_name"])
            return jsonify({"id": "ok"}), 200, None

        codex = SimpleNamespace(
            list_model_names=lambda: ("codex-model",),
            list_image_model_names=lambda: ("image-model",),
            has_model=lambda model: model == "codex-model",
            has_image_model=lambda model: model == "image-model",
            proxy_request=forward,
            proxy_image_request=forward,
        )
        ProxyController(
            self.ctx,
            CompletingProxyService(),
            cast(Any, FakeUserService()),
            self.log_service,
            cast(Any, FakeProviderManager()),
            api_key_service=self.api_key_service,
            codex_proxy_service=cast(Any, codex),
        )
        client = self.app.test_client()
        headers = {"Authorization": f"Bearer {key['api_key']}"}
        routes = (
            ("/v1/responses/compact", "codex-model"),
            ("/v1/images/generations", "image-model"),
            ("/v1/images/edits", "image-model"),
        )
        for route, model in routes:
            with self.subTest(route=route):
                self.assertEqual(200, client.post(route, json={"model": model}, headers=headers).status_code)
        for route, model in routes:
            with self.subTest(route=route):
                response = client.post(route, json={"model": model}, headers=headers)
                self.assertEqual(429, response.status_code)
                self.assertEqual("api_key_day_limit_exceeded", response.get_json()["error"]["code"])
        self.assertEqual(3, len(calls))

    def test_stream_slot_is_held_until_exhaustion_failure_or_disconnect(self) -> None:
        key = self.api_key_service.create_api_key(request_limits={"max_concurrency": 1})
        proxy = self._register_proxy()
        client = self.app.test_client()
        headers = {"Authorization": f"Bearer {key['api_key']}"}
        for ending in ("complete", "error", "disconnect"):
            with self.subTest(ending=ending):
                closed = []

                def events():
                    try:
                        yield "data: first\n\n"
                        if ending == "error":
                            raise RuntimeError("stream failed")
                        yield "data: last\n\n"
                    finally:
                        closed.append(True)

                with patch.object(
                    proxy, "proxy_request", return_value=(Response(events(), mimetype="text/event-stream"), 200, None)
                ):
                    response = client.post(
                        "/v1/chat/completions",
                        json={"model": "demo/m1", "stream": True},
                        headers=headers,
                        buffered=False,
                    )
                blocked = client.post("/v1/chat/completions", json={"model": "demo/m1"}, headers=headers)
                self.assertEqual("api_key_concurrency_limit_exceeded", blocked.get_json()["error"]["code"])
                if ending == "error":
                    with self.assertRaisesRegex(RuntimeError, "stream failed"):
                        list(response.response)
                elif ending == "complete":
                    list(response.response)
                else:
                    response.close()
                self.assertEqual([True], closed)
                self.api_key_service.acquire_request(key)()
                response.close()
                response.close()

    def test_unstarted_stream_close_releases_slot_and_original_iterator(self) -> None:
        key = self.api_key_service.create_api_key(request_limits={"max_concurrency": 1})
        closed = []

        class UnstartedStream:
            def __iter__(self):
                return self

            def __next__(self):
                raise AssertionError("The unstarted body must not be consumed")

            def close(self):
                closed.append(True)

        response = Response(UnstartedStream())
        release = self.api_key_service.acquire_request(key)
        self.assertTrue(ProxyController._hold_api_key_stream_slot(response, release))
        response.close()
        self.assertEqual([True], closed)
        self.api_key_service.acquire_request(key)()


if __name__ == "__main__":
    unittest.main()
