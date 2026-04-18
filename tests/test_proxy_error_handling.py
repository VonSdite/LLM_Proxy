from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional, cast

import requests
import websocket
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.external import LLMProvider, StaticUpstreamResponse
from src.external.stream_probe import BufferedUpstreamResponse
from src.hooks import BaseHook, HookContext, HookErrorType
from src.presentation.proxy_controller import ProxyController
from src.services.proxy_service import ProxyErrorInfo, ProxyService


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _log(self, level: str, msg: str, *args) -> None:
        rendered = msg % args if args else msg
        self.records.append((level, rendered))

    def info(self, msg: str, *args) -> None:
        self._log("info", msg, *args)

    def warning(self, msg: str, *args) -> None:
        self._log("warning", msg, *args)

    def error(self, msg: str, *args) -> None:
        self._log("error", msg, *args)

    def debug(self, msg: str, *args) -> None:
        self._log("debug", msg, *args)

    def messages(self, level: str) -> list[str]:
        return [message for record_level, message in self.records if record_level == level]


class FakeConfigManager:
    def __init__(self, whitelist_enabled: bool = False) -> None:
        self._whitelist_enabled = whitelist_enabled

    def is_chat_whitelist_enabled(self) -> bool:
        return self._whitelist_enabled


class FakeUserService:
    _UNSET = object()

    def __init__(
        self,
        *,
        user: Optional[dict[str, Any]] | object = _UNSET,
        accessible_models: Optional[list[str]] = None,
    ) -> None:
        self._user: Optional[dict[str, Any]] = (
            {"username": "tester", "model_permissions": "*"}
            if user is self._UNSET
            else cast(Optional[Dict[str, Any]], user)
        )
        self._accessible_models = None if accessible_models is None else list(accessible_models)

    def get_user_by_ip(
        self,
        ip_address: str,
        require_whitelist_access: bool = True,
    ) -> Optional[dict[str, Any]]:
        del ip_address, require_whitelist_access
        return self._user

    def can_user_access_model(
        self,
        user: Optional[dict[str, Any]],
        model_name: str,
        available_models=None,
    ) -> bool:
        del user
        if self._accessible_models is None:
            return True
        return model_name in set(self._accessible_models)

    def get_accessible_models_for_user(
        self,
        user: Optional[dict[str, Any]],
        available_models=None,
    ) -> list[str]:
        del user
        if self._accessible_models is None:
            return list(available_models or [])
        if available_models is None:
            return list(self._accessible_models)
        available_set = set(available_models)
        return [model_name for model_name in self._accessible_models if model_name in available_set]


class FakeLogService:
    @staticmethod
    def log_request(
        request_model: str,
        response_model: str | None,
        total_tokens: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        start_time=None,
        end_time=None,
        ip_address: str | None = None,
    ) -> None:
        del (
            request_model,
            response_model,
            total_tokens,
            prompt_tokens,
            completion_tokens,
            start_time,
            end_time,
            ip_address,
        )


class FakeProviderManager:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def get_provider_for_model(self, model_name: str):
        if model_name in {f"{self._provider.name}/{self._provider.model_list[0]}", self._provider.model_list[0]}:
            return self._provider
        return None

    def list_model_names(self) -> tuple[str, ...]:
        return tuple(f"{self._provider.name}/{model}" for model in self._provider.model_list)

    def get_provider_view(self, provider_name: str):
        if provider_name != self._provider.name:
            return None
        return SimpleNamespace(
            name=self._provider.name,
            api=self._provider.api,
            transport=self._provider.transport,
            source_format=self._provider.source_format,
            target_format=self._provider.target_format,
            target_formats=self._provider.target_formats,
            model_list=self._provider.model_list,
            proxy=self._provider.proxy,
            timeout_seconds=self._provider.timeout_seconds,
            max_retries=self._provider.max_retries,
            verify_ssl=self._provider.verify_ssl,
            hook=self._provider.hook,
        )


class StubProxyService:
    def __init__(self, proxy_result) -> None:
        self._proxy_result = proxy_result

    def proxy_request(self, *args, **kwargs):
        del args, kwargs
        return self._proxy_result


class RecordingProxyService:
    def __init__(self, proxy_result) -> None:
        self._proxy_result = proxy_result
        self.last_args: tuple[object, ...] | None = None
        self.last_kwargs: dict[str, object] | None = None

    def proxy_request(self, *args, **kwargs):
        self.last_args = args
        self.last_kwargs = kwargs
        return self._proxy_result


class ContextRecordingHook(BaseHook):
    def __init__(self) -> None:
        self.contexts: list[HookContext] = []

    def header_hook(self, ctx: HookContext, headers: dict[str, str]) -> dict[str, str]:
        self.contexts.append(ctx)
        return headers


