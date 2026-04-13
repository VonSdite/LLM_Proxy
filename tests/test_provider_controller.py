import sys
import unittest
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.presentation.provider_controller import ProviderController


class FakeLogger:
    def info(self, msg: str, *args) -> None:
        del msg, args

    def warning(self, msg: str, *args) -> None:
        del msg, args

    def error(self, msg: str, *args) -> None:
        del msg, args

    def debug(self, msg: str, *args) -> None:
        del msg, args


class FakeAuthService:
    def is_auth_enabled(self) -> bool:
        return False

    def validate_session(self, session_token: str | None) -> bool:
        del session_token
        return True


class FakeProviderService:
    def __init__(self) -> None:
        self.reorder_calls: list[list[str]] = []
        self.raise_error: Exception | None = None

    def reorder_providers(self, names: list[str]) -> dict:
        self.reorder_calls.append(list(names))
        if self.raise_error is not None:
            raise self.raise_error
        return {
            "count": len(names),
            "names": list(names),
        }


class FakeAuthGroupService:
    def __init__(self) -> None:
        self.header_calls: list[str] = []
        self.entry_header_calls: list[tuple[str, str]] = []
        self.headers_by_name: dict[str, dict[str, str]] = {
            "pool-a": {"Authorization": "Bearer sk-a", "x-org": "team-a"}
        }
        self.headers_by_entry: dict[tuple[str, str], dict[str, str]] = {
            ("pool-a", "entry-a"): {"Authorization": "Bearer sk-entry-a", "x-org": "team-a"}
        }

    def get_first_entry_headers(self, name: str) -> dict[str, str]:
        self.header_calls.append(name)
        if name not in self.headers_by_name:
            raise ValueError(f"Auth group not found: {name}")
        return dict(self.headers_by_name[name])

    def get_entry_headers(self, name: str, entry_id: str) -> dict[str, str]:
        self.entry_header_calls.append((name, entry_id))
        key = (name, entry_id)
        if key not in self.headers_by_entry:
            raise ValueError(f"Auth entry not found: {name}/{entry_id}")
        return dict(self.headers_by_entry[key])


class FakeModelDiscoveryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def fetch_models_preview(
        self,
        *,
        api: str,
        api_key: str | None = None,
        request_headers: dict[str, str] | None = None,
        proxy: str | None = None,
        timeout_seconds: str | None = None,
        verify_ssl: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "api": api,
                "api_key": api_key,
                "request_headers": dict(request_headers or {}),
                "proxy": proxy,
                "timeout_seconds": timeout_seconds,
                "verify_ssl": verify_ssl,
            }
        )
        return {
            "fetched_models": ["demo-model"],
            "fetched_count": 1,
        }


class FakeProviderModelTestService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def test_models(
        self,
        payload: dict[str, object],
        *,
        request_headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "payload": dict(payload),
                "request_headers": dict(request_headers or {}),
            }
        )
        models = payload.get("models") if isinstance(payload.get("models"), list) else []
        return {
            "results": [
                {
                    "requested_model": str(models[0]) if models else "demo-model",
                    "available": True,
                    "first_token_latency_ms": 12.5,
                    "tps": 18.2,
                    "response_model": "demo-model",
                    "error": None,
                }
            ]
        }


class ProviderControllerOrderRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        self.provider_service = FakeProviderService()
        self.provider_model_test_service = FakeProviderModelTestService()
        self.controller = ProviderController(
            ctx,
            self.provider_service,  # type: ignore[arg-type]
            self.provider_model_test_service,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            FakeAuthService(),  # type: ignore[arg-type]
        )
        self.client = app.test_client()

    def test_reorder_providers_route_returns_success(self) -> None:
        response = self.client.put(
            "/api/providers/order",
            json={"names": ["provider-b", "provider-a"]},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "count": 2,
                "names": ["provider-b", "provider-a"],
            },
            response.get_json(),
        )
        self.assertEqual(
            [["provider-b", "provider-a"]],
            self.provider_service.reorder_calls,
        )

    def test_reorder_providers_route_returns_bad_request_for_validation_error(self) -> None:
        self.provider_service.raise_error = ValueError(
            "Provider order must include every provider exactly once"
        )

        response = self.client.put(
            "/api/providers/order",
            json={"names": ["provider-a"]},
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {
                "error": "Provider order must include every provider exactly once",
            },
            response.get_json(),
        )


class ProviderControllerFetchModelsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        self.provider_service = FakeProviderService()
        self.auth_group_service = FakeAuthGroupService()
        self.model_discovery_service = FakeModelDiscoveryService()
        self.provider_model_test_service = FakeProviderModelTestService()
        self.controller = ProviderController(
            ctx,
            self.provider_service,  # type: ignore[arg-type]
            self.provider_model_test_service,  # type: ignore[arg-type]
            self.auth_group_service,  # type: ignore[arg-type]
            self.model_discovery_service,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            FakeAuthService(),  # type: ignore[arg-type]
        )
        self.client = app.test_client()

    def test_fetch_models_route_uses_first_auth_group_headers(self) -> None:
        response = self.client.get(
            "/api/providers/fetch-models",
            query_string={
                "api": "https://example.com/v1/chat/completions",
                "auth_group": "pool-a",
                "timeout_seconds": "15",
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(["pool-a"], self.auth_group_service.header_calls)
        self.assertEqual(
            {
                "api": "https://example.com/v1/chat/completions",
                "api_key": None,
                "request_headers": {
                    "Authorization": "Bearer sk-a",
                    "x-org": "team-a",
                },
                "proxy": None,
                "timeout_seconds": "15",
                "verify_ssl": None,
            },
            self.model_discovery_service.calls[0],
        )

    def test_fetch_models_route_rejects_auth_group_and_api_key_together(self) -> None:
        response = self.client.get(
            "/api/providers/fetch-models",
            query_string={
                "api": "https://example.com/v1/chat/completions",
                "auth_group": "pool-a",
                "api_key": "sk-demo",
            },
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {
                "error": "Model fetch must use either auth_group or api_key, not both",
            },
            response.get_json(),
        )
        self.assertEqual([], self.model_discovery_service.calls)


class ProviderControllerTestModelsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        self.provider_service = FakeProviderService()
        self.auth_group_service = FakeAuthGroupService()
        self.provider_model_test_service = FakeProviderModelTestService()
        self.controller = ProviderController(
            ctx,
            self.provider_service,  # type: ignore[arg-type]
            self.provider_model_test_service,  # type: ignore[arg-type]
            self.auth_group_service,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            FakeAuthService(),  # type: ignore[arg-type]
        )
        self.client = app.test_client()

    def test_test_models_route_uses_selected_auth_entry_headers(self) -> None:
        response = self.client.post(
            "/api/providers/test-models",
            json={
                "api": "https://example.com/v1/chat/completions",
                "auth_group": "pool-a",
                "auth_entry_id": "entry-a",
                "models": ["demo-model"],
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual([("pool-a", "entry-a")], self.auth_group_service.entry_header_calls)
        self.assertEqual(
            {
                "Authorization": "Bearer sk-entry-a",
                "x-org": "team-a",
            },
            self.provider_model_test_service.calls[0]["request_headers"],
        )

    def test_test_models_route_rejects_auth_group_and_api_key_together(self) -> None:
        response = self.client.post(
            "/api/providers/test-models",
            json={
                "api": "https://example.com/v1/chat/completions",
                "api_key": "sk-demo",
                "auth_group": "pool-a",
                "auth_entry_id": "entry-a",
                "models": ["demo-model"],
            },
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {
                "error": "Model test must use either auth_group or api_key, not both",
            },
            response.get_json(),
        )
        self.assertEqual([], self.provider_model_test_service.calls)

    def test_test_models_route_requires_auth_entry_id_when_auth_group_is_set(self) -> None:
        response = self.client.post(
            "/api/providers/test-models",
            json={
                "api": "https://example.com/v1/chat/completions",
                "auth_group": "pool-a",
                "models": ["demo-model"],
            },
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {
                "error": "Model test auth_group requires auth_entry_id",
            },
            response.get_json(),
        )
        self.assertEqual([], self.provider_model_test_service.calls)

    def test_test_models_route_supports_legacy_api_key_mode(self) -> None:
        response = self.client.post(
            "/api/providers/test-models",
            json={
                "api": "https://example.com/v1/chat/completions",
                "api_key": "sk-legacy-demo",
                "models": ["demo-model"],
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual([], self.auth_group_service.entry_header_calls)
        self.assertEqual({}, self.provider_model_test_service.calls[0]["request_headers"])
        self.assertEqual("sk-legacy-demo", self.provider_model_test_service.calls[0]["payload"]["api_key"])


if __name__ == "__main__":
    unittest.main()
