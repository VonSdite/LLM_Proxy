import json
import sys
import unittest
from pathlib import Path
from typing import Any

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.executors import OpenedUpstreamResponse
from src.external import LLMProvider
from src.hooks import BaseHook, HookAbortError
from src.proxy_core import decode_stream_events
from src.services.proxy_service import ProxyService
from src.translators import (
    ClaudeChatTranslator,
    ClaudePassthroughTranslator,
    ComposedTranslator,
    OpenAIChatClaudeTranslator,
    OpenAIChatResponsesTranslator,
    OpenAIChatTranslator,
    OpenAIResponsesPassthroughTranslator,
    OpenAIResponsesTranslator,
    build_default_translator_registry,
)


class FakeLogger:
    def info(self, msg: str, *args) -> None:
        del msg, args

    def warning(self, msg: str, *args) -> None:
        del msg, args

    def error(self, msg: str, *args) -> None:
        del msg, args

    def debug(self, msg: str, *args) -> None:
        del msg, args


class FakeConfigManager:
    def __init__(self, *, llm_request_debug_enabled: bool = False) -> None:
        self._llm_request_debug_enabled = llm_request_debug_enabled

    def is_llm_request_debug_enabled(self) -> bool:
        return self._llm_request_debug_enabled


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


class AbortOnResponseHook(BaseHook):
    def __init__(self, *, message: str, status_code: int, error_type: str) -> None:
        self._message = message
        self._status_code = status_code
        self._error_type = error_type

    def response_guard(self, ctx: Any, body: Any) -> Any:
        del ctx, body
        raise HookAbortError(
            self._message,
            status_code=self._status_code,
            error_type=self._error_type,
        )