class ProxyControllerErrorFormatTests(unittest.TestCase):
    def test_list_models_includes_provider_protocol_metadata(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/responses",
            transport="http",
            source_format="openai_responses",
            target_format="codex",
            model_list=("gpt-5-codex",),
        )
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService((None, 200, None)),
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().get("/v1/models")

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "object": "list",
                "data": [
                    {
                        "id": "demo/gpt-5-codex",
                        "object": "model",
                        "owned_by": "demo",
                        "provider_name": "demo",
                        "source_format": "openai_responses",
                        "target_formats": ["codex"],
                        "transport": "http",
                    }
                ],
            },
            response.get_json(),
        )

    def test_list_models_filters_to_models_allowed_for_whitelisted_user(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1", "gpt-4.1-mini"),
        )
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(whitelist_enabled=True),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService((None, 200, None)),
            FakeUserService(accessible_models=["demo/gpt-4.1-mini"]),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().get("/v1/models", environ_base={"REMOTE_ADDR": "127.0.0.1"})

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(["demo/gpt-4.1-mini"], [item["id"] for item in payload["data"]])

    def test_list_models_rejects_non_whitelisted_ip_when_whitelist_enabled(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
        )
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(whitelist_enabled=True),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService((None, 200, None)),
            FakeUserService(user=None),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().get("/v1/models", environ_base={"REMOTE_ADDR": "127.0.0.1"})

        self.assertEqual(403, response.status_code)
        self.assertEqual("ip_not_whitelisted", response.get_json()["error"]["code"])

    def test_chat_completions_rejects_model_not_allowed_for_whitelisted_user(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1", "gpt-4.1-mini"),
        )
        logger = FakeLogger()
        app = Flask(__name__)
        ctx = AppContext(
            logger=logger,
            config_manager=FakeConfigManager(whitelist_enabled=True),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService((None, 200, None)),
            FakeUserService(accessible_models=["demo/gpt-4.1-mini"]),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().post(
            "/v1/chat/completions",
            json={"model": "demo/gpt-4.1", "messages": [{"role": "user", "content": "hi"}]},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(403, response.status_code)
        self.assertEqual("model_not_allowed", response.get_json()["error"]["code"])
        self.assertTrue(
            any("is not allowed to access model=demo/gpt-4.1" in msg for msg in logger.messages("warning"))
        )

    def test_chat_completions_returns_openai_style_error_payload_for_upstream_failures(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
        )
        logger = FakeLogger()
        app = Flask(__name__)
        ctx = AppContext(
            logger=logger,
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService(
                (
                    None,
                    502,
                    ProxyErrorInfo(
                        message="HTTP upstream request failed after 2 attempts: dial tcp timeout",
                        status_code=502,
                        error_type="upstream_error",
                        error_code="upstream_request_failed",
                    ),
                )
            ),
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().post(
            "/v1/chat/completions",
            json={"model": "demo/gpt-4.1", "messages": [{"role": "user", "content": "hi"}]},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(502, response.status_code)
        self.assertEqual(
            {
                "error": {
                    "message": "HTTP upstream request failed after 2 attempts: dial tcp timeout",
                    "type": "upstream_error",
                    "param": None,
                    "code": "upstream_request_failed",
                }
            },
            response.get_json(),
        )
        self.assertTrue(
            any("upstream_error=HTTP upstream request failed after 2 attempts: dial tcp timeout" in msg for msg in logger.messages("error"))
        )

    def test_responses_route_rejects_target_format_mismatch(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
            target_format="openai_chat",
        )
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService((None, 200, None)),
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().post(
            "/v1/responses",
            json={"model": "demo/gpt-4.1", "input": "hi"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {
                "error": {
                    "message": "Model demo/gpt-4.1 is configured for downstream formats openai_chat, not one of openai_responses, codex",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "target_format_mismatch",
                }
            },
            response.get_json(),
        )

    def test_responses_route_allows_openai_responses_target(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
            target_format="openai_responses",
        )
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService(
                (
                    app.response_class(
                        '{"id":"resp_1","object":"response"}',
                        status=200,
                        mimetype="application/json",
                    ),
                    200,
                    None,
                )
            ),
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().post(
            "/v1/responses",
            json={"model": "demo/gpt-4.1", "input": "hi"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual('{"id":"resp_1","object":"response"}', response.get_data(as_text=True))

    def test_chat_route_resolves_target_format_from_multi_target_provider(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
            target_formats=("openai_chat", "claude_chat"),
        )
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        proxy_service = RecordingProxyService(
            (
                app.response_class(
                    '{"id":"chatcmpl_1","object":"chat.completion"}',
                    status=200,
                    mimetype="application/json",
                ),
                200,
                None,
            )
        )
        ProxyController(
            ctx,
            proxy_service,
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().post(
            "/v1/chat/completions",
            json={"model": "demo/gpt-4.1", "messages": [{"role": "user", "content": "hi"}]},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(200, response.status_code)
        self.assertIsNotNone(proxy_service.last_kwargs)
        assert proxy_service.last_kwargs is not None
        self.assertEqual("openai_chat", proxy_service.last_kwargs["resolved_target_format"])

    def test_responses_route_allows_codex_target(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/responses",
            model_list=("gpt-5.2",),
            target_format="codex",
        )
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService(
                (
                    app.response_class(
                        '{"id":"resp_1","object":"response"}',
                        status=200,
                        mimetype="application/json",
                    ),
                    200,
                    None,
                )
            ),
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().post(
            "/v1/responses",
            json={"model": "demo/gpt-5.2", "input": "hi"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual('{"id":"resp_1","object":"response"}', response.get_data(as_text=True))

    def test_messages_route_rejects_target_format_mismatch(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
            target_format="openai_chat",
        )
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService((None, 200, None)),
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().post(
            "/v1/messages",
            json={"model": "demo/gpt-4.1", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Model demo/gpt-4.1 is configured for downstream formats openai_chat, not claude_chat",
                },
            },
            response.get_json(),
        )

    def test_messages_route_allows_claude_target(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/messages",
            model_list=("claude-sonnet-4-5",),
            target_format="claude_chat",
        )
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService(
                (
                    app.response_class(
                        '{"id":"msg_1","type":"message"}',
                        status=200,
                        mimetype="application/json",
                    ),
                    200,
                    None,
                )
            ),
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().post(
            "/v1/messages",
            json={
                "model": "demo/claude-sonnet-4-5",
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual('{"id":"msg_1","type":"message"}', response.get_data(as_text=True))

    def test_messages_route_returns_claude_style_error_payload_for_upstream_failures(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/messages",
            model_list=("claude-sonnet-4-5",),
            target_format="claude_chat",
        )
        logger = FakeLogger()
        app = Flask(__name__)
        ctx = AppContext(
            logger=logger,
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        ProxyController(
            ctx,
            StubProxyService(
                (
                    None,
                    502,
                    ProxyErrorInfo(
                        message="Upstream Claude request failed: overloaded",
                        status_code=502,
                        error_type="api_error",
                        error_code="upstream_overloaded",
                    ),
                )
            ),
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
        )

        response = app.test_client().post(
            "/v1/messages",
            json={
                "model": "demo/claude-sonnet-4-5",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(502, response.status_code)
        self.assertEqual(
            {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": "Upstream Claude request failed: overloaded",
                },
            },
            response.get_json(),
        )
        self.assertTrue(any("upstream_error=Upstream Claude request failed: overloaded" in msg for msg in logger.messages("error")))

class ProxyServiceErrorLoggingTests(unittest.TestCase):
    def test_proxy_service_logs_upstream_error_payload(self) -> None:
        logger = FakeLogger()
        ctx = AppContext(
            logger=logger,
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=Flask(__name__),
        )
        service = ProxyService(ctx)
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        upstream_response = BufferedUpstreamResponse(
            StaticUpstreamResponse(
                status_code=429,
                headers={"Content-Type": "application/json"},
            ),
            b'{"error":{"message":"Rate limit reached","type":"rate_limit_error","code":"rate_limit_exceeded"}}',
        )
        service._open_upstream_response = lambda *args, **kwargs: (upstream_response, False, 429)  # type: ignore[method-assign]

        response, status_code, failure_info = service.proxy_request(
            provider,
            {"model": "demo/gpt-4.1", "messages": [{"role": "user", "content": "hi"}]},
            {},
        )

        self.assertIsNone(failure_info)
        self.assertEqual(429, status_code)
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("Rate limit reached", response.get_data(as_text=True))
        self.assertTrue(
            any(
                "Rate limit reached (type=rate_limit_error, code=rate_limit_exceeded)" in msg
                for msg in logger.messages("warning")
            )
        )


class ProxyServiceRetryHookContextTests(unittest.TestCase):
    def _build_service(self) -> ProxyService:
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=Flask(__name__),
        )
        return ProxyService(ctx)

    @staticmethod
    def _chat_completion_response(status_code: int, body: bytes) -> tuple[BufferedUpstreamResponse, bool, int]:
        response = BufferedUpstreamResponse(
            StaticUpstreamResponse(
                status_code=status_code,
                headers={"Content-Type": "application/json"},
            ),
            body,
        )
        return response, False, status_code

    @staticmethod
    def _success_response() -> tuple[BufferedUpstreamResponse, bool, int]:
        return ProxyServiceRetryHookContextTests._chat_completion_response(
            200,
            (
                b'{"id":"chatcmpl_1","object":"chat.completion","created":123,"model":"gpt-4.1",'
                b'"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}'
            ),
        )

    def test_hook_sees_previous_retryable_status_code_on_next_attempt(self) -> None:
        service = self._build_service()
        hook = ContextRecordingHook()
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
            hook=hook,
            max_retries=2,
        )
        attempts = iter(
            [
                self._chat_completion_response(
                    429,
                    b'{"error":{"message":"Rate limit reached","type":"rate_limit_error","code":"rate_limit_exceeded"}}',
                ),
                self._success_response(),
            ]
        )
        service._open_upstream_response = lambda *args, **kwargs: next(attempts)  # type: ignore[method-assign]

        response, status_code, failure_info = service.proxy_request(
            provider,
            {"model": "demo/gpt-4.1", "messages": [{"role": "user", "content": "hi"}]},
            {},
        )

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)
        self.assertEqual(2, len(hook.contexts))
        self.assertIsNone(hook.contexts[0].last_status_code)
        self.assertIsNone(hook.contexts[0].last_error_type)
        self.assertEqual(429, hook.contexts[1].last_status_code)
        self.assertIsNone(hook.contexts[1].last_error_type)

    def test_hook_sees_timeout_error_type_on_next_attempt(self) -> None:
        service = self._build_service()
        hook = ContextRecordingHook()
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
            hook=hook,
            max_retries=2,
        )
        calls = {"count": 0}

        def stub_open_upstream_response(*args, **kwargs):
            del args, kwargs
            if calls["count"] == 0:
                calls["count"] += 1
                raise requests.exceptions.Timeout("timed out")
            return self._success_response()

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        response, status_code, failure_info = service.proxy_request(
            provider,
            {"model": "demo/gpt-4.1", "messages": [{"role": "user", "content": "hi"}]},
            {},
        )

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)
        self.assertEqual(2, len(hook.contexts))
        self.assertIsNone(hook.contexts[1].last_status_code)
        self.assertEqual(HookErrorType.TIMEOUT, hook.contexts[1].last_error_type)

    def test_hook_sees_connection_error_type_on_next_attempt(self) -> None:
        service = self._build_service()
        hook = ContextRecordingHook()
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
            hook=hook,
            max_retries=2,
        )
        calls = {"count": 0}

        def stub_open_upstream_response(*args, **kwargs):
            del args, kwargs
            if calls["count"] == 0:
                calls["count"] += 1
                raise requests.exceptions.ConnectionError("connection lost")
            return self._success_response()

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        response, status_code, failure_info = service.proxy_request(
            provider,
            {"model": "demo/gpt-4.1", "messages": [{"role": "user", "content": "hi"}]},
            {},
        )

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)
        self.assertEqual(2, len(hook.contexts))
        self.assertIsNone(hook.contexts[1].last_status_code)
        self.assertEqual(HookErrorType.CONNECTION_ERROR, hook.contexts[1].last_error_type)

    def test_hook_sees_websocket_error_type_on_next_attempt(self) -> None:
        service = self._build_service()
        hook = ContextRecordingHook()
        provider = LLMProvider(
            name="demo",
            api="wss://example.com/v1/chat/completions",
            transport="websocket",
            model_list=("gpt-4.1",),
            hook=hook,
            max_retries=2,
        )
        calls = {"count": 0}

        def stub_open_upstream_response(*args, **kwargs):
            del args, kwargs
            if calls["count"] == 0:
                calls["count"] += 1
                raise websocket.WebSocketException("socket closed")
            return self._success_response()

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        response, status_code, failure_info = service.proxy_request(
            provider,
            {"model": "demo/gpt-4.1", "messages": [{"role": "user", "content": "hi"}]},
            {},
        )

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)
        self.assertEqual(2, len(hook.contexts))
        self.assertIsNone(hook.contexts[1].last_status_code)
        self.assertEqual(HookErrorType.WEBSOCKET_ERROR, hook.contexts[1].last_error_type)

    def test_non_retryable_status_does_not_trigger_second_attempt(self) -> None:
        service = self._build_service()
        hook = ContextRecordingHook()
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            model_list=("gpt-4.1",),
            hook=hook,
            max_retries=2,
        )
        attempts = iter(
            [
                self._chat_completion_response(
                    401,
                    b'{"error":{"message":"Unauthorized","type":"invalid_request_error","code":"invalid_api_key"}}',
                )
            ]
        )
        service._open_upstream_response = lambda *args, **kwargs: next(attempts)  # type: ignore[method-assign]

        response, status_code, failure_info = service.proxy_request(
            provider,
            {"model": "demo/gpt-4.1", "messages": [{"role": "user", "content": "hi"}]},
            {},
        )

        self.assertIsNone(failure_info)
        self.assertEqual(401, status_code)
        self.assertIsNotNone(response)
        self.assertEqual(1, len(hook.contexts))
        self.assertIsNone(hook.contexts[0].last_status_code)
        self.assertIsNone(hook.contexts[0].last_error_type)


if __name__ == "__main__":
    unittest.main()
