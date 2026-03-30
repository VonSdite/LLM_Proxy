import json
import sys
import unittest
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.executors import OpenedUpstreamResponse
from src.external import LLMProvider
from src.proxy_core import decode_stream_events
from src.services.proxy_service import ProxyService
from src.translators import (
    ClaudeChatTranslator,
    ClaudePassthroughTranslator,
    CodexChatTranslator,
    CodexPassthroughTranslator,
    ComposedTranslator,
    OpenAIChatClaudeTranslator,
    OpenAIChatCodexTranslator,
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
    pass


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

    def test_codex_passthrough_translator_normalizes_request(self) -> None:
        translator = CodexPassthroughTranslator()

        translated = translator.translate_request(
            "gpt-5-codex",
            {
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
                ]
            },
            True,
        )

        self.assertEqual("gpt-5-codex", translated["model"])
        self.assertEqual("developer", translated["input"][0]["role"])
        self.assertEqual(["reasoning.encrypted_content"], translated["include"])
        self.assertFalse(translated["store"])
        self.assertTrue(translated["parallel_tool_calls"])


class TranslatorRegistryTests(unittest.TestCase):
    def test_default_registry_contains_only_clean_pairs(self) -> None:
        registry = build_default_translator_registry()

        expected_pairs = {
            ("openai_chat", "openai_chat"): OpenAIChatTranslator,
            ("openai_chat", "openai_responses"): OpenAIChatResponsesTranslator,
            ("openai_chat", "claude_chat"): OpenAIChatClaudeTranslator,
            ("openai_chat", "codex"): OpenAIChatCodexTranslator,
            ("openai_responses", "openai_chat"): OpenAIResponsesTranslator,
            ("openai_responses", "openai_responses"): OpenAIResponsesPassthroughTranslator,
            ("openai_responses", "claude_chat"): ComposedTranslator,
            ("openai_responses", "codex"): ComposedTranslator,
            ("claude_chat", "openai_chat"): ClaudeChatTranslator,
            ("claude_chat", "openai_responses"): ComposedTranslator,
            ("claude_chat", "claude_chat"): ClaudePassthroughTranslator,
            ("claude_chat", "codex"): ComposedTranslator,
            ("codex", "openai_chat"): CodexChatTranslator,
            ("codex", "openai_responses"): ComposedTranslator,
            ("codex", "claude_chat"): ComposedTranslator,
            ("codex", "codex"): CodexPassthroughTranslator,
        }

        for pair, expected_type in expected_pairs.items():
            self.assertIsInstance(registry.get(*pair), expected_type)

        total_pairs = sum(len(targets) for targets in registry._translators.values())  # type: ignore[attr-defined]
        self.assertEqual(16, total_pairs)

    def test_default_registry_rejects_removed_gemini_pairs(self) -> None:
        registry = build_default_translator_registry()

        with self.assertRaisesRegex(ValueError, "Unsupported translator pair: gemini_chat -> openai_chat"):
            registry.get("gemini_chat", "openai_chat")


class ProxyServicePipelineTests(unittest.TestCase):
    def _build_service(self):
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(),
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
            target_format="openai_chat",
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
            stream_body = b"".join(response.response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("Be brief", captured["body"]["instructions"])
        self.assertEqual("Hello", captured["body"]["input"][0]["content"][0]["text"])
        self.assertIn(b"Hello from Responses", stream_body)
        self.assertIn(b'"prompt_tokens": 3', stream_body)
        self.assertIn(b'"completion_tokens": 2', stream_body)
        self.assertIn(b'"total_tokens": 5', stream_body)
        self.assertIn(b"data: [DONE]", stream_body)
        self.assertTrue(fake_response.closed)

    def test_proxy_service_translates_openai_chat_stream_to_openai_responses(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_format="openai_responses",
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
            stream_body = b"".join(response.response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("system", captured["body"]["messages"][0]["role"])
        self.assertEqual("Be brief", captured["body"]["messages"][0]["content"])
        self.assertEqual("Hello", captured["body"]["messages"][1]["content"][0]["text"])
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
            target_format="claude_chat",
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
            stream_body = b"".join(response.response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("user", captured["body"]["messages"][0]["role"])
        self.assertEqual("Hello", captured["body"]["messages"][0]["content"])
        self.assertIn(b"event: message_start", stream_body)
        self.assertIn(b"event: content_block_start", stream_body)
        self.assertIn(b"event: content_block_delta", stream_body)
        self.assertIn(b"event: message_delta", stream_body)
        self.assertIn(b"event: message_stop", stream_body)
        self.assertNotIn(b"[DONE]", stream_body)
        self.assertTrue(fake_response.closed)

    def test_proxy_service_translates_codex_stream_to_openai_responses(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="codex-upstream",
            api="https://example.com/v1/responses",
            transport="http",
            source_format="codex",
            target_format="openai_responses",
            model_list=("gpt-5-codex",),
            max_retries=1,
        )
        captured = {}
        fake_response = FakeStreamResponse(
            [
                b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","created_at":123,"model":"gpt-5-codex"}}\n\n',
                b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","item_id":"msg_resp_1_0","output_index":0,"delta":"Hi from Codex"}\n\n',
                b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1","created_at":123,"model":"gpt-5-codex","usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}\n\n',
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
                    "model": "codex-upstream/gpt-5-codex",
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
            stream_body = b"".join(response.response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("Be brief", captured["body"]["instructions"])
        self.assertEqual("user", captured["body"]["input"][0]["role"])
        self.assertEqual(["reasoning.encrypted_content"], captured["body"]["include"])
        self.assertIn(b"event: response.created", stream_body)
        self.assertIn(b"event: response.output_text.delta", stream_body)
        self.assertIn(b"event: response.completed", stream_body)
        self.assertIn(b'"delta": "Hi from Codex"', stream_body)
        self.assertNotIn(b"[DONE]", stream_body)
        self.assertTrue(fake_response.closed)


if __name__ == "__main__":
    unittest.main()
