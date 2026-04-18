from __future__ import annotations

import sys
import unittest
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.config.provider_runtime_factory import ProviderRuntimeFactory
from src.executors import OpenedUpstreamResponse
from src.services.provider_model_test_service import ProviderModelTestService


class FakeLogger:
    def info(self, msg: str, *args) -> None:
        del msg, args

    def warning(self, msg: str, *args) -> None:
        del msg, args

    def error(self, msg: str, *args) -> None:
        del msg, args

    def debug(self, msg: str, *args) -> None:
        del msg, args


class FakeStreamResponse:
    def __init__(self, chunks, *, content_type: str = "text/event-stream", status_code: int = 200):
        self._chunks = list(chunks)
        self.headers = {"Content-Type": content_type}
        self.status_code = status_code
        self.closed = False

    def iter_content(self, chunk_size=None):
        del chunk_size
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


class FakeBufferedResponse:
    def __init__(self, body: bytes, *, content_type: str = "application/json", status_code: int = 200):
        self.content = body
        self.headers = {"Content-Type": content_type}
        self.status_code = status_code
        self.closed = False

    def close(self) -> None:
        self.closed = True


class ProviderModelTestServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        self.service = ProviderModelTestService(ctx, ProviderRuntimeFactory(ctx))

    def test_openai_chat_stream_test_injects_include_usage_and_calculates_metrics(self) -> None:
        captured: dict[str, object] = {}
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"demo-model","choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n',
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"demo-model","choices":[],"usage":{"prompt_tokens":12,"completion_tokens":8,"total_tokens":20}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider, headers, body, **kwargs):
            del provider, headers, kwargs
            captured["body"] = body
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        self.service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        result = self.service.test_models(
            {
                "api": "https://example.com/v1/chat/completions",
                "source_format": "openai_chat",
                "transport": "http",
                "models": ["demo-model"],
            }
        )

        request_body = captured["body"]
        assert isinstance(request_body, dict)
        self.assertEqual({"include_usage": True}, request_body.get("stream_options"))
        self.assertEqual(1, len(result["results"]))
        self.assertTrue(result["results"][0]["available"])
        self.assertIsNotNone(result["results"][0]["first_token_latency_ms"])
        self.assertIsNotNone(result["results"][0]["tps"])

    def test_stream_success_without_usage_returns_null_tps(self) -> None:
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"demo-model","choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider, headers, body, **kwargs):
            del provider, headers, body, kwargs
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        self.service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        result = self.service.test_models(
            {
                "api": "https://example.com/v1/chat/completions",
                "source_format": "openai_chat",
                "transport": "http",
                "models": ["demo-model"],
            }
        )

        self.assertTrue(result["results"][0]["available"])
        self.assertIsNone(result["results"][0]["tps"])

    def test_nonstream_success_marks_available_but_without_latency_and_tps(self) -> None:
        fake_response = FakeBufferedResponse(
            (
                b'{"id":"chatcmpl_1","object":"chat.completion","model":"demo-model","choices":[{"index":0,"message":{"role":"assistant","content":"Hello"},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}'
            )
        )

        def stub_open_upstream_response(provider, headers, body, **kwargs):
            del provider, headers, body, kwargs
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="application/json",
                is_stream=False,
                stream_format="nonstream",
            )

        self.service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        result = self.service.test_models(
            {
                "api": "https://example.com/v1/chat/completions",
                "source_format": "openai_chat",
                "transport": "http",
                "models": ["demo-model"],
            }
        )

        self.assertTrue(result["results"][0]["available"])
        self.assertIsNone(result["results"][0]["first_token_latency_ms"])
        self.assertIsNone(result["results"][0]["tps"])

    def test_legacy_api_key_mode_sends_bearer_authorization_header(self) -> None:
        captured: dict[str, object] = {}
        fake_response = FakeBufferedResponse(
            b'{"id":"chatcmpl_1","object":"chat.completion","model":"demo-model","choices":[{"index":0,"message":{"role":"assistant","content":"Hello"},"finish_reason":"stop"}]}'
        )

        def stub_open_upstream_response(provider, headers, body, **kwargs):
            del provider, body, kwargs
            captured["headers"] = dict(headers)
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="application/json",
                is_stream=False,
                stream_format="nonstream",
            )

        self.service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        self.service.test_models(
            {
                "api": "https://example.com/v1/chat/completions",
                "source_format": "openai_chat",
                "transport": "http",
                "api_key": "sk-legacy-demo",
                "models": ["demo-model"],
            }
        )

        headers = captured.get("headers")
        assert isinstance(headers, dict)
        self.assertEqual(
            "Bearer sk-legacy-demo",
            headers["authorization"],
        )

    def test_build_provider_ignores_non_provider_test_fields(self) -> None:
        provider = self.service._build_provider(
            {
                "api": "https://example.com/v1/chat/completions",
                "source_format": "openai_chat",
                "transport": "http",
                "api_key": "sk-legacy-demo",
                "models": ["demo-model"],
                "auth_entry_id": "entry-a",
            },
            ["demo-model"],
        )

        self.assertEqual("https://example.com/v1/chat/completions", provider.api)
        self.assertEqual(("demo-model",), provider.model_list)
        self.assertEqual("sk-legacy-demo", provider.api_key)


if __name__ == "__main__":
    unittest.main()
