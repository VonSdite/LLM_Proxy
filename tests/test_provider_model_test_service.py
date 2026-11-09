from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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


class FakeLogService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def log_request(self, **kwargs) -> int:
        self.calls.append(dict(kwargs))
        return len(self.calls)


class ProviderModelTestServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=None,  # type: ignore[arg-type]
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        self.log_service = FakeLogService()
        self.service = ProviderModelTestService(
            ctx,
            ProviderRuntimeFactory(ctx),
            self.log_service,  # type: ignore[arg-type]
        )

    def test_openai_chat_stream_test_injects_include_usage_and_calculates_metrics(self) -> None:
        captured: dict[str, object] = {}
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"demo-model","choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n',
                b'data: {"choices":[{"delta":{"content":" world"},"index":0}]}\n\n',
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
                "name": "demo",
                "api": "https://example.com/v1/chat/completions",
                "source_format": "openai_chat",
                "models": ["demo-model"],
            },
            ip_address="10.0.0.8",
        )

        request_body = captured["body"]
        assert isinstance(request_body, dict)
        self.assertEqual({"include_usage": True}, request_body.get("stream_options"))
        self.assertEqual(1, len(result["results"]))
        self.assertTrue(result["results"][0]["available"])
        self.assertIsNotNone(result["results"][0]["first_token_latency_ms"])
        self.assertIsNotNone(result["results"][0]["tps"])
        self.assertEqual(1, len(self.log_service.calls))
        self.assertEqual(
            {
                "request_model": "demo/demo-model",
                "response_model": "demo-model",
                "total_tokens": 20,
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "ip_address": "10.0.0.8",
            },
            {
                key: self.log_service.calls[0][key]
                for key in (
                    "request_model",
                    "response_model",
                    "total_tokens",
                    "prompt_tokens",
                    "completion_tokens",
                    "ip_address",
                )
            },
        )
        self.assertIsNotNone(self.log_service.calls[0]["start_time"])
        self.assertIsNotNone(self.log_service.calls[0]["end_time"])

    def test_glm_claude_test_enables_reasoning_by_default(self) -> None:
        provider = self.service._build_provider(
            {
                "name": "zai",
                "api": "https://example.com/v1/messages",
                "source_format": "claude_chat",
            },
            ["glm-5.3"],
        )
        translator = self.service._translator_registry.get("claude_chat", "openai_chat")

        _, _, translated_request, _ = self.service._build_upstream_request(
            provider,
            "glm-5.3",
            request_headers={},
            auth_entry_id=None,
            translator=translator,
            attempt=0,
            previous_status_code=None,
            previous_error_type=None,
        )

        self.assertEqual({"type": "enabled", "budget_tokens": 2048}, translated_request["thinking"])
        self.assertEqual(2240, translated_request["max_tokens"])
        self.assertEqual(192, translated_request["max_tokens"] - translated_request["thinking"]["budget_tokens"])

    def test_default_sampling_parameters_are_omitted_across_protocols(self) -> None:
        for source_format, token_field in (
            ("openai_chat", "max_tokens"),
            ("openai_responses", "max_output_tokens"),
            ("claude_chat", "max_tokens"),
        ):
            for model in ("custom-model", "glm-5.3"):
                with self.subTest(source_format=source_format, model=model):
                    provider = self.service._build_provider(
                        {"api": "https://example.com", "source_format": source_format}, [model]
                    )
                    _, original, translated, _ = self.service._build_upstream_request(
                        provider,
                        model,
                        request_headers={},
                        auth_entry_id=None,
                        translator=self.service._translator_registry.get(source_format, "openai_chat"),
                        attempt=0,
                        previous_status_code=None,
                        previous_error_type=None,
                    )
                    for body in (original, translated):
                        self.assertNotIn("temperature", body)
                        self.assertNotIn("top_p", body)
                        self.assertTrue(body["stream"])
                    self.assertEqual(
                        2240 if source_format == "claude_chat" and model == "glm-5.3" else 192, translated[token_field]
                    )

    def test_explicit_token_parameter_fallback_survives_retry_and_is_local_to_model(self) -> None:
        responses = [
            FakeBufferedResponse(
                b'{"error":{"message":"Unsupported parameter: \'max_tokens\' is not supported. Use \'max_completion_tokens\' instead."}}',
                status_code=400,
            ),
            FakeBufferedResponse(b'{"error":{"message":"busy"}}', status_code=429),
            FakeBufferedResponse(b'{"choices":[{"message":{"content":"Hello"}}]}'),
            FakeBufferedResponse(b'{"choices":[{"message":{"content":"Hello"}}]}'),
        ]
        bodies = []

        def open_response(provider, headers, body, **kwargs):
            del provider, headers, kwargs
            response = responses[len(bodies)]
            bodies.append(body)
            return OpenedUpstreamResponse(
                response=response,
                status_code=response.status_code,
                content_type="application/json",
                is_stream=False,
                stream_format="nonstream",
            )

        self.service._open_upstream_response = open_response
        result = self.service.test_models(
            {
                "api": "https://example.com",
                "source_format": "openai_chat",
                "max_retries": 3,
                "models": ["alias-a", "alias-b"],
            }
        )
        self.assertTrue(all(item["available"] for item in result["results"]))
        self.assertEqual(4, len(bodies))
        for body, token_field in zip(
            bodies, ["max_tokens", "max_completion_tokens", "max_completion_tokens", "max_tokens"]
        ):
            self.assertEqual(192, body[token_field])
            self.assertFalse("max_tokens" in body and "max_completion_tokens" in body)
            self.assertEqual({"include_usage": True}, body["stream_options"])
        self.assertEqual(2, len(self.log_service.calls))
        self.assertTrue(all(response.closed for response in responses))

    def test_parameter_fallback_is_bounded_and_only_handles_explicit_chat_parameter_errors(self) -> None:
        explicit = "Unsupported parameter: 'max_tokens' is not supported. Use 'max_completion_tokens' instead."
        for status, message, protocol, limit, expected_calls in (
            (400, explicit, "openai_chat", 1, 1),
            (400, explicit, "openai_chat", 5, 2),
            (422, "max_tokens is not allowed; use max_completion_tokens", "openai_chat", 3, 2),
            (401, explicit, "openai_chat", 3, 1),
            (400, explicit, "claude_chat", 3, 1),
            (400, explicit, "openai_responses", 3, 1),
            (400, "Model not found", "openai_chat", 3, 1),
            (400, "max_tokens exceeds max_completion_tokens limit", "openai_chat", 3, 1),
            (400, "Unsupported parameter: 'max_completion_tokens'. Use 'max_tokens' instead.", "openai_chat", 3, 1),
        ):
            with self.subTest(status=status, message=message, protocol=protocol, limit=limit):
                calls = []

                def open_response(provider, headers, body, **kwargs):
                    del provider, headers, kwargs
                    calls.append(body)
                    response = FakeBufferedResponse(
                        json.dumps({"error": {"message": message}}).encode(), status_code=status
                    )
                    return OpenedUpstreamResponse(
                        response=response,
                        status_code=status,
                        content_type="application/json",
                        is_stream=False,
                        stream_format="nonstream",
                    )

                self.service._open_upstream_response = open_response
                result = self.service.test_models(
                    {
                        "api": "https://example.com",
                        "source_format": protocol,
                        "max_retries": limit,
                        "models": ["custom-model"],
                    }
                )
                self.assertFalse(result["results"][0]["available"])
                self.assertEqual(message, result["results"][0]["error"])
                self.assertEqual(expected_calls, len(calls))
                self.assertEqual([], self.log_service.calls)

    def test_non_glm_claude_test_does_not_force_reasoning(self) -> None:
        provider = self.service._build_provider(
            {
                "name": "anthropic",
                "api": "https://example.com/v1/messages",
                "source_format": "claude_chat",
            },
            ["claude-sonnet-4-5"],
        )
        translator = self.service._translator_registry.get("claude_chat", "openai_chat")

        _, _, translated_request, _ = self.service._build_upstream_request(
            provider,
            "claude-sonnet-4-5",
            request_headers={},
            auth_entry_id=None,
            translator=translator,
            attempt=0,
            previous_status_code=None,
            previous_error_type=None,
        )

        self.assertNotIn("thinking", translated_request)

    def test_provider_named_like_model_prefix_keeps_full_upstream_model_id(self) -> None:
        captured: dict[str, object] = {}
        fake_response = FakeBufferedResponse(
            b'{"id":"chatcmpl_1","object":"chat.completion","model":"aliyun/deepseek","choices":[{"index":0,"message":{"role":"assistant","content":"Hello"},"finish_reason":"stop"}]}'
        )

        def stub_open_upstream_response(provider, headers, body, **kwargs):
            del provider, headers, kwargs
            captured["body"] = body
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
                "name": "aliyun",
                "api": "https://example.com/v1/chat/completions",
                "source_format": "openai_chat",
                "models": ["aliyun/deepseek"],
            }
        )

        request_body = captured["body"]
        assert isinstance(request_body, dict)
        self.assertEqual("aliyun/deepseek", request_body["model"])
        self.assertTrue(result["results"][0]["available"])

    def test_openai_chat_stream_reasoning_delta_counts_as_first_token(self) -> None:
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"demo-model","choices":[{"delta":{"content":null,"reasoning_content":"The"},"index":0}],"usage":{"prompt_tokens":12,"completion_tokens":1,"total_tokens":13}}\n\n',
                b'data: {"choices":[{"delta":{"content":"Answer"},"index":0}]}\n\n',
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"demo-model","choices":[],"usage":{"prompt_tokens":12,"completion_tokens":8,"total_tokens":20}}\n\n',
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
                "models": ["demo-model"],
            }
        )

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
                "models": ["demo-model"],
            }
        )

        self.assertTrue(result["results"][0]["available"])
        self.assertIsNone(result["results"][0]["tps"])

    def test_stream_tps_is_unavailable_when_output_events_arrive_in_one_network_chunk(self) -> None:
        fake_response = FakeStreamResponse(
            [
                (
                    b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"demo-model",'
                    b'"choices":[{"delta":{"content":"Hello"},"index":0}]}\n\n'
                    b'data: {"choices":[{"delta":{"content":" world"},"index":0}]}\n\n'
                    b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"demo-model",'
                    b'"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":100,"total_tokens":112}}\n\n'
                    b"data: [DONE]\n\n"
                )
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

        with patch(
            "src.services.provider_model_test_service.time.perf_counter",
            side_effect=[100.0, 110.0],
        ):
            result = self.service.test_models(
                {
                    "api": "https://example.com/v1/chat/completions",
                    "source_format": "openai_chat",
                    "models": ["demo-model"],
                }
            )

        self.assertEqual(10000.0, result["results"][0]["first_token_latency_ms"])
        self.assertIsNone(result["results"][0]["tps"])

    def _measure_timed_stream(self, events, source_format="openai_chat"):
        """通过真实解码与翻译链路验证接收时间，准备请求耗时不进入 TTFT。"""
        clock = [10.0]
        response = FakeStreamResponse([])

        def iter_content(chunk_size=None):
            del chunk_size
            for timestamp, payload in events:
                clock[0] = timestamp
                yield payload if isinstance(payload, bytes) else f"data: {json.dumps(payload)}\n\n".encode()

        response.iter_content = iter_content
        build_request = self.service._build_upstream_request

        def prepare(*args, **kwargs):
            built = build_request(*args, **kwargs)
            clock[0] = 100.0
            return built

        opened = OpenedUpstreamResponse(
            response=response,
            status_code=200,
            content_type="text/event-stream",
            is_stream=True,
            stream_format="sse_json",
        )
        with (
            patch("src.services.provider_model_test_service.time.perf_counter", side_effect=lambda: clock[0]),
            patch.object(self.service, "_build_upstream_request", side_effect=prepare),
            patch.object(self.service, "_open_upstream_response", return_value=opened),
        ):
            result = self.service.test_models(
                {
                    "api": "https://example.com",
                    "source_format": source_format,
                    "models": ["test-model"],
                    "max_retries": 1,
                }
            )["results"][0]
        self.assertTrue(response.closed)
        self.assertTrue(result["available"])
        return result

    def test_ttft_ignores_preparation_and_empty_events_tps_excludes_both_waits(self) -> None:
        result = self._measure_timed_stream(
            [
                (101.0, b": heartbeat\n\n"),
                (101.5, {"choices": [{"delta": {"role": "assistant", "content": ""}}]}),
                (102.0, {"choices": [{"delta": {"content": "Hello"}}]}),
                (104.0, {"choices": [{"delta": {"content": " world"}}]}),
                (120.0, {"choices": [], "usage": {"completion_tokens": 101}}),
                (125.0, b"data: [DONE]\n\n"),
            ]
        )
        self.assertEqual(2000.0, result["first_token_latency_ms"])
        self.assertEqual(50.0, result["tps"])

    def test_tps_handles_missing_small_counts_and_invalid_intervals(self) -> None:
        for tokens, last_time in ((None, 104.0), (0, 104.0), (1, 104.0), (101, 102.0), (101, 101.0)):
            with self.subTest(tokens=tokens, last_time=last_time):
                result = self._measure_timed_stream(
                    [
                        (102.0, {"choices": [{"delta": {"content": "Hello"}}]}),
                        (last_time, {"choices": [{"delta": {"content": " world"}}]}),
                        (120.0, {"choices": [], "usage": {"completion_tokens": tokens}}),
                        (125.0, b"data: [DONE]\n\n"),
                    ]
                )
                self.assertEqual(2000.0, result["first_token_latency_ms"])
                self.assertIsNone(result["tps"])

    def test_reasoning_counts_only_when_its_generation_interval_is_observable(self) -> None:
        for reasoning_streamed in (False, True):
            with self.subTest(reasoning_streamed=reasoning_streamed):
                result = self._measure_timed_stream(
                    [
                        (
                            102.0,
                            {
                                "choices": [
                                    {"delta": {"reasoning_content" if reasoning_streamed else "content": "First"}}
                                ]
                            },
                        ),
                        (104.0, {"choices": [{"delta": {"content": "Answer"}}]}),
                        (
                            120.0,
                            {
                                "choices": [],
                                "usage": {
                                    "completion_tokens": 101,
                                    "completion_tokens_details": {"reasoning_tokens": 80},
                                },
                            },
                        ),
                        (125.0, b"data: [DONE]\n\n"),
                    ]
                )
                self.assertEqual(2000.0, result["first_token_latency_ms"])
                self.assertEqual(50.0 if reasoning_streamed else None, result["tps"])

    def test_responses_translation_uses_same_formula_and_rejects_reasoning_summaries(self) -> None:
        for reasoning in (False, True):
            with self.subTest(reasoning=reasoning):
                result = self._measure_timed_stream(
                    [
                        (
                            102.0,
                            {
                                "type": "response.reasoning_summary_text.delta"
                                if reasoning
                                else "response.output_text.delta",
                                "delta": "Hello",
                            },
                        ),
                        (104.0, {"type": "response.output_text.delta", "delta": "Answer"}),
                        (
                            120.0,
                            {
                                "type": "response.completed",
                                "response": {
                                    "id": "r1",
                                    "model": "test-model",
                                    "status": "completed",
                                    "output": [],
                                    "usage": {"input_tokens": 10, "output_tokens": 101},
                                },
                            },
                        ),
                    ],
                    "openai_responses",
                )
                self.assertEqual(2000.0, result["first_token_latency_ms"])
                self.assertEqual(None if reasoning else 50.0, result["tps"])

    def test_claude_translation_uses_same_formula_and_rejects_redacted_thinking(self) -> None:
        for redacted in (False, True):
            with self.subTest(redacted=redacted):
                result = self._measure_timed_stream(
                    [
                        (
                            101.0,
                            {
                                "type": "message_start",
                                "message": {
                                    "id": "m1",
                                    "model": "test-model",
                                    "usage": {"input_tokens": 10, "output_tokens": 0},
                                },
                            },
                        ),
                        (
                            101.5,
                            {
                                "type": "content_block_start",
                                "index": 0,
                                "content_block": {"type": "redacted_thinking" if redacted else "text"},
                            },
                        ),
                        (
                            102.0,
                            {
                                "type": "content_block_delta",
                                "index": 1,
                                "delta": {"type": "text_delta", "text": "Hello"},
                            },
                        ),
                        (
                            104.0,
                            {
                                "type": "content_block_delta",
                                "index": 1,
                                "delta": {"type": "text_delta", "text": " world"},
                            },
                        ),
                        (
                            120.0,
                            {
                                "type": "message_delta",
                                "delta": {"stop_reason": "end_turn"},
                                "usage": {"output_tokens": 101},
                            },
                        ),
                        (125.0, {"type": "message_stop"}),
                    ],
                    "claude_chat",
                )
                self.assertEqual(2000.0, result["first_token_latency_ms"])
                self.assertEqual(None if redacted else 50.0, result["tps"])

    def test_split_sse_event_is_timed_when_complete_and_usage_does_not_extend_generation(self) -> None:
        result = self._measure_timed_stream(
            [
                (101.0, b'data: {"choices": [{"delta": {"content": "He'),
                (102.0, b'llo"}}]}\n\n'),
                (104.0, {"choices": [{"delta": {"content": " world"}}]}),
                (120.0, {"choices": [], "usage": {"completion_tokens": 101}}),
                (125.0, b"data: [DONE]\n\n"),
            ]
        )
        self.assertEqual(2000.0, result["first_token_latency_ms"])
        self.assertEqual(50.0, result["tps"])

    def test_retry_attempts_write_one_request_log(self) -> None:
        responses = [
            FakeBufferedResponse(
                b'{"error":{"message":"busy"}}',
                status_code=429,
            ),
            FakeBufferedResponse(
                (
                    b'{"id":"chatcmpl_1","object":"chat.completion","model":"demo-model",'
                    b'"choices":[{"index":0,"message":{"role":"assistant","content":"Hello"},'
                    b'"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2,'
                    b'"total_tokens":5}}'
                )
            ),
        ]
        opened_count = 0

        def stub_open_upstream_response(provider, headers, body, **kwargs):
            nonlocal opened_count
            del provider, headers, body, kwargs
            response = responses[opened_count]
            opened_count += 1
            return OpenedUpstreamResponse(
                response=response,
                status_code=response.status_code,
                content_type=response.headers["Content-Type"],
                is_stream=False,
                stream_format="nonstream",
            )

        self.service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        result = self.service.test_models(
            {
                "name": "demo",
                "api": "https://example.com/v1/chat/completions",
                "source_format": "openai_chat",
                "max_retries": 2,
                "models": ["demo-model"],
            }
        )

        self.assertTrue(result["results"][0]["available"])
        self.assertEqual(2, opened_count)
        self.assertEqual(1, len(self.log_service.calls))
        self.assertEqual(5, self.log_service.calls[0]["total_tokens"])

    def test_stream_success_without_model_output_marks_unavailable(self) -> None:
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"demo-model","choices":[],"usage":{"prompt_tokens":3,"completion_tokens":0,"total_tokens":3}}\n\n',
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
                "models": ["demo-model"],
            }
        )

        self.assertFalse(result["results"][0]["available"])
        self.assertIn("no valid model output", result["results"][0]["error"])
        self.assertEqual([], self.log_service.calls)

    def test_raw_text_stream_success_marks_unavailable(self) -> None:
        fake_response = FakeStreamResponse(
            [b"<html><body>ok</body></html>"],
            content_type="text/html",
        )

        def stub_open_upstream_response(provider, headers, body, **kwargs):
            del provider, headers, body, kwargs
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/html",
                is_stream=True,
                stream_format="raw_text",
            )

        self.service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        result = self.service.test_models(
            {
                "api": "https://example.com",
                "source_format": "openai_chat",
                "models": ["demo-model"],
            }
        )

        self.assertFalse(result["results"][0]["available"])
        self.assertIn("no valid model output", result["results"][0]["error"])

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
                "models": ["demo-model"],
            }
        )

        self.assertTrue(result["results"][0]["available"])
        self.assertIsNone(result["results"][0]["first_token_latency_ms"])
        self.assertIsNone(result["results"][0]["tps"])

    def test_nonstream_success_without_model_output_marks_unavailable(self) -> None:
        fake_response = FakeBufferedResponse(b'{"status":"ok"}')

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
                "models": ["demo-model"],
            }
        )

        self.assertFalse(result["results"][0]["available"])
        self.assertIn("no valid model output", result["results"][0]["error"])

    def test_nonstream_html_success_marks_unavailable(self) -> None:
        fake_response = FakeBufferedResponse(
            b"<html><body>ok</body></html>",
            content_type="text/html",
        )

        def stub_open_upstream_response(provider, headers, body, **kwargs):
            del provider, headers, body, kwargs
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/html",
                is_stream=False,
                stream_format="nonstream",
            )

        self.service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        result = self.service.test_models(
            {
                "api": "https://example.com",
                "source_format": "openai_chat",
                "models": ["demo-model"],
            }
        )

        self.assertFalse(result["results"][0]["available"])
        self.assertIn("no valid model output", result["results"][0]["error"])

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

    def test_request_headers_replace_case_insensitive_default_headers(self) -> None:
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
                "models": ["demo-model"],
            },
            request_headers={"Content-Type": "application/custom+json"},
        )

        headers = captured.get("headers")
        assert isinstance(headers, dict)
        self.assertEqual({"Content-Type": "application/custom+json"}, headers)

    def test_build_provider_ignores_non_provider_test_fields(self) -> None:
        provider = self.service._build_provider(
            {
                "api": "https://example.com/v1/chat/completions",
                "source_format": "openai_chat",
                "api_key": "sk-legacy-demo",
                "models": ["demo-model"],
                "auth_entry_id": "entry-a",
            },
            ["demo-model"],
        )

        self.assertEqual("https://example.com/v1/chat/completions", provider.api)
        self.assertEqual(("demo-model",), provider.model_list)
        self.assertEqual("sk-legacy-demo", provider.api_key)

    def test_build_provider_uses_internal_name_for_legacy_invalid_provider_name(self) -> None:
        provider = self.service._build_provider(
            {
                "name": "legacy/provider",
                "api": "https://example.com/v1/chat/completions",
                "source_format": "openai_chat",
                "models": ["demo-model"],
            },
            ["demo-model"],
        )

        self.assertEqual("ProviderModelTest", provider.name)
        self.assertEqual(("demo-model",), provider.model_list)


if __name__ == "__main__":
    unittest.main()
