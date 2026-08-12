import base64
import json
import sys
import unittest
from pathlib import Path
from typing import Any

import requests
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext
from src.config.auth_group_manager import SelectedAuthEntry
from src.executors import OpenedUpstreamResponse
from src.external import LLMProvider
from src.hooks import BaseHook, HookAbortError
from src.proxy_core import StreamEvent, decode_stream_events
from src.services.proxy_service import ProxyService
from src.services.upstream_request_builder import build_upstream_request
from src.translators import (
    ClaudeChatTranslator,
    ClaudeOpenAIResponsesTranslator,
    ClaudePassthroughTranslator,
    OpenAIChatClaudeTranslator,
    OpenAIChatResponsesTranslator,
    OpenAIChatTranslator,
    OpenAIResponsesClaudeTranslator,
    OpenAIResponsesPassthroughTranslator,
    OpenAIResponsesTranslator,
    build_default_translator_registry,
)
from src.translators.stream_aggregator import (
    StreamAggregationError,
    aggregate_stream_to_native_response,
    aggregate_stream_to_openai_chat,
    infer_stream_aggregation_status_code,
)


def _valid_claude_cais_signature() -> str:
    """构造满足 Claude CAIS 结构校验的最小签名。"""
    channel = b"\x08\x10\x2a\x01\x00\x32\x08claude-x"
    container = b"\x0a" + bytes([len(channel)]) + channel
    payload = b"\x08\x02\x12" + bytes([len(container)]) + container
    return base64.b64encode(payload).decode("ascii")


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


class FailingStreamResponse(FakeStreamResponse):
    def __init__(
        self,
        chunks,
        *,
        fail_after_chunks: int,
        error: Exception | None = None,
        content_type: str = "text/event-stream",
        status_code: int = 200,
    ) -> None:
        super().__init__(chunks, content_type=content_type, status_code=status_code)
        self._fail_after_chunks = fail_after_chunks
        self._error = error or requests.exceptions.ChunkedEncodingError("simulated incomplete chunk")

    def iter_content(self, chunk_size=None):
        del chunk_size
        for index, chunk in enumerate(self._chunks):
            if index == self._fail_after_chunks:
                raise self._error
            yield chunk
        if self._fail_after_chunks >= len(self._chunks):
            raise self._error


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


class UnserializableTerminalHook(BaseHook):
    def response_guard(self, ctx: Any, body: Any) -> Any:
        del ctx
        if isinstance(body, dict) and body.get("type") == "response.completed":
            changed = dict(body)
            changed["invalid"] = {"not-json-serializable"}
            return changed
        return body


class RewriteRequestModelHook(BaseHook):
    def __init__(self, target_model: str) -> None:
        self._target_model = target_model

    def request_guard(self, ctx: Any, body: dict[str, Any]) -> dict[str, Any]:
        del ctx
        rewritten_body = dict(body)
        rewritten_body["model"] = self._target_model
        return rewritten_body


class RequestBodyRecordingHook(BaseHook):
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def request_guard(self, ctx: Any, body: dict[str, Any]) -> dict[str, Any]:
        del ctx
        self.bodies.append(dict(body))
        return body


class RewriteRequestStreamHook(BaseHook):
    def __init__(self, stream: bool) -> None:
        self._stream = stream

    def request_guard(self, ctx: Any, body: dict[str, Any]) -> dict[str, Any]:
        del ctx
        rewritten_body = dict(body)
        rewritten_body["stream"] = self._stream
        return rewritten_body


class HeaderRecordingHook(BaseHook):
    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []

    def header_hook(self, ctx: Any, headers: dict[str, str]) -> dict[str, str]:
        del ctx
        self.headers.append(dict(headers))
        headers["x-hook-stage"] = "after-auth"
        return headers


