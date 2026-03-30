import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.external import LLMProvider, StaticUpstreamResponse
from src.external.stream_probe import BufferedUpstreamResponse
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
    @staticmethod
    def is_chat_whitelist_enabled() -> bool:
        return False


class FakeUserService:
    @staticmethod
    def get_user_by_ip(ip_address: str, require_whitelist_access: bool = True):
        del ip_address, require_whitelist_access
        return {"username": "tester"}


class FakeLogService:
    @staticmethod
    def log_request(**kwargs) -> None:
        del kwargs


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
                        "target_format": "codex",
                        "transport": "http",
                    }
                ],
            },
            response.get_json(),
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
                    "message": "Model demo/gpt-4.1 is configured for downstream format openai_chat, not one of openai_responses, codex",
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
                    "message": "Model demo/gpt-4.1 is configured for downstream format openai_chat, not claude_chat",
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
        self.assertIn("Rate limit reached", response.get_data(as_text=True))
        self.assertTrue(
            any(
                "Rate limit reached (type=rate_limit_error, code=rate_limit_exceeded)" in msg
                for msg in logger.messages("warning")
            )
        )


if __name__ == "__main__":
    unittest.main()