class StreamDecoderTests(unittest.TestCase):
    def test_sse_json_decoder_handles_split_utf8_and_done(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"content":"\xe4',
            b'\xbd\xa0\xe5\xa5\xbd"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]

        events = list(decode_stream_events(chunks, "sse_json"))

        self.assertEqual(["json", "done"], [event.kind for event in events])
        self.assertEqual("你好", events[0].payload["choices"][0]["delta"]["content"])

    def test_ndjson_decoder_handles_split_lines(self) -> None:
        chunks = [
            b'{"id":1}\n{"id"',
            b':2}\n[D',
            b'ONE]\n',
        ]

        events = list(decode_stream_events(chunks, "ndjson"))

        self.assertEqual(["json", "json", "done"], [event.kind for event in events])
        self.assertEqual(1, events[0].payload["id"])
        self.assertEqual(2, events[1].payload["id"])


class TranslatorTests(unittest.TestCase):
    def test_openai_responses_translator_maps_chat_request(self) -> None:
        translator = OpenAIResponsesTranslator()

        translated = translator.translate_request(
            "gpt-4.1",
            {
                "messages": [
                    {"role": "system", "content": "Be brief"},
                    {"role": "user", "content": "Hello"},
                ],
                "max_tokens": 128,
            },
            True,
        )

        self.assertEqual("gpt-4.1", translated["model"])
        self.assertTrue(translated["stream"])
        self.assertEqual("Be brief", translated["instructions"])
        self.assertEqual("message", translated["input"][0]["type"])
        self.assertEqual("user", translated["input"][0]["role"])
        self.assertEqual("Hello", translated["input"][0]["content"][0]["text"])
        self.assertEqual(128, translated["max_output_tokens"])

    def test_claude_chat_translator_maps_chat_request(self) -> None:
        translator = ClaudeChatTranslator()

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "messages": [
                    {"role": "system", "content": "Be careful"},
                    {"role": "user", "content": "Hello"},
                ]
            },
            True,
        )

        self.assertEqual("claude-sonnet-4-5", translated["model"])
        self.assertEqual("Be careful", translated["system"])
        self.assertEqual("user", translated["messages"][0]["role"])
        self.assertEqual("Hello", translated["messages"][0]["content"][0]["text"])
        self.assertTrue(translated["stream"])

    def test_openai_chat_responses_translator_maps_nonstream_payload(self) -> None:
        translator = OpenAIChatResponsesTranslator()

        translated = translator.translate_nonstream_response(
            "gpt-4.1",
            {"instructions": "Be brief"},
            {"model": "gpt-4.1"},
            {
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "created": 123,
                "model": "gpt-4.1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello from chat"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

        self.assertEqual("response", translated["object"])
        self.assertEqual("completed", translated["status"])
        self.assertEqual("Hello from chat", translated["output"][0]["content"][0]["text"])
        self.assertEqual({"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}, translated["usage"])
        self.assertEqual("Be brief", translated["instructions"])

    def test_openai_responses_passthrough_translator_normalizes_response_done_event(self) -> None:
        translator = OpenAIResponsesPassthroughTranslator()

        translated_chunks = translator.translate_stream_event(
            "gpt-5-codex",
            {"model": "gpt-5-codex"},
            {"model": "gpt-5-codex"},
            type(
                "FakeEvent",
                (),
                {
                    "kind": "json",
                    "payload": {
                        "type": "response.done",
                        "response": {"id": "resp_1", "model": "gpt-5-codex"},
                    },
                    "raw": '{"type":"response.done"}',
                    "event": "response.done",
                },
            )(),
            {},
        )

        self.assertEqual(1, len(translated_chunks))
        self.assertEqual("response.completed", translated_chunks[0].event)
        self.assertEqual("response.completed", translated_chunks[0].payload["type"])


class TranslatorRegistryTests(unittest.TestCase):
    def test_default_registry_contains_only_clean_pairs(self) -> None:
        registry = build_default_translator_registry()

        expected_pairs = {
            ("openai_chat", "openai_chat"): OpenAIChatTranslator,
            ("openai_chat", "openai_responses"): OpenAIChatResponsesTranslator,
            ("openai_chat", "claude_chat"): OpenAIChatClaudeTranslator,
            ("openai_responses", "openai_chat"): OpenAIResponsesTranslator,
            ("openai_responses", "openai_responses"): OpenAIResponsesPassthroughTranslator,
            ("openai_responses", "claude_chat"): ComposedTranslator,
            ("claude_chat", "openai_chat"): ClaudeChatTranslator,
            ("claude_chat", "openai_responses"): ComposedTranslator,
            ("claude_chat", "claude_chat"): ClaudePassthroughTranslator,
        }

        for pair, expected_type in expected_pairs.items():
            self.assertIsInstance(registry.get(*pair), expected_type)

        total_pairs = sum(len(targets) for targets in registry._translators.values())  # type: ignore[attr-defined]
        self.assertEqual(9, total_pairs)

    def test_default_registry_rejects_removed_gemini_pairs(self) -> None:
        registry = build_default_translator_registry()

        with self.assertRaisesRegex(ValueError, "Unsupported translator pair: gemini_chat -> openai_chat"):
            registry.get("gemini_chat", "openai_chat")

    def test_default_registry_rejects_removed_codex_pairs(self) -> None:
        registry = build_default_translator_registry()

        with self.assertRaisesRegex(ValueError, "Unsupported translator pair: codex -> openai_responses"):
            registry.get("codex", "openai_responses")


class ProxyServicePipelineTests(unittest.TestCase):
    @staticmethod
    def _collect_response_body(response) -> bytes:
        assert response is not None
        chunks = response.response
        assert chunks is not None
        return b"".join(
            chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            for chunk in chunks
        )

    def _build_service(self, *, llm_request_debug_enabled: bool = False):
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(llm_request_debug_enabled=llm_request_debug_enabled),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        return app, ProxyService(ctx)

    def test_proxy_service_translates_openai_responses_stream_to_openai_chat(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="responses-upstream",
            api="https://example.com/v1/responses",
            transport="http",
            source_format="openai_responses",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        captured = {}
        fake_response = FakeStreamResponse(
            [
                b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","created_at":123,"model":"gpt-4.1"}}\n\n',
                b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","item_id":"msg_resp_1_0","output_index":0,"delta":"Hello from Responses"}\n\n',
                b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1","created_at":123,"model":"gpt-4.1","usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, args, kwargs
            captured["body"] = body
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        with app.test_request_context("/v1/chat/completions"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "responses-upstream/gpt-4.1",
                    "messages": [
                        {"role": "system", "content": "Be brief"},
                        {"role": "user", "content": "Hello"},
                    ],
                    "stream": True,
                },
                {},
                forward_stream_usage=True,
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("Be brief", captured["body"]["instructions"])
        self.assertEqual("Hello", captured["body"]["input"][0]["content"][0]["text"])
        self.assertIn(b"Hello from Responses", stream_body)
        self.assertIn(b'"prompt_tokens": 3', stream_body)
        self.assertIn(b'"completion_tokens": 2', stream_body)
        self.assertIn(b'"total_tokens": 5', stream_body)
        self.assertIn(b"data: [DONE]", stream_body)
        self.assertEqual(1, stream_body.count(b"data: [DONE]\n\n"))
        self.assertTrue(fake_response.closed)

    def test_proxy_service_collects_usage_when_openai_chat_usage_chunk_is_suppressed(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="responses-upstream",
            api="https://example.com/v1/responses",
            transport="http",
            source_format="openai_responses",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        captured_meta = {}
        fake_response = FakeStreamResponse(
            [
                b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","created_at":123,"model":"gpt-4.1"}}\n\n',
                b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","item_id":"msg_resp_1_0","output_index":0,"delta":"Hello from Responses"}\n\n',
                b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1","created_at":123,"model":"gpt-4.1","usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, body, args, kwargs
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        with app.test_request_context("/v1/chat/completions"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "responses-upstream/gpt-4.1",
                    "messages": [
                        {"role": "user", "content": "Hello"},
                    ],
                    "stream": True,
                },
                {},
                on_complete=captured_meta.update,
                forward_stream_usage=False,
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIn(b"Hello from Responses", stream_body)
        self.assertNotIn(b'"usage"', stream_body)
        self.assertEqual("gpt-4.1", captured_meta["response_model"])
        self.assertEqual(3, captured_meta["prompt_tokens"])
        self.assertEqual(2, captured_meta["completion_tokens"])
        self.assertEqual(5, captured_meta["total_tokens"])
        self.assertTrue(fake_response.closed)

    def test_proxy_service_skips_trace_buffering_when_debug_logging_disabled(self) -> None:
        app, service = self._build_service(llm_request_debug_enabled=False)
        provider = LLMProvider(
            name="responses-upstream",
            api="https://example.com/v1/responses",
            transport="http",
            source_format="openai_responses",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        fake_response = FakeStreamResponse(
            [
                b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","created_at":123,"model":"gpt-4.1"}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, body, args, kwargs
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        coerce_calls: list[bytes] = []

        def record_trace_bytes(payload):
            coerce_calls.append(payload if isinstance(payload, bytes) else str(payload).encode("utf-8"))
            return payload if isinstance(payload, bytes) else str(payload).encode("utf-8")

        original_coerce_trace_bytes = ProxyService._coerce_trace_bytes
        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]
        ProxyService._coerce_trace_bytes = staticmethod(record_trace_bytes)  # type: ignore[assignment]
        try:
            with app.test_request_context("/v1/chat/completions"):
                response, status_code, failure_info = service.proxy_request(
                    provider,
                    {
                        "model": "responses-upstream/gpt-4.1",
                        "messages": [{"role": "user", "content": "Hello"}],
                        "stream": True,
                    },
                    {},
                    trace_id="trace-disabled",
                )
                stream_body = self._collect_response_body(response)
        finally:
            ProxyService._coerce_trace_bytes = original_coerce_trace_bytes  # type: ignore[assignment]

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual([], coerce_calls)
        self.assertIn(b"data: [DONE]", stream_body)

    def test_stream_hook_abort_emits_openai_chat_error_chunk_and_done(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="responses-upstream",
            api="https://example.com/v1/responses",
            transport="http",
            source_format="openai_responses",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
            hook=AbortOnResponseHook(
                message="blocked by response guard",
                status_code=451,
                error_type="hook_blocked",
            ),
        )
        captured_meta: dict[str, object] = {}
        fake_response = FakeStreamResponse(
            [
                b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","created_at":123,"model":"gpt-4.1"}}\n\n',
                b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","item_id":"msg_resp_1_0","output_index":0,"delta":"Hello"}\n\n',
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, body, args, kwargs
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        with app.test_request_context("/v1/chat/completions"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "responses-upstream/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {},
                on_complete=captured_meta.update,
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIn(b'"message": "blocked by response guard"', stream_body)
        self.assertIn(b'"type": "hook_blocked"', stream_body)
        self.assertIn(b"data: [DONE]", stream_body)
        self.assertEqual("gpt-4.1", captured_meta["response_model"])
        self.assertTrue(fake_response.closed)

    def test_stream_hook_abort_emits_responses_failed_event(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_formats=("openai_responses",),
            model_list=("gpt-4.1",),
            max_retries=1,
            hook=AbortOnResponseHook(
                message="blocked by response guard",
                status_code=451,
                error_type="hook_blocked",
            ),
        )
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","created":123,"model":"gpt-4.1","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n',
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, body, args, kwargs
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        with app.test_request_context("/v1/responses"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "chat-upstream/gpt-4.1",
                    "instructions": "Be brief",
                    "input": "Hello",
                    "stream": True,
                },
                {},
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIn(b"event: response.failed", stream_body)
        self.assertIn(b'"status": "failed"', stream_body)
        self.assertIn(b'"message": "blocked by response guard"', stream_body)
        self.assertNotIn(b"[DONE]", stream_body)
        self.assertTrue(fake_response.closed)

    def test_proxy_service_translates_openai_chat_stream_to_openai_responses(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_formats=("openai_responses",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        captured = {}
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","created":123,"model":"gpt-4.1","choices":[{"index":0,"delta":{"content":"Hi from chat"},"finish_reason":null}]}\n\n',
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","created":123,"model":"gpt-4.1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, args, kwargs
            captured["body"] = body
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        with app.test_request_context("/v1/responses"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "chat-upstream/gpt-4.1",
                    "instructions": "Be brief",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Hello"}],
                        }
                    ],
                    "stream": True,
                },
                {},
                forward_stream_usage=True,
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("system", captured["body"]["messages"][0]["role"])
        self.assertEqual("Be brief", captured["body"]["messages"][0]["content"])
        self.assertEqual("Hello", captured["body"]["messages"][1]["content"][0]["text"])
        self.assertTrue(captured["body"]["stream_options"]["include_usage"])
        self.assertIn(b"event: response.created", stream_body)
        self.assertIn(b"event: response.output_text.delta", stream_body)
        self.assertIn(b"event: response.completed", stream_body)
        self.assertIn(b'"delta": "Hi from chat"', stream_body)
        self.assertNotIn(b"[DONE]", stream_body)
        self.assertTrue(fake_response.closed)

    def test_proxy_service_translates_openai_chat_stream_to_claude_messages(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_formats=("claude_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        captured = {}
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","created":123,"model":"gpt-4.1","choices":[{"index":0,"delta":{"content":"Hi from chat"},"finish_reason":null}]}\n\n',
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","created":123,"model":"gpt-4.1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, args, kwargs
            captured["body"] = body
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        with app.test_request_context("/v1/messages"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "chat-upstream/gpt-4.1",
                    "max_tokens": 256,
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                    ],
                    "stream": True,
                },
                {},
                forward_stream_usage=True,
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("user", captured["body"]["messages"][0]["role"])
        self.assertEqual("Hello", captured["body"]["messages"][0]["content"])
        self.assertTrue(captured["body"]["stream_options"]["include_usage"])
        self.assertIn(b"event: message_start", stream_body)
        self.assertIn(b"event: content_block_start", stream_body)
        self.assertIn(b"event: content_block_delta", stream_body)
        self.assertIn(b"event: message_delta", stream_body)
        self.assertIn(b"event: message_stop", stream_body)
        self.assertNotIn(b"[DONE]", stream_body)
        self.assertTrue(fake_response.closed)

    def test_proxy_service_translates_openai_responses_stream_to_claude_messages_without_upstream_done_and_preserves_response_model(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="responses-upstream",
            api="https://example.com/v1/responses",
            transport="http",
            source_format="openai_responses",
            target_formats=("claude_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        captured = {}
        captured_meta = {}
        fake_response = FakeStreamResponse(
            [
                b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","created_at":123,"model":"gpt-4.1"}}\n\n',
                b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","item_id":"msg_resp_1_0","output_index":0,"delta":"Hi from responses"}\n\n',
                b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1","created_at":123,"model":"gpt-5.4","usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}\n\n',
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, args, kwargs
            captured["body"] = body
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        with app.test_request_context("/v1/messages"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "responses-upstream/gpt-4.1",
                    "max_tokens": 256,
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                    ],
                    "stream": True,
                },
                {},
                forward_stream_usage=True,
                on_complete=captured_meta.update,
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("user", captured["body"]["messages"][0]["role"])
        self.assertEqual("Hello", captured["body"]["messages"][0]["content"])
        self.assertIn(b"event: message_start", stream_body)
        self.assertIn(b"event: content_block_start", stream_body)
        self.assertIn(b"event: content_block_delta", stream_body)
        self.assertIn(b"event: message_delta", stream_body)
        self.assertIn(b"event: message_stop", stream_body)
        self.assertIn(b'"model": "gpt-4.1"', stream_body)
        self.assertNotIn(b"[DONE]", stream_body)
        self.assertEqual("gpt-5.4", captured_meta["response_model"])
        self.assertEqual(3, captured_meta["prompt_tokens"])
        self.assertEqual(2, captured_meta["completion_tokens"])
        self.assertEqual(5, captured_meta["total_tokens"])
        self.assertTrue(fake_response.closed)


    def test_proxy_service_normalizes_response_done_to_response_completed(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="responses-upstream",
            api="https://example.com/v1/responses",
            transport="http",
            source_format="openai_responses",
            target_formats=("openai_responses",),
            model_list=("gpt-5-codex",),
            max_retries=1,
        )
        captured = {}
        fake_response = FakeStreamResponse(
            [
                b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","created_at":123,"model":"gpt-5-codex"}}\n\n',
                b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","item_id":"msg_resp_1_0","output_index":0,"delta":"Hi from Codex"}\n\n',
                b'event: response.done\ndata: {"type":"response.done","response":{"id":"resp_1","created_at":123,"model":"gpt-5-codex","usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, args, kwargs
            captured["body"] = body
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        with app.test_request_context("/v1/responses"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "responses-upstream/gpt-5-codex",
                    "input": [
                        {
                            "type": "message",
                            "role": "system",
                            "content": [{"type": "input_text", "text": "Be brief"}],
                        },
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Hello"}],
                        },
                    ],
                    "stream": True,
                },
                {},
                forward_stream_usage=True,
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("system", captured["body"]["input"][0]["role"])
        self.assertEqual("user", captured["body"]["input"][1]["role"])
        self.assertNotIn("include", captured["body"])
        self.assertIn(b"event: response.created", stream_body)
        self.assertIn(b"event: response.output_text.delta", stream_body)
        self.assertIn(b"event: response.completed", stream_body)
        self.assertNotIn(b"event: response.done", stream_body)
        self.assertIn(b'"delta": "Hi from Codex"', stream_body)
        self.assertNotIn(b"[DONE]", stream_body)
        self.assertTrue(fake_response.closed)


if __name__ == "__main__":
    unittest.main()