class RotatingAuthGroupManager:
    def __init__(self) -> None:
        self._selections = [
            SelectedAuthEntry("pool", "key-a", (("Authorization", "Bearer key-a"),)),
            SelectedAuthEntry("pool", "key-b", (("Authorization", "Bearer key-b"),)),
        ]
        self.acquire_calls: list[str | None] = []
        self.finish_calls: list[tuple[str | None, dict[str, Any]]] = []

    def acquire(self, auth_group_name: str | None) -> SelectedAuthEntry:
        self.acquire_calls.append(auth_group_name)
        return self._selections[len(self.acquire_calls) - 1]

    def mark_request_dispatched(self, selection: SelectedAuthEntry | None) -> None:
        del selection

    def finish(self, selection: SelectedAuthEntry | None, **kwargs: Any) -> None:
        self.finish_calls.append((selection.entry_id if selection is not None else None, dict(kwargs)))


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

    def test_sse_json_decoder_rejects_invalid_utf8_bytes(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"content":"',
            b"\x85",
            b'ok"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]

        with self.assertRaises(UnicodeDecodeError):
            list(decode_stream_events(chunks, "sse_json"))

    def test_sse_json_decoder_ignores_comments_and_metadata_only_events(self) -> None:
        events = list(
            decode_stream_events(
                [
                    b": keep-alive\n\n",
                    b"event: ping\nid: 1\nretry: 1000\n\n",
                    b'data: {"id":1}\n\n',
                    b"data: [DONE]\n\n",
                ],
                "sse_json",
            )
        )

        self.assertEqual(["json", "done"], [event.kind for event in events])
        self.assertEqual(1, events[0].payload["id"])

    def test_ndjson_decoder_handles_split_lines(self) -> None:
        chunks = [
            b'{"id":1}\n{"id"',
            b":2}\n[D",
            b"ONE]\n",
        ]

        events = list(decode_stream_events(chunks, "ndjson"))

        self.assertEqual(["json", "json", "done"], [event.kind for event in events])
        self.assertEqual(1, events[0].payload["id"])
        self.assertEqual(2, events[1].payload["id"])


class TranslatorTests(unittest.TestCase):
    def test_stream_aggregation_error_status_inference_covers_auth_and_quota_errors(self) -> None:
        cases = [
            ("rate_limit_error", "rate_limit_exceeded", 429),
            ("invalid_request_error", "insufficient_quota", 429),
            ("authentication_error", None, 401),
            ("invalid_request_error", "invalid_api_key", 401),
            ("invalidRequestError", "refreshTokenReused", 401),
            ("permission_error", None, 403),
            ("authorization_error", None, 403),
            ("upstream_stream_incomplete", None, 502),
        ]

        for error_type, error_code, expected_status_code in cases:
            with self.subTest(error_type=error_type, error_code=error_code):
                exc = StreamAggregationError(
                    {
                        "error": {
                            "message": "failed",
                            "type": error_type,
                            "code": error_code,
                        }
                    }
                )
                self.assertEqual(expected_status_code, infer_stream_aggregation_status_code(exc))

    def test_openai_chat_native_aggregation_rejects_empty_and_incomplete_streams(self) -> None:
        cases = {
            "empty": [],
            "partial": [
                StreamEvent(
                    kind="json",
                    payload={
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "partial"},
                                "finish_reason": None,
                            }
                        ]
                    },
                )
            ],
        }

        for name, events in cases.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(StreamAggregationError, "incomplete"):
                    aggregate_stream_to_native_response(
                        source_format="openai_chat",
                        model_name="gpt-4.1",
                        events=events,
                    )

    def test_openai_chat_native_aggregation_accepts_clean_eof_after_finish_reason(self) -> None:
        payload = aggregate_stream_to_native_response(
            source_format="openai_chat",
            model_name="gpt-4.1",
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "complete"},
                                "finish_reason": "stop",
                            }
                        ]
                    },
                )
            ],
        )

        self.assertEqual("complete", payload["choices"][0]["message"]["content"])
        self.assertEqual("stop", payload["choices"][0]["finish_reason"])

    def test_native_json_aggregators_reject_non_json_events(self) -> None:
        for source_format in ("openai_chat", "claude_chat"):
            with self.subTest(source_format=source_format):
                with self.assertRaisesRegex(StreamAggregationError, "non-JSON"):
                    aggregate_stream_to_native_response(
                        source_format=source_format,
                        model_name="model",
                        events=[StreamEvent(kind="text", payload='{"truncated":')],
                    )

    def test_native_aggregators_stop_consuming_after_terminal_event(self) -> None:
        cases = {
            "openai_chat": [
                StreamEvent(
                    kind="json",
                    payload={
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "done"},
                                "finish_reason": "stop",
                            }
                        ]
                    },
                ),
                StreamEvent(kind="done", payload="[DONE]"),
            ],
            "openai_responses": [
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.completed",
                        "response": {
                            "id": "resp-done",
                            "object": "response",
                            "status": "completed",
                            "output": [],
                        },
                    },
                )
            ],
            "claude_chat": [
                StreamEvent(
                    kind="json",
                    payload={"type": "message_start", "message": {"content": []}},
                ),
                StreamEvent(kind="json", payload={"type": "message_stop"}),
            ],
        }

        for source_format, terminal_events in cases.items():
            with self.subTest(source_format=source_format):

                def events():
                    yield from terminal_events
                    raise AssertionError("aggregator consumed events after protocol completion")

                payload = aggregate_stream_to_native_response(
                    source_format=source_format,
                    model_name="model",
                    events=events(),
                )
                self.assertIsInstance(payload, dict)

    def test_openai_chat_native_aggregation_preserves_extended_fields(self) -> None:
        payload = aggregate_stream_to_native_response(
            source_format="openai_chat",
            model_name="gpt-4.1",
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "id": "chatcmpl-fields",
                        "created": 123,
                        "model": "gpt-4.1",
                        "system_fingerprint": "fp_test",
                        "service_tier": "priority",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "content": "Hello",
                                    "audio": {"id": "audio-1", "data": "YWJj", "transcript": "Hel"},
                                },
                                "logprobs": {
                                    "content": [{"token": "Hello", "logprob": -0.1}],
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"audio": {"data": "ZA==", "transcript": "lo"}},
                                "logprobs": {
                                    "content": [{"token": "!", "logprob": -0.2}],
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    },
                ),
                StreamEvent(kind="done", payload="[DONE]"),
            ],
        )

        choice = payload["choices"][0]
        self.assertEqual("fp_test", payload["system_fingerprint"])
        self.assertEqual("priority", payload["service_tier"])
        self.assertEqual(["Hello", "!"], [item["token"] for item in choice["logprobs"]["content"]])
        self.assertEqual("YWJjZA==", choice["message"]["audio"]["data"])
        self.assertEqual("Hello", choice["message"]["audio"]["transcript"])

    def test_stream_aggregator_does_not_duplicate_repeated_tool_name(self) -> None:
        translator = OpenAIChatTranslator()

        payload = aggregate_stream_to_openai_chat(
            translator=translator,
            model_name="gpt-4.1",
            original_request={"messages": [], "stream": True},
            translated_request={"model": "gpt-4.1", "stream": True},
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "id": "chatcmpl-tool",
                        "created": 123,
                        "model": "gpt-4.1",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call-1",
                                            "type": "function",
                                            "function": {"name": "lookup", "arguments": "{"},
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "id": "chatcmpl-tool",
                        "created": 123,
                        "model": "gpt-4.1",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"name": "lookup", "arguments": '"q":"x"}'},
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                    },
                ),
                StreamEvent(kind="done", payload="[DONE]"),
            ],
        )

        tool_call = payload["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual("lookup", tool_call["function"]["name"])
        self.assertEqual('{"q":"x"}', tool_call["function"]["arguments"])

    def test_openai_responses_done_events_preserve_text_and_tool_arguments(self) -> None:
        translator = OpenAIResponsesTranslator()

        native_payload = aggregate_stream_to_native_response(
            source_format="openai_responses",
            model_name="gpt-5-codex",
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.created",
                        "response": {"id": "resp-done", "created_at": 123, "model": "gpt-5-codex"},
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.output_text.done",
                        "item_id": "msg-1",
                        "output_index": 0,
                        "text": "complete text",
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.output_item.added",
                        "output_index": 1,
                        "item": {
                            "id": "fc-1",
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "lookup",
                        },
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.output_item.done",
                        "output_index": 1,
                        "item": {
                            "id": "fc-1",
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "lookup",
                            "arguments": '{"q":"x"}',
                        },
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.completed",
                        "response": {
                            "id": "resp-done",
                            "created_at": 123,
                            "model": "gpt-5-codex",
                        },
                    },
                ),
                StreamEvent(kind="done", payload="[DONE]"),
            ],
        )
        payload = translator.translate_nonstream_response(
            "gpt-5-codex",
            {"messages": []},
            {"model": "gpt-5-codex"},
            native_payload,
        )

        self.assertEqual("complete text", payload["choices"][0]["message"]["content"])
        tool_call = payload["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual("lookup", tool_call["function"]["name"])
        self.assertEqual('{"q":"x"}', tool_call["function"]["arguments"])

    def test_openai_responses_full_completion_fills_partial_text(self) -> None:
        translator = OpenAIResponsesTranslator()

        native_payload = aggregate_stream_to_native_response(
            source_format="openai_responses",
            model_name="gpt-5-codex",
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.created",
                        "response": {"id": "resp-partial", "created_at": 123, "model": "gpt-5-codex"},
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.output_text.delta",
                        "item_id": "msg-1",
                        "output_index": 0,
                        "delta": "part",
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.completed",
                        "response": {
                            "id": "resp-partial",
                            "created_at": 123,
                            "model": "gpt-5-codex",
                            "output": [
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "partial completion"}],
                                }
                            ],
                        },
                    },
                ),
            ],
        )
        payload = translator.translate_nonstream_response(
            "gpt-5-codex",
            {"messages": []},
            {"model": "gpt-5-codex"},
            native_payload,
        )

        self.assertEqual("partial completion", payload["choices"][0]["message"]["content"])

    def test_claude_stream_preserves_initial_usage_and_tool_input(self) -> None:
        translator = ClaudeChatTranslator()

        payload = aggregate_stream_to_openai_chat(
            translator=translator,
            model_name="claude-sonnet-4-5",
            original_request={"messages": [], "stream": True},
            translated_request={"model": "claude-sonnet-4-5", "stream": True},
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "message_start",
                        "message": {
                            "id": "msg-tool",
                            "model": "claude-sonnet-4-5",
                            "role": "assistant",
                            "content": [],
                            "usage": {"input_tokens": 3},
                        },
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu-1",
                            "name": "lookup",
                            "input": {"q": "x"},
                        },
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {"output_tokens": 2},
                    },
                ),
                StreamEvent(kind="json", payload={"type": "content_block_stop", "index": 0}),
                StreamEvent(kind="json", payload={"type": "message_stop"}),
            ],
        )

        message = payload["choices"][0]["message"]
        self.assertEqual("lookup", message["tool_calls"][0]["function"]["name"])
        self.assertEqual('{"q": "x"}', message["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(3, payload["usage"]["prompt_tokens"])
        self.assertEqual(2, payload["usage"]["completion_tokens"])

    def test_claude_stream_emits_one_complete_usage_chunk(self) -> None:
        translator = ClaudeChatTranslator()
        state: dict[str, Any] = {}

        start_chunks = translator.translate_stream_event(
            "claude-sonnet-4-5",
            {},
            {},
            StreamEvent(
                kind="json",
                payload={
                    "type": "message_start",
                    "message": {
                        "id": "msg-usage",
                        "model": "claude-sonnet-4-5",
                        "usage": {"input_tokens": 3, "output_tokens": 0},
                    },
                },
            ),
            state,
        )
        delta_chunks = translator.translate_stream_event(
            "claude-sonnet-4-5",
            {},
            {},
            StreamEvent(
                kind="json",
                payload={
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 2},
                },
            ),
            state,
        )

        usage_chunks = [
            chunk.payload["usage"]
            for chunk in [*start_chunks, *delta_chunks]
            if chunk.kind == "json" and isinstance(chunk.payload, dict) and isinstance(chunk.payload.get("usage"), dict)
        ]
        self.assertEqual(
            [{"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}],
            usage_chunks,
        )

    def test_native_claude_aggregation_preserves_citations(self) -> None:
        citation = {
            "type": "char_location",
            "cited_text": "source",
            "document_index": 0,
            "document_title": "doc",
            "start_char_index": 0,
            "end_char_index": 6,
        }
        payload = aggregate_stream_to_native_response(
            source_format="claude_chat",
            model_name="claude-sonnet-4-5",
            events=[
                StreamEvent(
                    kind="json",
                    payload={"type": "message_start", "message": {"content": []}},
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": "answer", "citations": []},
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "citations_delta", "citation": citation},
                    },
                ),
                StreamEvent(kind="json", payload={"type": "message_stop"}),
            ],
        )

        self.assertEqual([citation], payload["content"][0]["citations"])

    def test_native_responses_aggregation_preserves_custom_output_item_shape(self) -> None:
        custom_item = {
            "id": "ct_1",
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "shell",
            "input": "ls",
        }
        payload = aggregate_stream_to_native_response(
            source_format="openai_responses",
            model_name="gpt-5-codex",
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.completed",
                        "response": {
                            "id": "resp-custom",
                            "object": "response",
                            "status": "completed",
                            "model": "gpt-5-codex",
                            "output": [custom_item],
                        },
                    },
                )
            ],
        )

        self.assertEqual(custom_item, payload["output"][0])

    def test_responses_and_claude_targets_preserve_refusal(self) -> None:
        registry = build_default_translator_registry()
        chat_payload = {
            "id": "chatcmpl-refusal",
            "created": 123,
            "model": "gpt-4.1",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "refusal": "I cannot help with that.",
                    },
                    "finish_reason": "stop",
                }
            ],
        }

        responses = registry.get("openai_chat", "openai_responses").translate_nonstream_response(
            "gpt-4.1", {"input": "hi"}, {"model": "gpt-4.1"}, chat_payload
        )
        claude = registry.get("openai_chat", "claude_chat").translate_nonstream_response(
            "gpt-4.1", {"messages": []}, {"model": "gpt-4.1"}, chat_payload
        )

        self.assertEqual("I cannot help with that.", responses["output"][0]["content"][0]["refusal"])
        self.assertEqual("I cannot help with that.", claude["content"][0]["text"])

    def test_responses_eof_without_terminal_event_is_rejected(self) -> None:
        with self.assertRaisesRegex(StreamAggregationError, "incomplete"):
            aggregate_stream_to_native_response(
                source_format="openai_responses",
                model_name="gpt-5-codex",
                events=[
                    StreamEvent(
                        kind="json",
                        payload={
                            "type": "response.created",
                            "response": {"id": "resp-eof", "created_at": 123, "model": "gpt-5-codex"},
                        },
                    ),
                    StreamEvent(
                        kind="json",
                        payload={
                            "type": "response.output_text.delta",
                            "item_id": "msg-1",
                            "output_index": 0,
                            "delta": "partial",
                        },
                    ),
                ],
            )

    def test_native_openai_chat_aggregation_preserves_reasoning_and_tools(self) -> None:
        payload = aggregate_stream_to_native_response(
            source_format="openai_chat",
            model_name="gpt-4.1",
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "id": "chatcmpl-native",
                        "created": 123,
                        "model": "gpt-4.1",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "reasoning_content": "Think first. ",
                                    "content": "Hello",
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "id": "chatcmpl-native",
                        "created": 123,
                        "model": "gpt-4.1",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call-1",
                                            "type": "function",
                                            "function": {"name": "lookup", "arguments": '{"q":'},
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "id": "chatcmpl-native",
                        "created": 123,
                        "model": "gpt-4.1",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "content": " world",
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"name": "lookup", "arguments": '"x"}'},
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                    },
                ),
                StreamEvent(kind="done", payload="[DONE]"),
            ],
        )

        message = payload["choices"][0]["message"]
        self.assertEqual("Hello world", message["content"])
        self.assertEqual("Think first. ", message["reasoning_content"])
        self.assertEqual("lookup", message["tool_calls"][0]["function"]["name"])
        self.assertEqual('{"q":"x"}', message["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(5, payload["usage"]["total_tokens"])

    def test_native_openai_responses_aggregation_preserves_terminal_response_and_output(self) -> None:
        native_payload = aggregate_stream_to_native_response(
            source_format="openai_responses",
            model_name="gpt-5-codex",
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.created",
                        "response": {
                            "id": "resp-native",
                            "created_at": 123,
                            "model": "gpt-5-codex",
                            "metadata": {"trace": "created"},
                        },
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.output_text.delta",
                        "item_id": "msg-native",
                        "output_index": 0,
                        "delta": "Hello",
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.output_text.done",
                        "item_id": "msg-native",
                        "output_index": 0,
                        "text": "Hello world",
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.reasoning_summary_text.delta",
                        "item_id": "reasoning-native",
                        "output_index": 1,
                        "summary_index": 0,
                        "delta": "Need context",
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.reasoning_summary_text.done",
                        "item_id": "reasoning-native",
                        "output_index": 1,
                        "summary_index": 0,
                        "text": "Need context first",
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.output_item.added",
                        "output_index": 2,
                        "item": {
                            "id": "fc-native",
                            "type": "function_call",
                            "call_id": "call-native",
                            "name": "lookup",
                        },
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc-native",
                        "output_index": 2,
                        "delta": '{"q":',
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.function_call_arguments.done",
                        "item_id": "fc-native",
                        "output_index": 2,
                        "arguments": '{"q":"x"}',
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.completed",
                        "response": {
                            "id": "resp-native",
                            "object": "response",
                            "created_at": 123,
                            "status": "completed",
                            "model": "gpt-5-codex",
                            "metadata": {"trace": "completed"},
                            "output": [],
                            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                        },
                    },
                ),
            ],
        )

        self.assertEqual("response", native_payload["object"])
        self.assertEqual({"trace": "completed"}, native_payload["metadata"])
        self.assertEqual(3, len(native_payload["output"]))
        translator = OpenAIResponsesTranslator()
        chat_payload = translator.translate_nonstream_response(
            "gpt-5-codex",
            {"messages": []},
            {"model": "gpt-5-codex"},
            native_payload,
        )
        self.assertEqual("Hello world", chat_payload["choices"][0]["message"]["content"])
        self.assertEqual("Need context first", chat_payload["choices"][0]["message"]["reasoning_content"])
        self.assertEqual('{"q":"x"}', chat_payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(5, chat_payload["usage"]["total_tokens"])

    def test_native_claude_aggregation_preserves_thinking_tools_and_usage(self) -> None:
        payload = aggregate_stream_to_native_response(
            source_format="claude_chat",
            model_name="claude-sonnet-4-5",
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "message_start",
                        "message": {
                            "id": "msg-native",
                            "type": "message",
                            "role": "assistant",
                            "model": "claude-sonnet-4-5",
                            "content": [],
                            "usage": {"input_tokens": 3},
                        },
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": "Hello"},
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": " world"},
                    },
                ),
                StreamEvent(kind="json", payload={"type": "content_block_stop", "index": 0}),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {"type": "thinking", "thinking": "Plan"},
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "thinking_delta", "thinking": " carefully"},
                    },
                ),
                StreamEvent(kind="json", payload={"type": "content_block_stop", "index": 1}),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "content_block_start",
                        "index": 2,
                        "content_block": {"type": "tool_use", "id": "toolu-native", "name": "lookup", "input": {}},
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "content_block_delta",
                        "index": 2,
                        "delta": {"type": "input_json_delta", "partial_json": '{"q":"x"}'},
                    },
                ),
                StreamEvent(kind="json", payload={"type": "content_block_stop", "index": 2}),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {"output_tokens": 2},
                    },
                ),
                StreamEvent(kind="json", payload={"type": "message_stop"}),
            ],
        )

        self.assertEqual("Hello world", payload["content"][0]["text"])
        self.assertEqual("Plan carefully", payload["content"][1]["thinking"])
        self.assertEqual({"q": "x"}, payload["content"][2]["input"])
        self.assertEqual("tool_use", payload["stop_reason"])
        self.assertEqual(3, payload["usage"]["input_tokens"])
        self.assertEqual(2, payload["usage"]["output_tokens"])

    def test_native_response_aggregation_rejects_responses_and_claude_failures(self) -> None:
        with self.assertRaisesRegex(StreamAggregationError, "quota"):
            aggregate_stream_to_native_response(
                source_format="openai_responses",
                model_name="gpt-5-codex",
                events=[
                    StreamEvent(
                        kind="json",
                        payload={
                            "type": "response.failed",
                            "response": {"error": {"type": "rate_limit_error", "message": "quota exceeded"}},
                        },
                    )
                ],
            )
        with self.assertRaisesRegex(StreamAggregationError, "overloaded"):
            aggregate_stream_to_native_response(
                source_format="claude_chat",
                model_name="claude-sonnet-4-5",
                events=[
                    StreamEvent(
                        kind="json",
                        payload={
                            "type": "error",
                            "error": {"type": "overloaded_error", "message": "overloaded"},
                        },
                    )
                ],
            )

    def test_stream_aggregator_preserves_legacy_function_call(self) -> None:
        translator = OpenAIChatTranslator()

        payload = aggregate_stream_to_openai_chat(
            translator=translator,
            model_name="gpt-4.1",
            original_request={"messages": [], "stream": True},
            translated_request={"model": "gpt-4.1", "stream": True},
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "id": "chatcmpl-legacy",
                        "created": 123,
                        "model": "gpt-4.1",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"function_call": {"name": "lookup", "arguments": '{"q":'}},
                                "finish_reason": None,
                            }
                        ],
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "id": "chatcmpl-legacy",
                        "created": 123,
                        "model": "gpt-4.1",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"function_call": {"arguments": '"x"}'}},
                                "finish_reason": "function_call",
                            }
                        ],
                    },
                ),
                StreamEvent(kind="done", payload="[DONE]"),
            ],
        )

        message = payload["choices"][0]["message"]
        self.assertEqual("lookup", message["function_call"]["name"])
        self.assertEqual('{"q":"x"}', message["function_call"]["arguments"])
        self.assertEqual("function_call", payload["choices"][0]["finish_reason"])

    def test_openai_responses_function_call_name_is_emitted_once(self) -> None:
        translator = OpenAIResponsesTranslator()

        payload = aggregate_stream_to_openai_chat(
            translator=translator,
            model_name="gpt-5-codex",
            original_request={"messages": [], "stream": True},
            translated_request={"model": "gpt-5-codex", "stream": True},
            events=[
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.created",
                        "response": {"id": "resp-1", "created_at": 123, "model": "gpt-5-codex"},
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.output_item.added",
                        "output_index": 1,
                        "item": {
                            "id": "fc-1",
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "lookup",
                        },
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc-1",
                        "output_index": 1,
                        "delta": '{"q":',
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.function_call_arguments.delta",
                        "item_id": "fc-1",
                        "output_index": 1,
                        "delta": '"x"}',
                    },
                ),
                StreamEvent(
                    kind="json",
                    payload={
                        "type": "response.completed",
                        "response": {
                            "id": "resp-1",
                            "created_at": 123,
                            "model": "gpt-5-codex",
                            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                        },
                    },
                ),
            ],
        )

        self.assertEqual(1, len(payload["choices"]))
        self.assertEqual(0, payload["choices"][0]["index"])
        self.assertEqual(1, len(payload["choices"][0]["message"]["tool_calls"]))
        tool_call = payload["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual("lookup", tool_call["function"]["name"])
        self.assertEqual('{"q":"x"}', tool_call["function"]["arguments"])

    def test_openai_chat_target_converters_preserve_legacy_function_call(self) -> None:
        registry = build_default_translator_registry()
        chat_payload = {
            "id": "chatcmpl-legacy",
            "created": 123,
            "model": "gpt-4.1",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "function_call": {"name": "lookup", "arguments": '{"q":"x"}'},
                    },
                    "finish_reason": "function_call",
                }
            ],
        }

        responses = registry.get("openai_chat", "openai_responses").translate_nonstream_response(
            "gpt-4.1",
            {"input": "hi"},
            {"model": "gpt-4.1"},
            chat_payload,
        )
        claude = registry.get("openai_chat", "claude_chat").translate_nonstream_response(
            "gpt-4.1",
            {"messages": []},
            {"model": "gpt-4.1"},
            chat_payload,
        )

        responses_call = next(item for item in responses["output"] if item["type"] == "function_call")
        self.assertEqual("lookup", responses_call["name"])
        self.assertEqual('{"q":"x"}', responses_call["arguments"])
        self.assertTrue(responses_call["call_id"])
        self.assertEqual("lookup", claude["content"][0]["name"])
        self.assertEqual({"q": "x"}, claude["content"][0]["input"])

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
                "store": False,
                "include": ["reasoning.encrypted_content"],
                "parallel_tool_calls": True,
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
        self.assertFalse(translated["store"])
        self.assertEqual(["reasoning.encrypted_content"], translated["include"])
        self.assertTrue(translated["parallel_tool_calls"])

    def test_openai_responses_translator_maps_chat_reasoning_effort(self) -> None:
        translator = OpenAIResponsesTranslator()

        translated = translator.translate_request(
            "gpt-5.4",
            {
                "messages": [{"role": "user", "content": "Hello"}],
                "reasoning_effort": "unknown-effort",
            },
            False,
        )

        self.assertEqual({"effort": "xhigh"}, translated["reasoning"])

    def test_openai_chat_responses_translator_maps_responses_reasoning(self) -> None:
        translator = OpenAIChatResponsesTranslator()

        translated = translator.translate_request(
            "gpt-5.4",
            {
                "input": "Hello",
                "reasoning": {"effort": "medium"},
            },
            False,
        )

        self.assertEqual("medium", translated["reasoning_effort"])

    def test_openai_chat_responses_translator_falls_back_to_xhigh_reasoning(self) -> None:
        translator = OpenAIChatResponsesTranslator()

        translated = translator.translate_request(
            "gpt-5.4",
            {
                "input": "Hello",
                "reasoning": {"effort": "not-a-level"},
            },
            False,
        )

        self.assertEqual("xhigh", translated["reasoning_effort"])

    def test_direct_responses_to_chat_preserves_custom_tools_and_history_order(self) -> None:
        translator = OpenAIChatResponsesTranslator()

        translated = translator.translate_request(
            "gpt-4.1",
            {
                "input": [
                    {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "checking"}],
                    },
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_1",
                        "namespace": "ops",
                        "name": "shell",
                        "input": "pwd",
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "queued"}],
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_1",
                        "output": "C:/repo",
                    },
                    {
                        "type": "additional_tools",
                        "tools": [
                            {
                                "type": "namespace",
                                "name": "ops",
                                "tools": [{"type": "custom", "name": "shell", "description": "Run command"}],
                            }
                        ],
                    },
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "answer",
                        "schema": {"type": "object"},
                        "strict": True,
                    }
                },
            },
            False,
        )

        self.assertEqual(["assistant", "tool", "assistant"], [item["role"] for item in translated["messages"]])
        tool_call = translated["messages"][0]["tool_calls"][0]
        self.assertEqual("checking", translated["messages"][0]["reasoning_content"])
        self.assertEqual("ops__shell", tool_call["function"]["name"])
        self.assertEqual('{"input": "pwd"}', tool_call["function"]["arguments"])
        self.assertEqual("call_1", translated["messages"][1]["tool_call_id"])
        self.assertEqual("queued", translated["messages"][2]["content"][0]["text"])
        self.assertEqual("ops__shell", translated["tools"][0]["function"]["name"])
        self.assertEqual("string", translated["tools"][0]["function"]["parameters"]["properties"]["input"]["type"])
        self.assertEqual("answer", translated["response_format"]["json_schema"]["name"])

    def test_direct_responses_response_to_chat_maps_custom_tool_call(self) -> None:
        translator = OpenAIResponsesTranslator()

        translated = translator.translate_nonstream_response(
            "gpt-4.1",
            {},
            {"model": "gpt-4.1"},
            {
                "id": "resp_custom",
                "model": "gpt-4.1",
                "output": [
                    {
                        "id": "ctc_call_1",
                        "type": "custom_tool_call",
                        "call_id": "call_1",
                        "namespace": "ops",
                        "name": "shell",
                        "input": "pwd",
                    }
                ],
            },
        )

        message = translated["choices"][0]["message"]
        self.assertEqual("tool_calls", translated["choices"][0]["finish_reason"])
        self.assertEqual("ops__shell", message["tool_calls"][0]["function"]["name"])
        self.assertEqual('{"input": "pwd"}', message["tool_calls"][0]["function"]["arguments"])

    def test_direct_responses_stream_to_chat_maps_custom_tool_call(self) -> None:
        translator = OpenAIResponsesTranslator()
        state: dict[str, Any] = {}
        events = [
            StreamEvent(
                kind="json",
                event="response.output_item.added",
                payload={
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": "ctc_call_1",
                        "type": "custom_tool_call",
                        "call_id": "call_1",
                        "namespace": "ops",
                        "name": "shell",
                    },
                },
            ),
            StreamEvent(
                kind="json",
                event="response.custom_tool_call_input.done",
                payload={
                    "type": "response.custom_tool_call_input.done",
                    "item_id": "ctc_call_1",
                    "output_index": 0,
                    "input": "pwd",
                },
            ),
            StreamEvent(
                kind="json",
                event="response.completed",
                payload={"type": "response.completed", "response": {"model": "gpt-4.1"}},
            ),
        ]

        chunks = [
            chunk
            for event in events
            for chunk in translator.translate_stream_event("gpt-4.1", {}, {"model": "gpt-4.1"}, event, state)
        ]
        tool_deltas = [
            chunk.payload["choices"][0]["delta"]["tool_calls"][0]
            for chunk in chunks
            if chunk.kind == "json"
            and chunk.payload.get("choices")
            and chunk.payload["choices"][0]["delta"].get("tool_calls")
        ]

        self.assertEqual("ops__shell", tool_deltas[0]["function"]["name"])
        self.assertEqual('{"input": "pwd"}', "".join(item["function"].get("arguments", "") for item in tool_deltas))

    def test_direct_responses_stream_to_chat_emits_arguments_from_added_item(self) -> None:
        translator = OpenAIResponsesTranslator()

        chunks = translator.translate_stream_event(
            "gpt-4.1",
            {},
            {"model": "gpt-4.1"},
            StreamEvent(
                kind="json",
                event="response.output_item.added",
                payload={
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": "fc_call_1",
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "lookup",
                        "arguments": '{"q":"x"}',
                    },
                },
            ),
            {},
        )

        tool_call = chunks[0].payload["choices"][0]["delta"]["tool_calls"][0]
        self.assertEqual('{"q":"x"}', tool_call["function"]["arguments"])

    def test_direct_claude_to_responses_maps_thinking_effort(self) -> None:
        translator = build_default_translator_registry().get("openai_responses", "claude_chat")

        translated = translator.translate_request(
            "gpt-5.4",
            {
                "messages": [{"role": "user", "content": "Hello"}],
                "thinking": {"type": "mystery"},
                "max_tokens": 1000,
            },
            False,
        )

        self.assertEqual({"effort": "xhigh"}, translated["reasoning"])

    def test_direct_responses_to_claude_maps_reasoning_effort(self) -> None:
        translator = build_default_translator_registry().get("claude_chat", "openai_responses")

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "input": "Hello",
                "reasoning": {"effort": "not-a-level"},
                "max_output_tokens": 20000,
            },
            False,
        )

        self.assertEqual({"type": "enabled", "budget_tokens": 16384}, translated["thinking"])

    def test_direct_responses_request_to_claude_preserves_native_content(self) -> None:
        translator = ClaudeOpenAIResponsesTranslator()

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "instructions": "Be exact",
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "cache_control": {"type": "ephemeral"},
                        "content": [{"type": "input_text", "text": "Use JSON"}],
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": "data:image/png;base64,aGVsbG8=",
                                "cache_control": {"type": "ephemeral"},
                            },
                            {
                                "type": "input_file",
                                "file_data": "data:application/pdf;base64,cGRm",
                            },
                        ],
                    },
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_1",
                        "namespace": "ops",
                        "name": "shell",
                        "input": "pwd",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_1",
                        "output": "C:/repo",
                    },
                ],
                "tools": [
                    {
                        "type": "namespace",
                        "name": "ops",
                        "tools": [
                            {
                                "type": "custom",
                                "name": "shell",
                                "description": "Run a command",
                            }
                        ],
                    }
                ],
                "max_output_tokens": 512,
            },
            False,
        )

        self.assertEqual(512, translated["max_tokens"])
        self.assertEqual("Be exact", translated["system"][0]["text"])
        self.assertEqual("Use JSON", translated["system"][1]["text"])
        self.assertEqual({"type": "ephemeral"}, translated["system"][1]["cache_control"])
        user_blocks = translated["messages"][0]["content"]
        self.assertEqual("image", user_blocks[0]["type"])
        self.assertEqual("base64", user_blocks[0]["source"]["type"])
        self.assertEqual({"type": "ephemeral"}, user_blocks[0]["cache_control"])
        self.assertEqual("document", user_blocks[1]["type"])
        self.assertEqual("ops__shell", translated["messages"][1]["content"][0]["name"])
        self.assertEqual({"input": "pwd"}, translated["messages"][1]["content"][0]["input"])
        self.assertEqual("tool_result", translated["messages"][2]["content"][0]["type"])
        self.assertEqual("ops__shell", translated["tools"][0]["name"])

    def test_direct_responses_request_to_claude_matches_tool_compatibility_rules(self) -> None:
        translator = ClaudeOpenAIResponsesTranslator()

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call.with space:1",
                        "name": "lookup",
                        "arguments": '{"query":"x"}',
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Checking."}],
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call.with space:1",
                        "output": "done",
                    },
                    {
                        "type": "additional_tools",
                        "tools": [
                            {"type": "custom", "name": "lookup", "description": "lower priority"},
                            {
                                "type": "namespace",
                                "name": "ops",
                                "tools": [{"type": "custom", "name": "shell"}],
                            },
                        ],
                    },
                ],
                "tools": [
                    {
                        "type": "function",
                        "name": "lookup",
                        "description": "top level",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "oneOf": [{"required": ["query"]}],
                        },
                    }
                ],
            },
            False,
        )

        self.assertEqual(2, len(translated["messages"]))
        assistant_blocks = translated["messages"][0]["content"]
        self.assertEqual(["text", "tool_use"], [block["type"] for block in assistant_blocks])
        self.assertEqual("call_with_space_1", assistant_blocks[1]["id"])
        self.assertEqual("call_with_space_1", translated["messages"][1]["content"][0]["tool_use_id"])
        self.assertEqual(["lookup", "ops__shell"], [tool["name"] for tool in translated["tools"]])
        self.assertEqual("top level", translated["tools"][0]["description"])
        self.assertNotIn("oneOf", translated["tools"][0]["input_schema"])
        self.assertEqual("string", translated["tools"][1]["input_schema"]["properties"]["input"]["type"])

    def test_direct_responses_request_to_claude_preserves_system_markers_and_web_search(self) -> None:
        translator = ClaudeOpenAIResponsesTranslator()

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "cache_control": {"type": "ephemeral"},
                        "content": [
                            {"type": "input_text", "text": "Use evidence"},
                            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                        ],
                    }
                ],
                "tools": [
                    {
                        "type": "web_search",
                        "max_uses": 3,
                        "filters": {"allowed_domains": ["example.com"]},
                        "user_location": {"type": "approximate", "country": "CN"},
                    }
                ],
            },
            False,
        )

        self.assertEqual(["text", "input_image"], [block["type"] for block in translated["system"]])
        self.assertEqual({"type": "ephemeral"}, translated["system"][1]["cache_control"])
        self.assertEqual("web_search_20250305", translated["tools"][0]["type"])
        self.assertEqual(["example.com"], translated["tools"][0]["allowed_domains"])
        self.assertEqual("CN", translated["tools"][0]["user_location"]["country"])

    def test_direct_responses_request_to_claude_drops_incompatible_reasoning_signature(self) -> None:
        translator = ClaudeOpenAIResponsesTranslator()

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "input": [
                    {
                        "type": "reasoning",
                        "encrypted_content": "gAAAA-invalid-gpt-signature",
                        "summary": [{"type": "summary_text", "text": "private"}],
                    },
                    {"type": "message", "role": "user", "content": "continue"},
                ]
            },
            False,
        )

        self.assertEqual(1, len(translated["messages"]))
        self.assertEqual("user", translated["messages"][0]["role"])

    def test_direct_responses_request_to_claude_preserves_valid_reasoning_signature(self) -> None:
        translator = ClaudeOpenAIResponsesTranslator()
        signature = _valid_claude_cais_signature()

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "input": [
                    {
                        "type": "reasoning",
                        "encrypted_content": signature,
                        "summary": [{"type": "summary_text", "text": "private"}],
                    },
                    {"type": "message", "role": "user", "content": "continue"},
                ]
            },
            False,
        )

        reasoning = translated["messages"][0]["content"][0]
        self.assertEqual("thinking", reasoning["type"])
        self.assertEqual("private", reasoning["thinking"])
        self.assertEqual(signature, reasoning["signature"])

    def test_direct_claude_response_to_responses_preserves_extended_blocks(self) -> None:
        translator = ClaudeOpenAIResponsesTranslator()
        original_request = {
            "tools": [
                {
                    "type": "namespace",
                    "name": "ops",
                    "tools": [{"type": "custom", "name": "shell"}],
                }
            ]
        }

        response = translator.translate_nonstream_response(
            "claude-sonnet-4-5",
            original_request,
            {"model": "claude-sonnet-4-5"},
            {
                "id": "msg_1",
                "model": "claude-sonnet-4-5",
                "content": [
                    {"type": "thinking", "thinking": "check", "signature": "sig_1"},
                    {"type": "redacted_thinking", "data": "opaque"},
                    {
                        "type": "text",
                        "text": "answer",
                        "citations": [{"type": "web_search_result_location", "url": "https://example.com"}],
                    },
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "ops__shell",
                        "input": {"input": "pwd"},
                    },
                ],
                "usage": {
                    "input_tokens": 5,
                    "cache_creation_input_tokens": 2,
                    "cache_read_input_tokens": 4,
                    "output_tokens": 3,
                },
            },
        )

        reasoning = [item for item in response["output"] if item["type"] == "reasoning"]
        self.assertEqual("sig_1", reasoning[0]["encrypted_content"])
        self.assertEqual("claude-redacted-thinking:opaque", reasoning[1]["encrypted_content"])
        message = next(item for item in response["output"] if item["type"] == "message")
        self.assertEqual("https://example.com", message["content"][0]["annotations"][0]["url"])
        tool_call = next(item for item in response["output"] if item["type"] == "custom_tool_call")
        self.assertEqual("shell", tool_call["name"])
        self.assertEqual("ops", tool_call["namespace"])
        self.assertEqual("pwd", tool_call["input"])
        self.assertEqual(11, response["usage"]["input_tokens"])
        self.assertEqual(4, response["usage"]["input_tokens_details"]["cached_tokens"])
        self.assertEqual(2, response["usage"]["input_tokens_details"]["cache_write_tokens"])

    def test_direct_responses_stream_to_claude_maps_events_without_chat_chunks(self) -> None:
        translator = OpenAIResponsesClaudeTranslator()
        state: dict[str, Any] = {}
        events = [
            StreamEvent(
                kind="json",
                event="response.created",
                payload={
                    "type": "response.created",
                    "response": {"id": "resp_1", "model": "gpt-5.4"},
                },
            ),
            StreamEvent(
                kind="json",
                event="response.output_text.delta",
                payload={"type": "response.output_text.delta", "output_index": 0, "delta": "hi"},
            ),
            StreamEvent(
                kind="json",
                event="response.completed",
                payload={
                    "type": "response.completed",
                    "response": {
                        "id": "resp_1",
                        "model": "gpt-5.4",
                        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                    },
                },
            ),
        ]

        chunks = [
            chunk for event in events for chunk in translator.translate_stream_event("gpt-5.4", {}, {}, event, state)
        ]

        event_names = [chunk.event for chunk in chunks]
        self.assertEqual("message_start", event_names[0])
        self.assertIn("content_block_start", event_names)
        self.assertIn("content_block_delta", event_names)
        self.assertEqual("message_stop", event_names[-1])
        self.assertTrue(all(chunk.payload.get("object") != "chat.completion.chunk" for chunk in chunks))

    def test_direct_responses_stream_to_claude_keeps_signature_from_added_item(self) -> None:
        translator = OpenAIResponsesClaudeTranslator()
        state: dict[str, Any] = {}
        signature = _valid_claude_cais_signature()
        events = [
            StreamEvent(
                kind="json",
                event="response.output_item.added",
                payload={
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": "rs_1",
                        "type": "reasoning",
                        "encrypted_content": signature,
                    },
                },
            ),
            StreamEvent(
                kind="json",
                event="response.reasoning_summary_text.done",
                payload={
                    "type": "response.reasoning_summary_text.done",
                    "item_id": "rs_1",
                    "text": "private",
                },
            ),
            StreamEvent(
                kind="json",
                event="response.output_item.done",
                payload={
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {"id": "rs_1", "type": "reasoning"},
                },
            ),
            StreamEvent(
                kind="json",
                event="response.completed",
                payload={"type": "response.completed", "response": {"usage": {}}},
            ),
        ]

        chunks = [
            chunk for event in events for chunk in translator.translate_stream_event("gpt-5.4", {}, {}, event, state)
        ]
        thinking_delta = next(
            chunk.payload["delta"]
            for chunk in chunks
            if chunk.event == "content_block_delta" and chunk.payload["delta"]["type"] == "thinking_delta"
        )
        signature_delta = next(
            chunk.payload["delta"]
            for chunk in chunks
            if chunk.event == "content_block_delta" and chunk.payload["delta"]["type"] == "signature_delta"
        )

        self.assertEqual("private", thinking_delta["thinking"])
        self.assertEqual(signature, signature_delta["signature"])

    def test_direct_claude_stream_to_responses_hides_server_tools_and_keeps_indices_contiguous(self) -> None:
        translator = ClaudeOpenAIResponsesTranslator()
        state: dict[str, Any] = {}
        events = [
            StreamEvent(
                kind="json",
                payload={
                    "type": "message_start",
                    "message": {"id": "msg_1", "model": "claude-sonnet-4-5", "usage": {}},
                },
            ),
            StreamEvent(
                kind="json",
                payload={"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            ),
            StreamEvent(
                kind="json",
                payload={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "A"}},
            ),
            StreamEvent(kind="json", payload={"type": "content_block_stop", "index": 0}),
            StreamEvent(
                kind="json",
                payload={
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "server_tool_use", "name": "web_search"},
                },
            ),
            StreamEvent(kind="json", payload={"type": "content_block_stop", "index": 1}),
            StreamEvent(
                kind="json",
                payload={"type": "content_block_start", "index": 2, "content_block": {"type": "text"}},
            ),
            StreamEvent(
                kind="json",
                payload={"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "B"}},
            ),
            StreamEvent(kind="json", payload={"type": "content_block_stop", "index": 2}),
            StreamEvent(
                kind="json",
                payload={
                    "type": "content_block_start",
                    "index": 3,
                    "content_block": {"type": "tool_use", "id": "call_1", "name": "lookup"},
                },
            ),
            StreamEvent(
                kind="json",
                payload={
                    "type": "content_block_delta",
                    "index": 3,
                    "delta": {"type": "input_json_delta", "partial_json": '{"q":"x"}'},
                },
            ),
            StreamEvent(kind="json", payload={"type": "content_block_stop", "index": 3}),
            StreamEvent(kind="json", payload={"type": "message_stop"}),
        ]

        chunks = [
            chunk
            for event in events
            for chunk in translator.translate_stream_event("claude-sonnet-4-5", {}, {}, event, state)
        ]
        done_items = [chunk.payload for chunk in chunks if chunk.event == "response.output_item.done"]

        self.assertEqual([0, 1], [item["output_index"] for item in done_items])
        self.assertEqual(["message", "function_call"], [item["item"]["type"] for item in done_items])
        self.assertEqual("AB", done_items[0]["item"]["content"][0]["text"])

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

    def test_chat_to_claude_preserves_developer_media_and_cache_control(self) -> None:
        translator = ClaudeChatTranslator()

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "messages": [
                    {
                        "role": "developer",
                        "content": [
                            {
                                "type": "text",
                                "text": "Use JSON",
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "cache_control": {"type": "ephemeral"},
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                            },
                            {
                                "type": "file",
                                "file": {
                                    "file_data": "data:application/pdf;base64,cGRm",
                                },
                            },
                        ],
                    },
                ],
                "tools": [
                    {
                        "type": "function",
                        "cache_control": {"type": "ephemeral"},
                        "function": {
                            "name": "lookup",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
            },
            False,
        )

        self.assertEqual("Use JSON", translated["system"][0]["text"])
        self.assertEqual({"type": "ephemeral"}, translated["system"][0]["cache_control"])
        user_blocks = translated["messages"][0]["content"]
        self.assertEqual("image", user_blocks[0]["type"])
        self.assertEqual("base64", user_blocks[0]["source"]["type"])
        self.assertEqual("document", user_blocks[1]["type"])
        self.assertEqual({"type": "ephemeral"}, user_blocks[1]["cache_control"])
        self.assertEqual({"type": "ephemeral"}, translated["tools"][0]["cache_control"])

    def test_claude_chat_translator_maps_chat_reasoning_effort(self) -> None:
        translator = ClaudeChatTranslator()

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "messages": [{"role": "user", "content": "Hello"}],
                "reasoning_effort": "xhigh",
                "max_tokens": 20000,
            },
            False,
        )

        self.assertEqual({"type": "enabled", "budget_tokens": 16384}, translated["thinking"])

    def test_claude_chat_translator_raises_max_tokens_for_thinking_budget(self) -> None:
        translator = ClaudeChatTranslator()

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "messages": [{"role": "user", "content": "Hello"}],
                "reasoning_effort": "xhigh",
                "max_tokens": 1000,
            },
            False,
        )

        self.assertEqual({"type": "enabled", "budget_tokens": 16384}, translated["thinking"])
        self.assertEqual(16385, translated["max_tokens"])

    def test_claude_chat_translator_disables_thinking_from_reasoning_effort(self) -> None:
        translator = ClaudeChatTranslator()

        translated = translator.translate_request(
            "claude-sonnet-4-5",
            {
                "messages": [{"role": "user", "content": "Hello"}],
                "reasoning_effort": "none",
            },
            False,
        )

        self.assertEqual({"type": "disabled"}, translated["thinking"])

    def test_openai_chat_claude_translator_preserves_responses_extension_fields(
        self,
    ) -> None:
        translator = OpenAIChatClaudeTranslator()

        translated = translator.translate_request(
            "gpt-5-codex",
            {
                "max_tokens": 256,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                ],
                "store": False,
                "include": ["reasoning.encrypted_content"],
                "parallel_tool_calls": True,
            },
            True,
        )

        self.assertEqual("gpt-5-codex", translated["model"])
        self.assertEqual(256, translated["max_tokens"])
        self.assertFalse(translated["store"])
        self.assertEqual(["reasoning.encrypted_content"], translated["include"])
        self.assertTrue(translated["parallel_tool_calls"])

    def test_openai_chat_claude_translator_falls_back_to_xhigh_thinking_effort(self) -> None:
        translator = OpenAIChatClaudeTranslator()

        translated = translator.translate_request(
            "gpt-5-codex",
            {
                "max_tokens": 256,
                "thinking": {"type": "mystery"},
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
                ],
            },
            False,
        )

        self.assertEqual("xhigh", translated["reasoning_effort"])

    def test_openai_chat_claude_translator_maps_reasoning_details_stream(self) -> None:
        translator = OpenAIChatClaudeTranslator()
        state: dict[str, Any] = {}

        first_chunks = translator.translate_stream_event(
            "minimax-m3",
            {"messages": [], "stream": True},
            {"model": "minimax-m3"},
            StreamEvent(
                kind="json",
                payload={
                    "id": "chatcmpl_1",
                    "model": "minimax-m3",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"reasoning_details": [{"text": "Think"}]},
                            "finish_reason": None,
                        }
                    ],
                },
            ),
            state,
        )
        second_chunks = translator.translate_stream_event(
            "minimax-m3",
            {"messages": [], "stream": True},
            {"model": "minimax-m3"},
            StreamEvent(
                kind="json",
                payload={
                    "id": "chatcmpl_1",
                    "model": "minimax-m3",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"reasoning_details": [{"text": "Think more"}]},
                            "finish_reason": None,
                        }
                    ],
                },
            ),
            state,
        )

        first_delta = [chunk for chunk in first_chunks if chunk.payload.get("type") == "content_block_delta"][-1]
        second_delta = [chunk for chunk in second_chunks if chunk.payload.get("type") == "content_block_delta"][-1]
        self.assertEqual("Think", first_delta.payload["delta"]["thinking"])
        self.assertEqual(" more", second_delta.payload["delta"]["thinking"])

    def test_openai_chat_claude_translator_maps_reasoning_details_nonstream(self) -> None:
        translator = OpenAIChatClaudeTranslator()

        translated = translator.translate_nonstream_response(
            "minimax-m3",
            {"messages": []},
            {"model": "minimax-m3"},
            {
                "id": "chatcmpl_1",
                "model": "minimax-m3",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Answer",
                            "reasoning_details": [{"text": "Think"}],
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

        self.assertEqual({"type": "thinking", "thinking": "Think"}, translated["content"][0])
        self.assertEqual({"type": "text", "text": "Answer"}, translated["content"][1])

    def test_openai_chat_responses_translator_maps_reasoning_details_stream(self) -> None:
        translator = OpenAIChatResponsesTranslator()

        chunks = translator.translate_stream_event(
            "minimax-m3",
            {"input": "Hello", "stream": True},
            {"model": "minimax-m3"},
            StreamEvent(
                kind="json",
                payload={
                    "id": "chatcmpl_1",
                    "model": "minimax-m3",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"reasoning_details": [{"text": "Think"}]},
                            "finish_reason": None,
                        }
                    ],
                },
            ),
            {},
        )

        reasoning_delta = [chunk for chunk in chunks if chunk.event == "response.reasoning_summary_text.delta"][-1]
        self.assertEqual("Think", reasoning_delta.payload["delta"])

    def test_openai_chat_responses_translator_maps_reasoning_details_nonstream(self) -> None:
        translator = OpenAIChatResponsesTranslator()

        translated = translator.translate_nonstream_response(
            "minimax-m3",
            {"input": "Hello"},
            {"model": "minimax-m3"},
            {
                "id": "chatcmpl_1",
                "model": "minimax-m3",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Answer",
                            "reasoning_details": [{"text": "Think"}],
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

        self.assertEqual("reasoning", translated["output"][0]["type"])
        self.assertEqual("Think", translated["output"][0]["summary"][0]["text"])

    def test_openai_chat_passthrough_keeps_reasoning_details_shape(self) -> None:
        translator = OpenAIChatTranslator()
        payload = {
            "choices": [
                {
                    "delta": {
                        "reasoning_details": [{"text": "Think"}],
                    }
                }
            ]
        }

        chunks = translator.translate_stream_event(
            "minimax-m3",
            {"messages": [], "stream": True},
            {"model": "minimax-m3"},
            StreamEvent(kind="json", payload=payload),
            {},
        )

        delta = chunks[0].payload["choices"][0]["delta"]
        self.assertEqual([{"text": "Think"}], delta["reasoning_details"])
        self.assertNotIn("reasoning_content", delta)

    def test_openai_chat_responses_translator_maps_nonstream_payload(self) -> None:
        translator = OpenAIChatResponsesTranslator()

        translated = translator.translate_nonstream_response(
            "gpt-4.1",
            {
                "instructions": "Be brief",
                "store": False,
                "include": ["reasoning.encrypted_content"],
            },
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
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        )

        self.assertEqual("response", translated["object"])
        self.assertEqual("completed", translated["status"])
        self.assertEqual("Hello from chat", translated["output"][0]["content"][0]["text"])
        self.assertEqual(
            {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            translated["usage"],
        )
        self.assertEqual("Be brief", translated["instructions"])
        self.assertFalse(translated["store"])
        self.assertEqual(["reasoning.encrypted_content"], translated["include"])

    def test_direct_chat_to_responses_restores_custom_tool_identity(self) -> None:
        translator = OpenAIChatResponsesTranslator()
        original_request = {
            "input": [
                {
                    "type": "additional_tools",
                    "tools": [
                        {
                            "type": "namespace",
                            "name": "ops",
                            "tools": [{"type": "custom", "name": "shell"}],
                        }
                    ],
                }
            ]
        }

        translated = translator.translate_nonstream_response(
            "gpt-4.1",
            original_request,
            {"model": "gpt-4.1"},
            {
                "id": "chatcmpl_custom",
                "model": "gpt-4.1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "ops__shell",
                                        "arguments": '{"input":"pwd"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )

        item = next(item for item in translated["output"] if item["type"] == "custom_tool_call")
        self.assertEqual("shell", item["name"])
        self.assertEqual("ops", item["namespace"])
        self.assertEqual("pwd", item["input"])

    def test_direct_chat_stream_to_responses_restores_custom_tool_identity(self) -> None:
        translator = OpenAIChatResponsesTranslator()
        state: dict[str, Any] = {}
        original_request = {
            "tools": [
                {
                    "type": "namespace",
                    "name": "ops",
                    "tools": [{"type": "custom", "name": "shell"}],
                }
            ]
        }
        events = [
            StreamEvent(
                kind="json",
                payload={
                    "id": "chatcmpl_custom_stream",
                    "model": "gpt-4.1",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "ops__shell", "arguments": ""},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                },
            ),
            StreamEvent(
                kind="json",
                payload={
                    "id": "chatcmpl_custom_stream",
                    "model": "gpt-4.1",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"input":"pwd"}'}}]},
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
            ),
            StreamEvent(kind="done", payload="[DONE]"),
        ]

        chunks = [
            chunk
            for event in events
            for chunk in translator.translate_stream_event(
                "gpt-4.1", original_request, {"model": "gpt-4.1"}, event, state
            )
        ]
        added = next(chunk.payload["item"] for chunk in chunks if chunk.event == "response.output_item.added")
        done = next(
            chunk.payload["item"]
            for chunk in chunks
            if chunk.event == "response.output_item.done" and chunk.payload["item"]["type"] == "custom_tool_call"
        )

        self.assertEqual("custom_tool_call", added["type"])
        self.assertEqual("shell", added["name"])
        self.assertEqual("ops", added["namespace"])
        self.assertEqual("pwd", done["input"])

    def test_openai_responses_passthrough_translator_normalizes_response_done_event(
        self,
    ) -> None:
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
            (
                "openai_responses",
                "openai_responses",
            ): OpenAIResponsesPassthroughTranslator,
            ("openai_responses", "claude_chat"): OpenAIResponsesClaudeTranslator,
            ("claude_chat", "openai_chat"): ClaudeChatTranslator,
            ("claude_chat", "openai_responses"): ClaudeOpenAIResponsesTranslator,
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
        return b"".join(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8") for chunk in chunks)

    def _build_service(
        self,
        *,
        llm_request_debug_enabled: bool = False,
        auth_group_manager: Any = None,
    ):
        app = Flask(__name__)
        ctx = AppContext(
            logger=FakeLogger(),
            config_manager=FakeConfigManager(llm_request_debug_enabled=llm_request_debug_enabled),
            root_path=Path(__file__).resolve().parents[1],
            flask_app=app,
        )
        return app, ProxyService(ctx, auth_group_manager=auth_group_manager)

    def test_provider_api_key_replaces_client_authorization_before_header_hook(
        self,
    ) -> None:
        app, service = self._build_service()
        hook = HeaderRecordingHook()
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            api_key="sk-provider",
            max_retries=1,
            hook=hook,
        )
        captured: dict[str, Any] = {}
        fake_response = FakeStreamResponse(
            [
                b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, body, args, kwargs
            captured["headers"] = dict(headers)
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
                    "model": "demo/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {
                    "Authorization": "Bearer user-token",
                    "X-API-Key": "user-token",
                },
            )
            stream_body = self._collect_response_body(response)

        headers = captured["headers"]
        authorization_headers = [key for key in headers if key.lower() == "authorization"]
        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual(["authorization"], authorization_headers)
        self.assertEqual("Bearer sk-provider", headers["authorization"])
        self.assertEqual("user-token", headers["X-API-Key"])
        self.assertEqual("after-auth", headers["x-hook-stage"])
        self.assertEqual("Bearer sk-provider", hook.headers[0]["authorization"])
        self.assertNotIn("Authorization", hook.headers[0])
        self.assertEqual("user-token", hook.headers[0]["X-API-Key"])
        self.assertIn(b"data: [DONE]", stream_body)

    def test_force_upstream_stream_aggregates_nonstream_downstream_response(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
            force_upstream_stream=True,
        )
        captured: dict[str, Any] = {}
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","created":123,"model":"gpt-4.1","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n',
                b'data: {"id":"chatcmpl_1","created":123,"model":"gpt-4.1","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n',
                b'data: {"id":"chatcmpl_1","created":123,"model":"gpt-4.1","choices":[{"index":0,"delta":{"content":" world"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, requested_stream, *args, **kwargs):
            del provider_arg, headers, args, kwargs
            captured["body"] = body
            captured["requested_stream"] = requested_stream
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]
        completed_meta: dict[str, Any] = {}

        with app.test_request_context("/v1/chat/completions"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "chat-upstream/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": False,
                },
                {},
                on_complete=completed_meta.update,
            )
            response_body = self._collect_response_body(response)

        payload = json.loads(response_body)
        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertFalse(response.is_streamed)
        self.assertTrue(captured["requested_stream"])
        self.assertTrue(captured["body"]["stream"])
        self.assertEqual("Hello world", payload["choices"][0]["message"]["content"])
        self.assertEqual("stop", payload["choices"][0]["finish_reason"])
        self.assertEqual(5, payload["usage"]["total_tokens"])
        self.assertEqual("gpt-4.1", completed_meta["response_model"])
        self.assertEqual(5, completed_meta["total_tokens"])
        self.assertTrue(fake_response.closed)

    def test_force_upstream_stream_keeps_explicit_downstream_streaming(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
            force_upstream_stream=True,
        )
        captured: dict[str, Any] = {}
        fake_response = FakeStreamResponse([b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', b"data: [DONE]\n\n"])

        def stub_open_upstream_response(provider_arg, headers, body, requested_stream, *args, **kwargs):
            del provider_arg, headers, args, kwargs
            captured["body"] = body
            captured["requested_stream"] = requested_stream
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
                    "model": "chat-upstream/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {},
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertTrue(response.is_streamed)
        self.assertTrue(captured["requested_stream"])
        self.assertTrue(captured["body"]["stream"])
        self.assertIn(b"data: [DONE]", stream_body)
        self.assertTrue(fake_response.closed)

    def test_force_upstream_stream_aggregates_openai_responses_source(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="responses-upstream",
            api="https://example.com/v1/responses",
            source_format="openai_responses",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
            force_upstream_stream=True,
        )
        captured: dict[str, Any] = {}
        fake_response = FakeStreamResponse(
            [
                b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","created_at":123,"model":"gpt-4.1"}}\n\n',
                b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","item_id":"msg_1","output_index":0,"delta":"Hello from Responses"}\n\n',
                b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1","created_at":123,"model":"gpt-4.1","usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}\n\n',
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, requested_stream, *args, **kwargs):
            del provider_arg, headers, args, kwargs
            captured["body"] = body
            captured["requested_stream"] = requested_stream
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
                    "stream": False,
                },
                {},
            )
            response_body = self._collect_response_body(response)

        payload = json.loads(response_body)
        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertTrue(captured["requested_stream"])
        self.assertTrue(captured["body"]["stream"])
        self.assertEqual("Hello from Responses", payload["choices"][0]["message"]["content"])
        self.assertEqual("resp_1", payload["id"])
        self.assertEqual(5, payload["usage"]["total_tokens"])
        self.assertTrue(fake_response.closed)

    def test_force_upstream_stream_aggregates_claude_source(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="claude-upstream",
            api="https://example.com/v1/messages",
            source_format="claude_chat",
            target_formats=("openai_chat",),
            model_list=("claude-sonnet-4-5",),
            max_retries=1,
            force_upstream_stream=True,
        )
        fake_response = FakeStreamResponse(
            [
                b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","model":"claude-sonnet-4-5","role":"assistant","content":[]}}\n\n',
                b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello from Claude"}}\n\n',
                b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":3,"output_tokens":2}}\n\n',
                b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
            ]
        )

        service._open_upstream_response = lambda *args, **kwargs: OpenedUpstreamResponse(  # type: ignore[method-assign]
            response=fake_response,
            status_code=200,
            content_type="text/event-stream",
            is_stream=True,
            stream_format="sse_json",
        )

        with app.test_request_context("/v1/chat/completions"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "claude-upstream/claude-sonnet-4-5",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": False,
                },
                {},
            )
            response_body = self._collect_response_body(response)

        payload = json.loads(response_body)
        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("Hello from Claude", payload["choices"][0]["message"]["content"])
        self.assertEqual(3, payload["usage"]["prompt_tokens"])
        self.assertEqual(2, payload["usage"]["completion_tokens"])
        self.assertTrue(fake_response.closed)

    def test_force_upstream_stream_preserves_nonstream_target_formats(self) -> None:
        cases = {
            "openai_chat": ("Hello", lambda payload: payload["choices"][0]["message"]["content"]),
            "openai_responses": (
                "Hello",
                lambda payload: next(
                    part["text"]
                    for item in payload["output"]
                    if item.get("type") == "message"
                    for part in item.get("content", [])
                    if part.get("type") == "output_text"
                ),
            ),
            "claude_chat": (
                "Hello",
                lambda payload: next(part["text"] for part in payload["content"] if part.get("type") == "text"),
            ),
        }

        for target_format, (expected_text, extract_text) in cases.items():
            with self.subTest(target_format=target_format):
                app, service = self._build_service()
                provider = LLMProvider(
                    name="chat-upstream",
                    api="https://example.com/v1/chat/completions",
                    source_format="openai_chat",
                    target_formats=(target_format,),
                    model_list=("gpt-4.1",),
                    max_retries=1,
                    force_upstream_stream=True,
                )
                fake_response = FakeStreamResponse(
                    [
                        b'data: {"id":"chatcmpl-1","created":123,"model":"gpt-4.1",'
                        b'"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":"stop"}],'
                        b'"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n',
                        b"data: [DONE]\n\n",
                    ]
                )
                service._open_upstream_response = lambda *args, **kwargs: OpenedUpstreamResponse(  # type: ignore[method-assign]
                    response=fake_response,
                    status_code=200,
                    content_type="text/event-stream",
                    is_stream=True,
                    stream_format="sse_json",
                )

                with app.test_request_context("/v1/chat/completions"):
                    response, status_code, failure_info = service.proxy_request(
                        provider,
                        {
                            "model": "chat-upstream/gpt-4.1",
                            "messages": [{"role": "user", "content": "Hello"}],
                            "stream": False,
                        },
                        {},
                        resolved_target_format=target_format,
                    )
                    response_body = self._collect_response_body(response)

                payload = json.loads(response_body)
                self.assertIsNone(failure_info)
                self.assertEqual(200, status_code)
                self.assertFalse(response.is_streamed)
                self.assertEqual(expected_text, extract_text(payload))
                self.assertTrue(fake_response.closed)

    def test_force_upstream_stream_translates_all_native_protocol_pairs(self) -> None:
        source_cases = {
            "openai_chat": {
                "api": "https://example.com/v1/chat/completions",
                "model": "gpt-4.1",
                "chunks": [
                    b'data: {"id":"chatcmpl-matrix","created":123,"model":"gpt-4.1",'
                    b'"choices":[{"index":0,"delta":{"content":"Hello matrix"},"finish_reason":"stop"}]}'
                    b"\n\n",
                    b"data: [DONE]\n\n",
                ],
            },
            "openai_responses": {
                "api": "https://example.com/v1/responses",
                "model": "gpt-5-codex",
                "chunks": [
                    b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp-matrix","created_at":123,"model":"gpt-5-codex"}}\n\n',
                    b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","item_id":"msg-matrix","output_index":0,"delta":"Hello matrix"}\n\n',
                    b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp-matrix","created_at":123,"model":"gpt-5-codex","output":[],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n',
                ],
            },
            "claude_chat": {
                "api": "https://example.com/v1/messages",
                "model": "claude-sonnet-4-5",
                "chunks": [
                    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg-matrix","model":"claude-sonnet-4-5","role":"assistant","content":[]}}\n\n',
                    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
                    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello matrix"}}\n\n',
                    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
                    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":1,"output_tokens":2}}\n\n',
                    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
                ],
            },
        }
        target_bodies = {
            "openai_chat": {"messages": [{"role": "user", "content": "Hello"}]},
            "openai_responses": {"input": "Hello"},
            "claude_chat": {"max_tokens": 256, "messages": [{"role": "user", "content": "Hello"}]},
        }

        for source_format, source_case in source_cases.items():
            for target_format, target_body in target_bodies.items():
                with self.subTest(source_format=source_format, target_format=target_format):
                    app, service = self._build_service()
                    provider = LLMProvider(
                        name="matrix-upstream",
                        api=source_case["api"],
                        source_format=source_format,
                        target_formats=(target_format,),
                        model_list=(source_case["model"],),
                        max_retries=1,
                        force_upstream_stream=True,
                    )
                    fake_response = FakeStreamResponse(source_case["chunks"])
                    service._open_upstream_response = lambda *args, response=fake_response, **kwargs: (
                        OpenedUpstreamResponse(  # type: ignore[method-assign]
                            response=response,
                            status_code=200,
                            content_type="text/event-stream",
                            is_stream=True,
                            stream_format="sse_json",
                        )
                    )
                    request_data = {
                        "model": f"matrix-upstream/{source_case['model']}",
                        **target_body,
                        "stream": False,
                    }

                    with app.test_request_context("/v1/chat/completions"):
                        response, status_code, failure_info = service.proxy_request(
                            provider,
                            request_data,
                            {},
                            resolved_target_format=target_format,
                        )
                        response_body = self._collect_response_body(response)

                    payload = json.loads(response_body)
                    self.assertIsNone(failure_info)
                    self.assertEqual(200, status_code)
                    self.assertFalse(response.is_streamed)
                    if target_format == "openai_chat":
                        text = payload["choices"][0]["message"]["content"]
                    elif target_format == "openai_responses":
                        text = next(
                            part["text"]
                            for item in payload["output"]
                            if item.get("type") == "message"
                            for part in item.get("content", [])
                            if part.get("type") == "output_text"
                        )
                    else:
                        text = next(part["text"] for part in payload["content"] if part.get("type") == "text")
                    self.assertEqual("Hello matrix", text)
                    self.assertTrue(fake_response.closed)

    def test_force_upstream_stream_returns_target_formatted_error_event(self) -> None:
        expected_error_types = {
            "openai_chat": "rate_limit_error",
            "openai_responses": "rate_limit_error",
            "claude_chat": "rate_limit_error",
        }

        for target_format, expected_error_type in expected_error_types.items():
            with self.subTest(target_format=target_format):
                auth_group_manager = RotatingAuthGroupManager()
                app, service = self._build_service(auth_group_manager=auth_group_manager)
                provider = LLMProvider(
                    name="chat-upstream",
                    api="https://example.com/v1/chat/completions",
                    source_format="openai_chat",
                    target_formats=(target_format,),
                    auth_group="pool",
                    model_list=("gpt-4.1",),
                    max_retries=1,
                    force_upstream_stream=True,
                )
                fake_response = FakeStreamResponse(
                    [
                        b'data: {"error":{"message":"quota exceeded","type":"rate_limit_error",'
                        b'"code":"rate_limit_exceeded"}}\n\n'
                    ]
                )
                fake_response.headers["Retry-After"] = "17"
                service._open_upstream_response = lambda *args, **kwargs: OpenedUpstreamResponse(  # type: ignore[method-assign]
                    response=fake_response,
                    status_code=200,
                    content_type="text/event-stream",
                    is_stream=True,
                    stream_format="sse_json",
                )
                completed: list[dict[str, Any]] = []

                with app.test_request_context("/v1/chat/completions"):
                    response, status_code, failure_info = service.proxy_request(
                        provider,
                        {
                            "model": "chat-upstream/gpt-4.1",
                            "messages": [{"role": "user", "content": "Hello"}],
                            "stream": False,
                        },
                        {},
                        on_complete=completed.append,
                        resolved_target_format=target_format,
                    )
                    response_body = self._collect_response_body(response)

                payload = json.loads(response_body)
                self.assertIsNone(failure_info)
                self.assertEqual(502, status_code)
                self.assertEqual([], completed)
                if target_format == "claude_chat":
                    self.assertEqual("error", payload["type"])
                    self.assertEqual(expected_error_type, payload["error"]["type"])
                else:
                    self.assertEqual(expected_error_type, payload["error"]["type"])
                self.assertEqual("quota exceeded", payload["error"]["message"])
                self.assertEqual("key-a", auth_group_manager.finish_calls[0][0])
                self.assertEqual(429, auth_group_manager.finish_calls[0][1]["status_code"])
                self.assertEqual(
                    "17",
                    auth_group_manager.finish_calls[0][1]["response_headers"]["Retry-After"],
                )
                self.assertTrue(fake_response.closed)

    def test_force_upstream_stream_retries_with_next_auth_entry_before_returning(self) -> None:
        auth_group_manager = RotatingAuthGroupManager()
        app, service = self._build_service(auth_group_manager=auth_group_manager)
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            auth_group="pool",
            model_list=("gpt-4.1",),
            max_retries=2,
            force_upstream_stream=True,
        )
        upstream_responses = [
            FakeStreamResponse(
                [
                    b'data: {"error":{"message":"quota exceeded","type":"rate_limit_error",'
                    b'"code":"rate_limit_exceeded"}}\n\n'
                ]
            ),
            FakeStreamResponse(
                [
                    b'data: {"id":"chatcmpl-ok","model":"gpt-4.1",'
                    b'"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n',
                    b"data: [DONE]\n\n",
                ]
            ),
        ]
        authorization_headers: list[str] = []

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, body, args, kwargs
            authorization_headers.append(
                next(value for key, value in headers.items() if key.lower() == "authorization")
            )
            response = upstream_responses[len(authorization_headers) - 1]
            return OpenedUpstreamResponse(
                response=response,
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
                    "model": "chat-upstream/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": False,
                },
                {},
            )
            response_body = self._collect_response_body(response)

        payload = json.loads(response_body)
        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("ok", payload["choices"][0]["message"]["content"])
        self.assertEqual(["Bearer key-a", "Bearer key-b"], authorization_headers)
        self.assertEqual(["pool", "pool"], auth_group_manager.acquire_calls)
        self.assertEqual("key-a", auth_group_manager.finish_calls[0][0])
        self.assertEqual(429, auth_group_manager.finish_calls[0][1]["status_code"])
        self.assertEqual("key-b", auth_group_manager.finish_calls[1][0])
        self.assertEqual(200, auth_group_manager.finish_calls[1][1]["status_code"])
        self.assertTrue(all(item.closed for item in upstream_responses))

    def test_force_upstream_stream_handles_response_guard_abort_without_success_callback(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_responses",),
            model_list=("gpt-4.1",),
            max_retries=1,
            force_upstream_stream=True,
            hook=AbortOnResponseHook(
                message="blocked by response guard",
                status_code=451,
                error_type="hook_blocked",
            ),
        )
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl-1","created":123,"model":"gpt-4.1",'
                b'"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":"stop"}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )
        service._open_upstream_response = lambda *args, **kwargs: OpenedUpstreamResponse(  # type: ignore[method-assign]
            response=fake_response,
            status_code=200,
            content_type="text/event-stream",
            is_stream=True,
            stream_format="sse_json",
        )
        completed: list[dict[str, Any]] = []

        with app.test_request_context("/v1/responses"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "chat-upstream/gpt-4.1",
                    "input": "Hello",
                    "stream": False,
                },
                {},
                on_complete=completed.append,
                resolved_target_format="openai_responses",
            )
            response_body = self._collect_response_body(response)

        payload = json.loads(response_body)
        self.assertIsNone(failure_info)
        self.assertEqual(451, status_code)
        self.assertEqual([], completed)
        self.assertEqual("hook_blocked", payload["error"]["type"])
        self.assertEqual("blocked by response guard", payload["error"]["message"])
        self.assertTrue(fake_response.closed)

    def test_provider_without_api_key_drops_client_authorization_header(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        captured: dict[str, Any] = {}
        fake_response = FakeStreamResponse(
            [
                b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, body, args, kwargs
            captured["headers"] = dict(headers)
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
                    "model": "demo/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {
                    "Authorization": "Bearer user-token",
                    "X-API-Key": "user-token",
                },
            )
            self._collect_response_body(response)

        headers = captured["headers"]
        authorization_headers = [key for key in headers if key.lower() == "authorization"]
        api_key_headers = [key for key in headers if key.lower() == "x-api-key"]
        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual([], authorization_headers)
        self.assertEqual(["X-API-Key"], api_key_headers)
        self.assertEqual("user-token", headers["X-API-Key"])

    def test_nonstream_response_filters_upstream_content_type_before_setting_downstream_type(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        fake_response = FakeStreamResponse(
            [
                (
                    b'{"id":"chatcmpl_1","object":"chat.completion","model":"gpt-4.1",'
                    b'"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},'
                    b'"finish_reason":"stop"}]}'
                )
            ],
            content_type="application/json",
        )
        fake_response.headers = {
            "content-type": "application/json",
            "X-Upstream-Trace": "trace-1",
        }

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, body, args, kwargs
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="application/json",
                is_stream=False,
                stream_format="nonstream",
            )

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

        with app.test_request_context("/v1/chat/completions"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "demo/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": False,
                },
                {},
            )

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIsNotNone(response)
        self.assertEqual(["application/json; charset=utf-8"], response.headers.getlist("Content-Type"))
        self.assertEqual("trace-1", response.headers["X-Upstream-Trace"])

    def test_build_upstream_request_applies_request_guard_to_translated_body(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            hook=RewriteRequestModelHook("rewritten-model"),
        )

        built_request = build_upstream_request(
            root_path=Path(__file__).resolve().parents[1],
            logger=FakeLogger(),
            provider=provider,
            request_model="demo/original-model",
            upstream_model="original-model",
            provider_target_format="openai_chat",
            request_data={
                "model": "original-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
            request_headers={"content-type": "application/json"},
            translator=OpenAIChatTranslator(),
            attempt=0,
            previous_status_code=None,
            previous_error_type=None,
            auth_group_name=None,
            auth_entry_id=None,
        )

        self.assertEqual("original-model", built_request.original_body["model"])
        self.assertEqual("rewritten-model", built_request.translated_body["model"])
        self.assertEqual("rewritten-model", built_request.request_ctx.upstream_model)

    def test_build_upstream_request_passes_translated_body_to_request_guard(self) -> None:
        hook = RequestBodyRecordingHook()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("claude_chat",),
            hook=hook,
        )

        built_request = build_upstream_request(
            root_path=Path(__file__).resolve().parents[1],
            logger=FakeLogger(),
            provider=provider,
            request_model="chat-upstream/gpt-4.1",
            upstream_model="gpt-4.1",
            provider_target_format="claude_chat",
            request_data={
                "model": "chat-upstream/gpt-4.1",
                "max_tokens": 256,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Hello"}],
                    },
                ],
                "stream": True,
            },
            request_headers={"content-type": "application/json"},
            translator=OpenAIChatClaudeTranslator(),
            attempt=0,
            previous_status_code=None,
            previous_error_type=None,
            auth_group_name=None,
            auth_entry_id=None,
        )

        self.assertEqual("chat-upstream/gpt-4.1", built_request.original_body["model"])
        self.assertEqual("gpt-4.1", hook.bodies[0]["model"])
        self.assertEqual("Hello", hook.bodies[0]["messages"][0]["content"])
        self.assertEqual(hook.bodies[0]["messages"], built_request.translated_body["messages"])

    def test_build_upstream_request_uses_stream_rewritten_by_request_guard(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            hook=RewriteRequestStreamHook(True),
        )

        built_request = build_upstream_request(
            root_path=Path(__file__).resolve().parents[1],
            logger=FakeLogger(),
            provider=provider,
            request_model="demo/gpt-4.1",
            upstream_model="gpt-4.1",
            provider_target_format="openai_chat",
            request_data={
                "model": "gpt-4.1",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
            request_headers={"content-type": "application/json"},
            translator=OpenAIChatTranslator(),
            attempt=0,
            previous_status_code=None,
            previous_error_type=None,
            auth_group_name=None,
            auth_entry_id=None,
        )

        self.assertTrue(built_request.request_ctx.stream)
        self.assertTrue(built_request.translated_body["stream"])
        self.assertTrue(built_request.translated_body["stream_options"]["include_usage"])

    def test_build_upstream_request_forces_stream_after_request_guard(self) -> None:
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            force_upstream_stream=True,
        )

        built_request = build_upstream_request(
            root_path=Path(__file__).resolve().parents[1],
            logger=FakeLogger(),
            provider=provider,
            request_model="demo/gpt-4.1",
            upstream_model="gpt-4.1",
            provider_target_format="openai_chat",
            request_data={
                "model": "demo/gpt-4.1",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
            request_headers={"content-type": "application/json"},
            translator=OpenAIChatTranslator(),
            attempt=0,
            previous_status_code=None,
            previous_error_type=None,
            auth_group_name=None,
            auth_entry_id=None,
            force_upstream_stream=True,
        )

        self.assertFalse(built_request.original_body["stream"])
        self.assertTrue(built_request.translated_body["stream"])
        self.assertTrue(built_request.request_ctx.stream)
        self.assertTrue(built_request.translated_body["stream_options"]["include_usage"])

    def test_build_upstream_request_keeps_provider_like_upstream_model_id(self) -> None:
        provider = LLMProvider(
            name="aliyun",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
        )

        built_request = build_upstream_request(
            root_path=Path(__file__).resolve().parents[1],
            logger=FakeLogger(),
            provider=provider,
            request_model="aliyun/aliyun/deepseek",
            upstream_model="aliyun/deepseek",
            provider_target_format="openai_chat",
            request_data={
                "model": "aliyun/deepseek",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
            request_headers={"content-type": "application/json"},
            translator=OpenAIChatTranslator(),
            attempt=0,
            previous_status_code=None,
            previous_error_type=None,
            auth_group_name=None,
            auth_entry_id=None,
        )

        self.assertEqual("aliyun/deepseek", built_request.original_body["model"])
        self.assertEqual("aliyun/deepseek", built_request.translated_body["model"])
        self.assertEqual("aliyun/deepseek", built_request.request_ctx.upstream_model)

    def test_claude_chat_provider_resigns_existing_cch(self) -> None:
        provider = LLMProvider(
            name="claude-upstream",
            api="https://example.com/v1/messages",
            source_format="claude_chat",
            target_formats=("claude_chat",),
        )
        original_billing_header = "x-anthropic-billing-header: user=demo; cch=11111; token=demo"

        built_request = build_upstream_request(
            root_path=Path(__file__).resolve().parents[1],
            logger=FakeLogger(),
            provider=provider,
            request_model="claude-upstream/claude-sonnet-4-5",
            upstream_model="claude-sonnet-4-5",
            provider_target_format="claude_chat",
            request_data={
                "model": "claude-upstream/claude-sonnet-4-5",
                "system": [{"type": "text", "text": original_billing_header}],
                "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
                "stream": True,
            },
            request_headers={"content-type": "application/json"},
            translator=ClaudePassthroughTranslator(),
            attempt=0,
            previous_status_code=None,
            previous_error_type=None,
            auth_group_name=None,
            auth_entry_id=None,
        )

        signed_billing_header = built_request.translated_body["system"][0]["text"]
        self.assertNotEqual(original_billing_header, signed_billing_header)
        self.assertNotIn("cch=11111;", signed_billing_header)
        self.assertRegex(signed_billing_header, r"\bcch=[0-9a-f]{5};")

    def test_proxy_service_sends_model_rewritten_by_request_guard_upstream(
        self,
    ) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("original-model", "rewritten-model"),
            max_retries=1,
            hook=RewriteRequestModelHook("rewritten-model"),
        )
        captured: dict[str, Any] = {}
        fake_response = FakeStreamResponse(
            [
                b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
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
                    "model": "demo/original-model",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {},
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("rewritten-model", captured["body"]["model"])
        self.assertIn(b"data: [DONE]", stream_body)
        self.assertTrue(fake_response.closed)

    def test_proxy_service_drops_empty_openai_chat_delta_tool_calls(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("reasoning-model",),
            max_retries=1,
        )
        fake_response = FakeStreamResponse(
            [
                (
                    b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"reasoning-model",'
                    b'"choices":[{"index":0,"delta":{"content":null,"reasoning_content":"The",'
                    b'"role":"assistant","tool_calls":[]},"finish_reason":null}]}\n\n'
                ),
                (
                    b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","model":"reasoning-model",'
                    b'"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1",'
                    b'"type":"function","function":{"name":"lookup","arguments":"{}"}}]},'
                    b'"finish_reason":null}]}\n\n'
                ),
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
                    "model": "demo/reasoning-model",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {},
            )
            stream_body = self._collect_response_body(response)

        events = list(decode_stream_events([stream_body], "sse_json"))
        delta = events[0].payload["choices"][0]["delta"]
        tool_delta = events[1].payload["choices"][0]["delta"]

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("The", delta["reasoning_content"])
        self.assertEqual("assistant", delta["role"])
        self.assertNotIn("tool_calls", delta)
        self.assertEqual("call_1", tool_delta["tool_calls"][0]["id"])
        self.assertIn(b"data: [DONE]", stream_body)
        self.assertTrue(fake_response.closed)

    def test_proxy_service_translates_openai_responses_stream_to_openai_chat(
        self,
    ) -> None:
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
                    "store": False,
                    "include": ["reasoning.encrypted_content"],
                    "parallel_tool_calls": True,
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
        self.assertFalse(captured["body"]["store"])
        self.assertEqual(["reasoning.encrypted_content"], captured["body"]["include"])
        self.assertTrue(captured["body"]["parallel_tool_calls"])
        self.assertIn(b"Hello from Responses", stream_body)
        self.assertIn(b'"prompt_tokens": 3', stream_body)
        self.assertIn(b'"completion_tokens": 2', stream_body)
        self.assertIn(b'"total_tokens": 5', stream_body)
        self.assertIn(b"data: [DONE]", stream_body)
        self.assertEqual(1, stream_body.count(b"data: [DONE]\n\n"))
        self.assertTrue(fake_response.closed)

    def test_proxy_service_collects_usage_when_openai_chat_usage_chunk_is_suppressed(
        self,
    ) -> None:
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

    def test_proxy_service_skips_trace_buffering_when_debug_logging_disabled(
        self,
    ) -> None:
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

    def test_proxy_service_buffers_upstream_stream_trace_when_debug_logging_enabled(
        self,
    ) -> None:
        app, service = self._build_service(llm_request_debug_enabled=True)
        provider = LLMProvider(
            name="responses-upstream",
            api="https://example.com/v1/responses",
            transport="http",
            source_format="openai_responses",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        upstream_chunks = [
            b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","created_at":123,"model":"gpt-4.1"}}\n\n',
            b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"Hello"}\n\n',
            b"data: [DONE]\n\n",
        ]
        fake_response = FakeStreamResponse(upstream_chunks)

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            del provider_arg, headers, body, args, kwargs
            return OpenedUpstreamResponse(
                response=fake_response,
                status_code=200,
                content_type="text/event-stream",
                is_stream=True,
                stream_format="sse_json",
            )

        trace_entries: list[dict[str, Any]] = []

        def record_trace_entry(**kwargs):
            trace_entries.append(kwargs)

        service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]
        service._trace.log_entry = record_trace_entry  # type: ignore[method-assign]

        with app.test_request_context("/v1/chat/completions"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "responses-upstream/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {},
                trace_id="trace-enabled",
            )
            stream_body = self._collect_response_body(response)

        upstream_trace = next(entry for entry in trace_entries if entry["stage"] == "upstream_response")
        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIn(b"Hello", stream_body)
        self.assertEqual(b"".join(upstream_chunks), upstream_trace["payload"])

    def test_stream_transport_error_before_first_downstream_chunk_retries(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=2,
        )
        failed_response = FailingStreamResponse(
            [
                b'data: {"id":"chatcmpl_usage","object":"chat.completion.chunk","created":123,'
                b'"model":"gpt-4.1","choices":[],"usage":{"prompt_tokens":1,'
                b'"completion_tokens":0,"total_tokens":1}}\n\n'
            ],
            fail_after_chunks=1,
        )
        success_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","created":123,'
                b'"model":"gpt-4.1","choices":[{"index":0,"delta":{"content":"retried"},'
                b'"finish_reason":null}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )
        responses = iter((failed_response, success_response))
        open_calls = 0

        def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
            nonlocal open_calls
            del provider_arg, headers, body, args, kwargs
            open_calls += 1
            response = next(responses)
            return OpenedUpstreamResponse(
                response=response,
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
                    "model": "chat-upstream/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {},
            )
            self.assertEqual(2, open_calls)
            self.assertTrue(failed_response.closed)
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIn(b"retried", stream_body)
        self.assertNotIn(b"upstream_stream_error", stream_body)
        self.assertTrue(success_response.closed)

    def test_stream_transport_error_after_commit_emits_protocol_error(self) -> None:
        upstream_chunk = (
            b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","created":123,'
            b'"model":"gpt-4.1","choices":[{"index":0,"delta":{"content":"partial"},'
            b'"finish_reason":null}]}\n\n'
        )
        cases = (
            (
                "openai_chat",
                "/v1/chat/completions",
                {
                    "model": "chat-upstream/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                b"upstream_stream_error",
                True,
            ),
            (
                "openai_responses",
                "/v1/responses",
                {
                    "model": "chat-upstream/gpt-4.1",
                    "input": "Hello",
                    "stream": True,
                },
                b"event: response.failed",
                False,
            ),
            (
                "claude_chat",
                "/v1/messages",
                {
                    "model": "chat-upstream/gpt-4.1",
                    "max_tokens": 256,
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                b"event: error",
                False,
            ),
        )

        for target_format, route, request_body, expected_error, expects_done in cases:
            with self.subTest(target_format=target_format):
                app, service = self._build_service()
                provider = LLMProvider(
                    name="chat-upstream",
                    api="https://example.com/v1/chat/completions",
                    transport="http",
                    source_format="openai_chat",
                    target_formats=(target_format,),
                    model_list=("gpt-4.1",),
                    max_retries=3,
                )
                failed_response = FailingStreamResponse([upstream_chunk], fail_after_chunks=1)
                open_calls = 0
                completed_meta: list[dict[str, Any]] = []

                def stub_open_upstream_response(provider_arg, headers, body, *args, **kwargs):
                    nonlocal open_calls
                    del provider_arg, headers, body, args, kwargs
                    open_calls += 1
                    return OpenedUpstreamResponse(
                        response=failed_response,
                        status_code=200,
                        content_type="text/event-stream",
                        is_stream=True,
                        stream_format="sse_json",
                    )

                service._open_upstream_response = stub_open_upstream_response  # type: ignore[method-assign]

                with app.test_request_context(route):
                    response, status_code, failure_info = service.proxy_request(
                        provider,
                        request_body,
                        {},
                        on_complete=completed_meta.append,
                        resolved_target_format=target_format,
                    )
                    stream_body = self._collect_response_body(response)

                self.assertIsNone(failure_info)
                self.assertEqual(200, status_code)
                self.assertEqual(1, open_calls)
                self.assertIn(b"partial", stream_body)
                self.assertIn(expected_error, stream_body)
                self.assertEqual(1 if expects_done else 0, stream_body.count(b"data: [DONE]"))
                self.assertEqual(1, len(completed_meta))
                self.assertEqual("unknown", completed_meta[0]["usage_status"])
                self.assertTrue(failed_response.closed)

    def test_stream_client_cancel_records_unknown_usage(self) -> None:
        app, service = self._build_service(llm_request_debug_enabled=True)
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        fake_response = FakeStreamResponse(
            [
                b'data: {"model":"gpt-4.1","choices":[{"delta":{"content":"first"}}]}\n\n',
                b'data: {"model":"gpt-4.1","choices":[{"delta":{"content":"second"}}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )
        service._open_upstream_response = lambda *args, **kwargs: OpenedUpstreamResponse(  # type: ignore[method-assign]
            response=fake_response,
            status_code=200,
            content_type="text/event-stream",
            is_stream=True,
            stream_format="sse_json",
        )
        completed_meta: list[dict[str, Any]] = []
        trace_entries: list[dict[str, Any]] = []
        service._trace.log_entry = lambda **kwargs: trace_entries.append(kwargs)  # type: ignore[method-assign]

        with app.test_request_context("/v1/chat/completions"):
            response, _, _ = service.proxy_request(
                provider,
                {
                    "model": "chat-upstream/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {},
                on_complete=completed_meta.append,
                trace_id="cancelled-trace",
            )
            assert response is not None
            first_chunk = next(iter(response.response))
            response.close()

        self.assertIn(b"first", first_chunk)
        self.assertEqual(1, len(completed_meta))
        self.assertEqual("unknown", completed_meta[0]["usage_status"])
        self.assertTrue(fake_response.closed)
        response_traces = [
            entry for entry in trace_entries if entry["stage"] in {"upstream_response", "downstream_response"}
        ]
        self.assertEqual(2, len(response_traces))
        self.assertTrue(all(entry["completed"] is False for entry in response_traces))
        self.assertTrue(all(entry["error_type"] == "client_cancelled" for entry in response_traces))

    def test_stream_close_before_first_iteration_closes_prefetched_upstream(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        fake_response = FakeStreamResponse([b'data: {"model":"gpt-4.1","choices":[{"delta":{"content":"first"}}]}\n\n'])
        service._open_upstream_response = lambda *args, **kwargs: OpenedUpstreamResponse(  # type: ignore[method-assign]
            response=fake_response,
            status_code=200,
            content_type="text/event-stream",
            is_stream=True,
            stream_format="sse_json",
        )
        completed_meta: list[dict[str, Any]] = []

        with app.test_request_context("/v1/chat/completions"):
            response, _, _ = service.proxy_request(
                provider,
                {
                    "model": "chat-upstream/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {},
                on_complete=completed_meta.append,
            )
            assert response is not None
            response.close()

        self.assertEqual([], completed_meta)
        self.assertTrue(fake_response.closed)

    def test_stream_framing_error_after_terminal_keeps_success(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        fake_response = FailingStreamResponse(
            [
                b'data: {"model":"gpt-4.1","choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\n',
                b"data: [DONE]\n\n",
            ],
            fail_after_chunks=2,
        )
        service._open_upstream_response = lambda *args, **kwargs: OpenedUpstreamResponse(  # type: ignore[method-assign]
            response=fake_response,
            status_code=200,
            content_type="text/event-stream",
            is_stream=True,
            stream_format="sse_json",
        )
        completed_meta: list[dict[str, Any]] = []

        with app.test_request_context("/v1/chat/completions"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "chat-upstream/gpt-4.1",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                {},
                on_complete=completed_meta.append,
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual(1, stream_body.count(b"data: [DONE]"))
        self.assertNotIn(b"upstream_stream_error", stream_body)
        self.assertEqual(1, len(completed_meta))
        self.assertTrue(fake_response.closed)

    def test_stream_terminal_encoding_error_emits_processing_failure(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            transport="http",
            source_format="openai_chat",
            target_formats=("openai_responses",),
            model_list=("gpt-4.1",),
            max_retries=1,
            hook=UnserializableTerminalHook(),
        )
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","created":123,'
                b'"model":"gpt-4.1","choices":[{"index":0,"delta":{"content":"hi"},'
                b'"finish_reason":null}]}\n\n',
                b'data: {"id":"chatcmpl_1","object":"chat.completion.chunk","created":123,'
                b'"model":"gpt-4.1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )
        service._open_upstream_response = lambda *args, **kwargs: OpenedUpstreamResponse(  # type: ignore[method-assign]
            response=fake_response,
            status_code=200,
            content_type="text/event-stream",
            is_stream=True,
            stream_format="sse_json",
        )
        completed_meta: list[dict[str, Any]] = []

        with app.test_request_context("/v1/responses"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "chat-upstream/gpt-4.1",
                    "input": "Hello",
                    "stream": True,
                },
                {},
                on_complete=completed_meta.append,
                resolved_target_format="openai_responses",
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertIn(b"event: response.failed", stream_body)
        self.assertIn(b"upstream_stream_processing_error", stream_body)
        self.assertNotIn(b"event: response.completed", stream_body)
        self.assertEqual(1, len(completed_meta))
        self.assertEqual("unknown", completed_meta[0]["usage_status"])
        self.assertTrue(fake_response.closed)

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

    def test_proxy_service_translates_openai_chat_stream_to_openai_responses(
        self,
    ) -> None:
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

    def test_proxy_service_translates_openai_chat_stream_to_claude_messages(
        self,
    ) -> None:
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
            del provider_arg, args, kwargs
            captured["headers"] = headers
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
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "Hello"}],
                        },
                    ],
                    "stream": True,
                },
                {"Accept-Encoding": "gzip, br, zstd"},
                forward_stream_usage=True,
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        captured_accept_encoding = next(
            value for name, value in captured["headers"].items() if name.lower() == "accept-encoding"
        )
        self.assertEqual("identity", captured_accept_encoding)
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

    def test_proxy_service_finalizes_openai_chat_to_claude_stream_without_upstream_done(
        self,
    ) -> None:
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

        with app.test_request_context("/v1/messages"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {
                    "model": "chat-upstream/gpt-4.1",
                    "max_tokens": 256,
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "Hello"}],
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
        self.assertIn(b"event: content_block_stop", stream_body)
        self.assertIn(b"event: message_delta", stream_body)
        self.assertIn(b"event: message_stop", stream_body)
        self.assertNotIn(b"[DONE]", stream_body)
        self.assertTrue(fake_response.closed)

    def test_proxy_service_translates_openai_responses_stream_to_claude_messages_without_upstream_done_and_preserves_response_model(
        self,
    ) -> None:
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
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "Hello"}],
                        },
                    ],
                    "store": False,
                    "include": ["reasoning.encrypted_content"],
                    "parallel_tool_calls": True,
                    "stream": True,
                },
                {},
                forward_stream_usage=True,
                on_complete=captured_meta.update,
            )
            stream_body = self._collect_response_body(response)

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual("user", captured["body"]["input"][0]["role"])
        self.assertEqual("Hello", captured["body"]["input"][0]["content"][0]["text"])
        self.assertFalse(captured["body"]["store"])
        self.assertEqual(["reasoning.encrypted_content"], captured["body"]["include"])
        self.assertTrue(captured["body"]["parallel_tool_calls"])
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

    def test_usage_is_collected_before_response_guard(self) -> None:
        class RemoveUsageHook(BaseHook):
            def response_guard(self, ctx: Any, body: Any) -> Any:
                del ctx
                if isinstance(body, dict):
                    changed = dict(body)
                    changed.pop("usage", None)
                    return changed
                return body

        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
            hook=RemoveUsageHook(),
        )
        fake_response = FakeStreamResponse(
            [
                b'{"id":"chatcmpl-guard","model":"gpt-4.1","choices":[],'
                b'"usage":{"prompt_tokens":11,"completion_tokens":3}}',
            ]
        )
        service._open_upstream_response = lambda *args, **kwargs: OpenedUpstreamResponse(  # type: ignore[method-assign]
            response=fake_response,
            status_code=200,
            content_type="application/json",
            is_stream=False,
            stream_format="sse_json",
        )
        captured: list[dict[str, Any]] = []

        with app.test_request_context("/v1/chat/completions"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {"model": "chat-upstream/gpt-4.1", "messages": [], "stream": False},
                {},
                on_complete=captured.append,
            )
            response.get_data()

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual(
            {"usage_status": "known", "prompt_tokens": 11, "completion_tokens": 3},
            {key: captured[0][key] for key in ("usage_status", "prompt_tokens", "completion_tokens")},
        )

    def test_cancelled_stream_records_partial_usage(self) -> None:
        app, service = self._build_service()
        provider = LLMProvider(
            name="chat-upstream",
            api="https://example.com/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("gpt-4.1",),
            max_retries=1,
        )
        fake_response = FakeStreamResponse(
            [
                b'data: {"id":"chatcmpl-cancel","model":"gpt-4.1","choices":[{"delta":{"content":"x"}}],'
                b'"usage":{"prompt_tokens":5}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )
        service._open_upstream_response = lambda *args, **kwargs: OpenedUpstreamResponse(  # type: ignore[method-assign]
            response=fake_response,
            status_code=200,
            content_type="text/event-stream",
            is_stream=True,
            stream_format="sse_json",
        )
        captured: list[dict[str, Any]] = []

        with app.test_request_context("/v1/chat/completions"):
            response, status_code, failure_info = service.proxy_request(
                provider,
                {"model": "chat-upstream/gpt-4.1", "messages": [], "stream": True},
                {},
                on_complete=captured.append,
            )
            iterator = iter(response.response)
            next(iterator)
            response.close()

        self.assertIsNone(failure_info)
        self.assertEqual(200, status_code)
        self.assertEqual(1, len(captured))
        self.assertEqual(5, captured[0]["prompt_tokens"])
        self.assertEqual("partial", captured[0]["usage_status"])
        self.assertTrue(fake_response.closed)


if __name__ == "__main__":
    unittest.main()
