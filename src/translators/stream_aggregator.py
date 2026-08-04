#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按上游协议聚合流式事件并生成原生非流式响应。"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Iterable
from typing import Any

from ..proxy_core import DownstreamChunk, StreamEvent
from .reasoning_utils import extract_openai_reasoning_delta
from .registry import Translator


class StreamAggregationError(RuntimeError):
    """上游流返回了无法转换为完整响应的错误事件。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        error_payload = payload.get("error")
        if not isinstance(error_payload, dict):
            response = payload.get("response")
            if isinstance(response, dict):
                error_payload = response.get("error")
        if not isinstance(error_payload, dict):
            error_payload = {
                "message": "Upstream stream failed",
                "type": "upstream_stream_error",
                "code": "upstream_stream_error",
            }
        self.payload = dict(payload)
        self.error_payload = dict(error_payload)
        self.message = str(self.error_payload.get("message") or "Upstream stream failed")
        self.error_type = str(self.error_payload.get("type") or "upstream_stream_error")
        self.error_code = self.error_payload.get("code")
        super().__init__(self.message)

    @classmethod
    def from_message(
        cls,
        message: str,
        *,
        error_type: str = "upstream_stream_processing_error",
        error_code: str | None = None,
    ) -> "StreamAggregationError":
        return cls(
            {
                "error": {
                    "message": message,
                    "type": error_type,
                    "code": error_code or error_type,
                }
            }
        )


def infer_stream_aggregation_status_code(exc: StreamAggregationError) -> int:
    """根据流内错误标识推断本次上游尝试的状态码。"""
    identifiers = {
        str(value).strip().lower().replace("-", "_").replace(" ", "_")
        for value in (exc.error_type, exc.error_code)
        if value not in (None, "")
    }
    compact_identifiers = {identifier.replace("_", "") for identifier in identifiers}

    def contains(*markers: str) -> bool:
        return any(marker in identifier for identifier in identifiers for marker in markers) or any(
            marker.replace("_", "") in identifier for identifier in compact_identifiers for marker in markers
        )

    if "429" in identifiers or contains(
        "rate_limit",
        "quota",
        "usage_limit",
        "too_many_requests",
        "billing_hard_limit",
    ):
        return 429
    if "403" in identifiers or contains(
        "permission",
        "forbidden",
        "access_denied",
        "not_allowed",
        "authorization",
        "account_deactivated",
        "organization_disabled",
    ):
        return 403
    if "401" in identifiers or contains(
        "authentication",
        "unauthorized",
        "unauthenticated",
        "invalid_api_key",
        "api_key_invalid",
        "invalid_token",
        "token_expired",
        "expired_token",
        "invalid_grant",
        "refresh_token_reused",
        "token_refresh_failed",
        "missing_access_token",
    ):
        return 401
    return 502


def aggregate_stream_to_native_response(
    *,
    source_format: str,
    model_name: str,
    events: Iterable[StreamEvent],
) -> dict[str, Any]:
    """先按上游协议合并流，再交给 response translator 转换。"""
    normalized_source = str(source_format or "").strip().lower()
    if normalized_source == "openai_chat":
        return _aggregate_openai_chat_response(model_name, events)
    if normalized_source == "openai_responses":
        return _aggregate_openai_responses_response(model_name, events)
    if normalized_source == "claude_chat":
        return _aggregate_claude_response(model_name, events)
    raise ValueError(f"Unsupported native stream aggregation source format: {source_format}")


def _aggregate_openai_chat_response(model_name: str, events: Iterable[StreamEvent]) -> dict[str, Any]:
    accumulator = _OpenAIChatResponseAccumulator(model_name)
    saw_done_event = False
    for event in events:
        if event.kind == "done":
            saw_done_event = True
            break
        if event.kind == "text" and isinstance(event.payload, str) and event.payload.strip():
            raise StreamAggregationError.from_message(
                "OpenAI Chat stream contains a non-JSON event",
            )
        accumulator.consume(DownstreamChunk(kind=event.kind, payload=event.payload, event=event.event))
    accumulator.validate_native_stream(saw_done_event=saw_done_event)
    return accumulator.build()


def _aggregate_openai_responses_response(model_name: str, events: Iterable[StreamEvent]) -> dict[str, Any]:
    accumulator = _OpenAIResponsesResponseAccumulator(model_name)
    for event in events:
        accumulator.consume(event)
        if accumulator.is_complete:
            break
    return accumulator.build()


def _aggregate_claude_response(model_name: str, events: Iterable[StreamEvent]) -> dict[str, Any]:
    accumulator = _ClaudeResponseAccumulator(model_name)
    for event in events:
        accumulator.consume(event)
        if accumulator.is_complete:
            break
    return accumulator.build()


def aggregate_stream_to_openai_chat(
    *,
    translator: Translator,
    model_name: str,
    original_request: dict[str, Any],
    translated_request: dict[str, Any],
    events: Iterable[StreamEvent],
) -> dict[str, Any]:
    """消费上游事件并生成完整的 OpenAI Chat response。"""
    state: dict[str, Any] = {}
    accumulator = _OpenAIChatResponseAccumulator(model_name)
    saw_done_event = False
    saw_native_terminal_event = False

    for event in events:
        if event.kind == "done":
            saw_done_event = True
        if event.kind == "json" and isinstance(event.payload, dict):
            event_type = str(event.payload.get("type") or event.event or "").strip().lower()
            if event_type in {"response.completed", "response.done"}:
                saw_native_terminal_event = True
                full_payload = translator.translate_nonstream_response(
                    model_name,
                    original_request,
                    translated_request,
                    event.payload,
                )
                if isinstance(full_payload, dict):
                    accumulator.consume_full_response(full_payload)
            elif event_type == "message_stop":
                saw_native_terminal_event = True
        downstream_chunks = translator.translate_stream_event(
            model_name,
            original_request,
            translated_request,
            event,
            state,
        )
        for chunk in downstream_chunks:
            accumulator.consume(chunk)

    if translator.source_format in {"openai_responses", "claude_chat"} and not saw_native_terminal_event:
        protocol_name = "Responses" if translator.source_format == "openai_responses" else "Claude"
        raise StreamAggregationError.from_message(
            f"{protocol_name} stream is incomplete: ended before its terminal event",
            error_type="upstream_stream_incomplete",
        )

    if not saw_done_event:
        downstream_chunks = translator.translate_stream_event(
            model_name,
            original_request,
            translated_request,
            StreamEvent(kind="done", payload="[DONE]"),
            state,
        )
        for chunk in downstream_chunks:
            accumulator.consume(chunk)

    return accumulator.build()


class _OpenAIChatResponseAccumulator:
    """合并 OpenAI Chat 流式 chunk，保留常见文本、工具调用和 usage 字段。"""

    def __init__(self, model_name: str):
        self._default_model_name = model_name
        self._response_id = f"chatcmpl_{model_name}"
        self._created = 0
        self._response_model = model_name
        self._choices: dict[int, dict[str, Any]] = {}
        self._usage: dict[str, Any] | None = None
        self._response_fields: dict[str, Any] = {}
        self._reasoning_state: dict[str, str] = {}

    def consume(self, chunk: DownstreamChunk) -> None:
        if chunk.kind == "done":
            return
        if chunk.kind == "text":
            if isinstance(chunk.payload, str) and chunk.payload:
                self._merge_choice(0, {"content": chunk.payload}, None)
            return
        if chunk.kind != "json" or not isinstance(chunk.payload, dict):
            return

        payload = chunk.payload
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            raise StreamAggregationError(payload)
        if str(payload.get("type") or "").strip().lower() == "error":
            raise StreamAggregationError.from_message(
                str(payload.get("message") or "Upstream Chat stream failed"),
                error_type=str(payload.get("error_type") or "upstream_stream_error"),
            )

        self._update_response_metadata(payload)

        choices = payload.get("choices")
        if not isinstance(choices, list):
            return
        if any(isinstance(choice, dict) and isinstance(choice.get("message"), dict) for choice in choices):
            self.consume_full_response(payload)
            return
        for position, raw_choice in enumerate(choices):
            if not isinstance(raw_choice, dict):
                continue
            index = _coerce_int(raw_choice.get("index"), position)
            delta = raw_choice.get("delta")
            if not isinstance(delta, dict):
                delta = {}
            reasoning_text = extract_openai_reasoning_delta(
                delta,
                self._reasoning_state,
                f"reasoning_details:{index}",
            )
            if reasoning_text:
                delta = dict(delta)
                delta["reasoning_content"] = reasoning_text
            self._merge_choice(index, delta, raw_choice.get("finish_reason"))
            self._merge_choice_fields(index, raw_choice)

    def validate_native_stream(self, *, saw_done_event: bool) -> None:
        """确认原生 Chat 流包含回答，并且在无 DONE 时由 finish_reason 完成。"""
        if not self._choices:
            raise StreamAggregationError.from_message(
                "OpenAI Chat stream is incomplete: no choices were received",
                error_type="upstream_stream_incomplete",
            )
        if not saw_done_event and any(entry.get("finish_reason") in (None, "") for entry in self._choices.values()):
            raise StreamAggregationError.from_message(
                "OpenAI Chat stream is incomplete: ended before a terminal event",
                error_type="upstream_stream_incomplete",
            )

    def build(self) -> dict[str, Any]:
        choices = []
        for index in sorted(self._choices):
            entry = self._choices[index]
            message = dict(entry["message"])
            message.setdefault("role", "assistant")
            message.setdefault("content", "")
            finish_reason = entry.get("finish_reason")
            if finish_reason in (None, ""):
                if message.get("tool_calls"):
                    finish_reason = "tool_calls"
                elif message.get("function_call"):
                    finish_reason = "function_call"
                else:
                    finish_reason = "stop"
            choice = copy.deepcopy(entry.get("fields") or {})
            choice.update(
                {
                    "index": index,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            )
            choices.append(choice)

        response: dict[str, Any] = copy.deepcopy(self._response_fields)
        response.update(
            {
                "id": self._response_id,
                "object": "chat.completion",
                "created": self._created or int(time.time()),
                "model": self._response_model or self._default_model_name,
                "choices": choices,
            }
        )
        if self._usage:
            response["usage"] = self._usage
        return response

    def consume_full_response(self, payload: dict[str, Any]) -> None:
        """在流仅提供完整终止 payload 时补充正文。"""
        self._update_response_metadata(payload)
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return
        for position, raw_choice in enumerate(choices):
            if not isinstance(raw_choice, dict):
                continue
            message = raw_choice.get("message")
            if not isinstance(message, dict):
                continue
            index = _coerce_int(raw_choice.get("index"), position)
            entry = self._choices.get(index)
            if entry is None:
                self._merge_choice(index, message, raw_choice.get("finish_reason"))
                self._merge_choice_fields(index, raw_choice)
                continue
            self._replace_message_from_full(entry["message"], message)
            if entry.get("finish_reason") in (None, "") and raw_choice.get("finish_reason") not in (None, ""):
                entry["finish_reason"] = raw_choice["finish_reason"]
            self._merge_choice_fields(index, raw_choice)

    def _merge_choice(self, index: int, delta: dict[str, Any], finish_reason: Any) -> None:
        entry = self._choices.setdefault(
            index,
            {
                "message": {"role": "assistant", "content": ""},
                "finish_reason": None,
                "fields": {},
            },
        )
        message = entry["message"]

        role = delta.get("role")
        if role not in (None, ""):
            message["role"] = role

        for field in ("content", "refusal"):
            value = delta.get(field)
            if isinstance(value, str):
                message[field] = str(message.get(field) or "") + value
            elif value is not None and field not in message:
                message[field] = value

        reasoning_content = delta.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            message["reasoning_content"] = str(message.get("reasoning_content") or "") + reasoning_content

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            self._merge_tool_calls(message, tool_calls)

        function_call = delta.get("function_call")
        if isinstance(function_call, dict):
            self._merge_function_call(message, function_call)

        audio = delta.get("audio")
        if isinstance(audio, dict):
            self._merge_audio(message, audio)

        known_fields = {
            "role",
            "content",
            "refusal",
            "reasoning_content",
            "tool_calls",
            "function_call",
            "audio",
        }
        for field, value in delta.items():
            if field not in known_fields:
                message[field] = copy.deepcopy(value)

        if finish_reason not in (None, ""):
            entry["finish_reason"] = finish_reason

    def _update_response_metadata(self, payload: dict[str, Any]) -> None:
        response_id = payload.get("id")
        if response_id not in (None, ""):
            self._response_id = str(response_id)
        response_model = payload.get("model")
        if response_model not in (None, ""):
            self._response_model = str(response_model)
        if payload.get("created") is not None:
            self._created = _coerce_int(payload.get("created"), self._created)
        usage = payload.get("usage")
        if isinstance(usage, dict):
            if self._usage is None:
                self._usage = {}
            for field, value in usage.items():
                if value is not None and (self._usage.get(field) in (None, 0, "") or value not in (0, "")):
                    self._usage[field] = copy.deepcopy(value)
        for field, value in payload.items():
            if field not in {"id", "object", "created", "model", "choices", "usage"}:
                self._response_fields[field] = copy.deepcopy(value)

    def _merge_choice_fields(self, index: int, raw_choice: dict[str, Any]) -> None:
        entry = self._choices.get(index)
        if entry is None:
            return
        fields = entry.setdefault("fields", {})
        for field, value in raw_choice.items():
            if field in {"index", "delta", "message", "finish_reason", "logprobs"}:
                continue
            fields[field] = copy.deepcopy(value)
        if "logprobs" not in raw_choice:
            return
        incoming = raw_choice.get("logprobs")
        current = fields.get("logprobs")
        if not isinstance(incoming, dict):
            if current is None:
                fields["logprobs"] = copy.deepcopy(incoming)
            return
        if not isinstance(current, dict):
            current = {}
            fields["logprobs"] = current
        for field, value in incoming.items():
            if isinstance(value, list):
                existing = current.get(field)
                if not isinstance(existing, list):
                    existing = []
                    current[field] = existing
                existing.extend(copy.deepcopy(value))
            elif value is not None:
                current[field] = copy.deepcopy(value)

    @staticmethod
    def _merge_tool_calls(message: dict[str, Any], tool_calls: list[Any]) -> None:
        merged = message.setdefault("tool_calls", [])
        if not isinstance(merged, list):
            merged = []
            message["tool_calls"] = merged

        for position, raw_tool_call in enumerate(tool_calls):
            if not isinstance(raw_tool_call, dict):
                continue
            index = _coerce_int(raw_tool_call.get("index"), position)
            while len(merged) <= index:
                merged.append(
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                )
            target = merged[index]
            if not isinstance(target, dict):
                target = {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                }
                merged[index] = target
            if raw_tool_call.get("id") not in (None, ""):
                target["id"] = str(raw_tool_call["id"])
            if raw_tool_call.get("type") not in (None, ""):
                target["type"] = raw_tool_call["type"]
            function = raw_tool_call.get("function")
            if not isinstance(function, dict):
                continue
            target_function = target.setdefault("function", {})
            name = function.get("name")
            if name not in (None, ""):
                _merge_stream_name(target_function, str(name))
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                target_function["arguments"] = str(target_function.get("arguments") or "") + arguments

    @staticmethod
    def _replace_message_from_full(current: dict[str, Any], full: dict[str, Any]) -> None:
        for field, value in full.items():
            current[field] = copy.deepcopy(value)

    @staticmethod
    def _merge_audio(message: dict[str, Any], audio: dict[str, Any]) -> None:
        target = message.setdefault("audio", {})
        if not isinstance(target, dict):
            target = {}
            message["audio"] = target
        for field, value in audio.items():
            if field in {"data", "transcript"} and isinstance(value, str):
                target[field] = str(target.get(field) or "") + value
            else:
                target[field] = copy.deepcopy(value)

    @staticmethod
    def _merge_function_call(message: dict[str, Any], function_call: dict[str, Any]) -> None:
        target = message.setdefault("function_call", {"name": "", "arguments": ""})
        if not isinstance(target, dict):
            target = {"name": "", "arguments": ""}
            message["function_call"] = target
        name = function_call.get("name")
        if name not in (None, ""):
            _merge_stream_name(target, str(name))
        arguments = function_call.get("arguments")
        if isinstance(arguments, str):
            target["arguments"] = str(target.get("arguments") or "") + arguments


class _OpenAIResponsesResponseAccumulator:
    """合并 OpenAI Responses 原生事件，生成完整 response 对象。"""

    _SUCCESS_EVENTS = {"response.completed", "response.done"}
    _FAILURE_EVENTS = {"response.failed", "response.incomplete", "response.cancelled", "response.canceled"}

    def __init__(self, model_name: str) -> None:
        self._default_model_name = model_name
        self._response: dict[str, Any] = {
            "id": f"resp_{model_name}",
            "object": "response",
            "created_at": 0,
            "status": "in_progress",
            "model": model_name,
            "output": [],
        }
        self._output_items: dict[int, dict[str, Any]] = {}
        self._output_item_ids: dict[str, int] = {}
        self._next_output_index = 0
        self._terminal_response: dict[str, Any] | None = None

    @property
    def is_complete(self) -> bool:
        return self._terminal_response is not None

    def consume(self, event: StreamEvent) -> None:
        if event.kind == "done":
            return
        if event.kind == "text":
            if isinstance(event.payload, str) and event.payload.strip():
                raise StreamAggregationError.from_message(
                    "OpenAI Responses stream contains a non-JSON event",
                )
            return
        if event.kind != "json" or not isinstance(event.payload, dict):
            return

        payload = event.payload
        if isinstance(payload.get("error"), dict):
            raise StreamAggregationError(payload)

        event_type = _stream_event_type(event)
        if event_type == "error":
            raise StreamAggregationError.from_message(
                str(payload.get("message") or "Upstream Responses stream failed"),
                error_type=str(payload.get("error_type") or "upstream_stream_error"),
            )
        if event_type in self._FAILURE_EVENTS:
            self._raise_response_failure(payload, event_type)
        if event_type in self._SUCCESS_EVENTS:
            response = self._extract_response(payload)
            if response is None:
                raise StreamAggregationError.from_message(
                    "OpenAI Responses completion event does not contain a response",
                )
            self._absorb_response(response)
            self._terminal_response = copy.deepcopy(response)
            return

        if event_type == "response.created":
            response = self._extract_response(payload)
            if response is not None:
                self._absorb_response(response)
            return

        if event_type == "response.output_item.added":
            self._absorb_output_item(payload)
            return
        if event_type == "response.output_item.done":
            self._absorb_output_item(payload, completed=True)
            return
        if event_type in {"response.content_part.added", "response.content_part.done"}:
            self._absorb_content_part(payload)
            return
        if event_type == "response.output_text.delta":
            self._append_message_content(payload, "output_text", payload.get("delta"))
            return
        if event_type == "response.output_text.done":
            self._set_message_content(payload, "output_text", payload.get("text"))
            return
        if event_type in {"response.refusal.delta", "response.refusal.done"}:
            field = "delta" if event_type.endswith("delta") else "refusal"
            if event_type.endswith("delta"):
                self._append_message_content(payload, "refusal", payload.get(field))
            else:
                self._set_message_content(payload, "refusal", payload.get(field))
            return
        if event_type in {"response.reasoning_summary_part.added", "response.reasoning_summary_part.done"}:
            self._absorb_reasoning_part(payload)
            return
        if event_type in {"response.reasoning_summary_text.delta", "response.reasoning_summary_text.done"}:
            field = "delta" if event_type.endswith("delta") else "text"
            if event_type.endswith("delta"):
                self._append_reasoning_summary(payload, payload.get(field))
            else:
                self._set_reasoning_summary(payload, payload.get(field))
            return
        if event_type in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
            self._consume_function_arguments(payload, event_type.endswith("done"))
            return
        if event_type in {"response.custom_tool_call_input.delta", "response.custom_tool_call_input.done"}:
            self._consume_custom_tool_input(payload, event_type.endswith("done"))
            return
        if event_type == "response.output_text.annotation.added":
            self._absorb_text_annotation(payload)

    def build(self) -> dict[str, Any]:
        if self._terminal_response is None:
            raise StreamAggregationError.from_message(
                "OpenAI Responses stream is incomplete: ended before a completion event",
                error_type="upstream_stream_incomplete",
            )

        response = copy.deepcopy(self._terminal_response)
        response.setdefault("object", "response")
        response.setdefault("id", self._response.get("id") or f"resp_{self._default_model_name}")
        response.setdefault("created_at", self._response.get("created_at") or int(time.time()))
        response.setdefault("status", "completed")
        response.setdefault("model", self._response.get("model") or self._default_model_name)

        terminal_output = response.get("output")
        if not isinstance(terminal_output, list):
            terminal_output = []
        response["output"] = self._merge_terminal_output(terminal_output)
        if not isinstance(response.get("usage"), dict) and isinstance(self._response.get("usage"), dict):
            response["usage"] = copy.deepcopy(self._response["usage"])
        return response

    def _absorb_response(self, response: dict[str, Any]) -> None:
        for field, value in response.items():
            if field == "output":
                continue
            if value is not None:
                self._response[field] = copy.deepcopy(value)
        output = response.get("output")
        if isinstance(output, list):
            for index, item in enumerate(output):
                if isinstance(item, dict):
                    self._absorb_output_item(
                        {
                            "output_index": index,
                            "item": item,
                        }
                    )

    def _absorb_output_item(self, payload: dict[str, Any], *, completed: bool = False) -> None:
        raw_item = payload.get("item")
        if not isinstance(raw_item, dict):
            return
        index = self._resolve_output_index(payload, raw_item)
        item = self._ensure_output_item(index, raw_item)
        self._merge_output_item(item, raw_item)
        if completed:
            item["status"] = raw_item.get("status") or "completed"

    def _absorb_content_part(self, payload: dict[str, Any]) -> None:
        part = payload.get("part")
        if not isinstance(part, dict):
            return
        item = self._ensure_output_item(
            self._coerce_optional_index(payload.get("output_index")),
            {
                "id": payload.get("item_id"),
                "type": "message",
                "role": "assistant",
            },
        )
        content = item.setdefault("content", [])
        if not isinstance(content, list):
            content = []
            item["content"] = content
        content_index = _coerce_int(payload.get("content_index"), len(content))
        while len(content) <= content_index:
            content.append({})
        if isinstance(content[content_index], dict):
            _merge_content_block(content[content_index], part)
        else:
            content[content_index] = copy.deepcopy(part)

    def _append_message_content(self, payload: dict[str, Any], content_type: str, value: Any) -> None:
        if not isinstance(value, str):
            return
        item = self._ensure_message_item(payload)
        content = item.setdefault("content", [])
        if not isinstance(content, list):
            content = []
            item["content"] = content
        content_index = _coerce_int(payload.get("content_index"), 0)
        while len(content) <= content_index:
            content.append({"type": content_type})
        block = content[content_index]
        if not isinstance(block, dict):
            block = {"type": content_type}
            content[content_index] = block
        block.setdefault("type", content_type)
        if content_type == "refusal":
            block["refusal"] = str(block.get("refusal") or "") + value
        else:
            block["text"] = str(block.get("text") or "") + value

    def _set_message_content(self, payload: dict[str, Any], content_type: str, value: Any) -> None:
        if not isinstance(value, str):
            return
        item = self._ensure_message_item(payload)
        content = item.setdefault("content", [])
        if not isinstance(content, list):
            content = []
            item["content"] = content
        content_index = _coerce_int(payload.get("content_index"), 0)
        while len(content) <= content_index:
            content.append({"type": content_type})
        block = content[content_index]
        if not isinstance(block, dict):
            block = {"type": content_type}
            content[content_index] = block
        block["type"] = content_type
        if content_type == "refusal":
            block["refusal"] = value
        else:
            block["text"] = value

    def _append_reasoning_summary(self, payload: dict[str, Any], value: Any) -> None:
        if not isinstance(value, str):
            return
        summary = self._ensure_reasoning_item(payload).setdefault("summary", [])
        if not isinstance(summary, list):
            summary = []
            self._ensure_reasoning_item(payload)["summary"] = summary
        index = _coerce_int(payload.get("summary_index"), 0)
        while len(summary) <= index:
            summary.append({"type": "summary_text", "text": ""})
        part = summary[index]
        if not isinstance(part, dict):
            part = {"type": "summary_text", "text": ""}
            summary[index] = part
        part["type"] = "summary_text"
        part["text"] = str(part.get("text") or "") + value

    def _absorb_reasoning_part(self, payload: dict[str, Any]) -> None:
        part = payload.get("part")
        if not isinstance(part, dict):
            return
        item = self._ensure_reasoning_item(payload)
        summary = item.setdefault("summary", [])
        if not isinstance(summary, list):
            summary = []
            item["summary"] = summary
        index = _coerce_int(payload.get("summary_index"), len(summary))
        while len(summary) <= index:
            summary.append({})
        if isinstance(summary[index], dict):
            _merge_content_block(summary[index], part)
        else:
            summary[index] = copy.deepcopy(part)

    def _set_reasoning_summary(self, payload: dict[str, Any], value: Any) -> None:
        if not isinstance(value, str):
            return
        item = self._ensure_reasoning_item(payload)
        summary = item.setdefault("summary", [])
        if not isinstance(summary, list):
            summary = []
            item["summary"] = summary
        index = _coerce_int(payload.get("summary_index"), 0)
        while len(summary) <= index:
            summary.append({"type": "summary_text", "text": ""})
        summary[index] = {"type": "summary_text", "text": value}

    def _consume_function_arguments(self, payload: dict[str, Any], completed: bool) -> None:
        item = self._ensure_output_item(
            self._coerce_optional_index(payload.get("output_index")),
            {
                "id": payload.get("item_id"),
                "type": "function_call",
                "status": "in_progress",
            },
        )
        item["type"] = "function_call"
        value = payload.get("arguments") if completed else payload.get("delta")
        if not isinstance(value, str):
            return
        if completed:
            item["arguments"] = value
            item["status"] = "completed"
        else:
            item["arguments"] = str(item.get("arguments") or "") + value

    def _consume_custom_tool_input(self, payload: dict[str, Any], completed: bool) -> None:
        item = self._ensure_output_item(
            self._coerce_optional_index(payload.get("output_index")),
            {
                "id": payload.get("item_id"),
                "type": "custom_tool_call",
                "status": "in_progress",
            },
        )
        item["type"] = "custom_tool_call"
        value = payload.get("input") if completed else payload.get("delta")
        if not isinstance(value, str):
            return
        if completed:
            item["input"] = value
            item["status"] = "completed"
        else:
            item["input"] = str(item.get("input") or "") + value

    def _absorb_text_annotation(self, payload: dict[str, Any]) -> None:
        annotation = payload.get("annotation")
        if not isinstance(annotation, dict):
            return
        item = self._ensure_message_item(payload)
        content = item.setdefault("content", [])
        if not isinstance(content, list):
            return
        content_index = _coerce_int(payload.get("content_index"), 0)
        while len(content) <= content_index:
            content.append({"type": "output_text", "text": ""})
        block = content[content_index]
        if not isinstance(block, dict):
            block = {"type": "output_text", "text": ""}
            content[content_index] = block
        block.setdefault("annotations", []).append(copy.deepcopy(annotation))

    def _ensure_message_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._ensure_output_item(
            self._coerce_optional_index(payload.get("output_index")),
            {
                "id": payload.get("item_id"),
                "type": "message",
                "role": "assistant",
                "content": [],
            },
        )

    def _ensure_reasoning_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._ensure_output_item(
            self._coerce_optional_index(payload.get("output_index")),
            {
                "id": payload.get("item_id"),
                "type": "reasoning",
                "summary": [],
            },
        )

    def _ensure_output_item(self, index: int | None, item: dict[str, Any]) -> dict[str, Any]:
        item_id = str(item.get("id") or "").strip()
        if item_id and item_id in self._output_item_ids:
            resolved_index = self._output_item_ids[item_id]
        elif index is not None:
            resolved_index = index
        else:
            resolved_index = self._next_output_index
        current = self._output_items.get(resolved_index)
        if current is None:
            current = _default_responses_output_item(item)
            self._output_items[resolved_index] = current
        if item_id:
            self._output_item_ids[item_id] = resolved_index
            current.setdefault("id", item_id)
        self._next_output_index = max(self._next_output_index, resolved_index + 1)
        return current

    def _resolve_output_index(self, payload: dict[str, Any], item: dict[str, Any]) -> int | None:
        explicit = self._coerce_optional_index(payload.get("output_index"))
        if explicit is not None:
            return explicit
        item_id = str(item.get("id") or payload.get("item_id") or "").strip()
        if item_id and item_id in self._output_item_ids:
            return self._output_item_ids[item_id]
        return None

    @staticmethod
    def _coerce_optional_index(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _merge_output_item(target: dict[str, Any], source: dict[str, Any]) -> None:
        for field, value in source.items():
            if field == "content" and isinstance(value, list):
                existing = target.get("content")
                target["content"] = _merge_content_lists(existing, value)
            elif field == "summary" and isinstance(value, list):
                existing = target.get("summary")
                target["summary"] = _merge_content_lists(existing, value)
            elif field in {"arguments", "input"} and value in (None, "") and target.get(field) not in (None, ""):
                continue
            elif value is not None:
                target[field] = copy.deepcopy(value)

    def _merge_terminal_output(
        self,
        terminal_output: list[Any],
    ) -> list[dict[str, Any]]:
        output = [copy.deepcopy(item) for item in terminal_output if isinstance(item, dict)]
        id_to_position = {
            str(item.get("id")): position for position, item in enumerate(output) if item.get("id") not in (None, "")
        }
        for index, accumulated in sorted(self._output_items.items()):
            item_id = str(accumulated.get("id") or "")
            position = id_to_position.get(item_id) if item_id else None
            if position is None and index < len(output):
                position = index
            if position is None:
                missing_item = copy.deepcopy(accumulated)
                if missing_item.get("status") == "in_progress":
                    missing_item["status"] = "completed"
                output.append(missing_item)
                continue
            _merge_missing_output_item(output[position], accumulated)
        return output

    def _extract_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        response = payload.get("response")
        if isinstance(response, dict):
            return response
        if isinstance(payload.get("output"), list) or payload.get("object") == "response":
            return payload
        return None

    def _raise_response_failure(self, payload: dict[str, Any], event_type: str) -> None:
        response = payload.get("response")
        if isinstance(response, dict):
            error = response.get("error")
            if isinstance(error, dict):
                raise StreamAggregationError({"response": response, "error": error})
            incomplete_details = response.get("incomplete_details")
            if isinstance(incomplete_details, dict):
                reason = incomplete_details.get("reason") or incomplete_details.get("details")
                raise StreamAggregationError.from_message(
                    str(reason or f"OpenAI Responses stream ended with {event_type}"),
                    error_type=event_type.replace("response.", ""),
                )
        error = payload.get("error")
        if isinstance(error, dict):
            raise StreamAggregationError(payload)
        raise StreamAggregationError.from_message(
            f"OpenAI Responses stream ended with {event_type}",
            error_type=event_type.replace("response.", ""),
        )


class _ClaudeResponseAccumulator:
    """合并 Claude 原生事件，生成完整 message 对象。"""

    def __init__(self, model_name: str) -> None:
        self._default_model_name = model_name
        self._message: dict[str, Any] = {
            "id": f"msg_{model_name}",
            "type": "message",
            "role": "assistant",
            "model": model_name,
            "content": [],
        }
        self._blocks: dict[int, dict[str, Any]] = {}
        self._block_order: list[int] = []
        self._usage: dict[str, Any] = {}
        self._message_stopped = False

    @property
    def is_complete(self) -> bool:
        return self._message_stopped

    def consume(self, event: StreamEvent) -> None:
        if event.kind == "done":
            return
        if event.kind == "text":
            if isinstance(event.payload, str) and event.payload.strip():
                raise StreamAggregationError.from_message(
                    "Claude stream contains a non-JSON event",
                )
            return
        if event.kind != "json" or not isinstance(event.payload, dict):
            return

        payload = event.payload
        if isinstance(payload.get("error"), dict):
            raise StreamAggregationError(payload)
        event_type = _stream_event_type(event)
        if event_type == "error":
            raise StreamAggregationError(payload)
        if event_type == "message_start":
            message = payload.get("message")
            if isinstance(message, dict):
                self._merge_message_start(message)
            return
        if event_type == "content_block_start":
            self._start_content_block(payload)
            return
        if event_type == "content_block_delta":
            self._consume_content_delta(payload)
            return
        if event_type == "content_block_stop":
            self._stop_content_block(payload)
            return
        if event_type == "message_delta":
            self._consume_message_delta(payload)
            return
        if event_type == "message_stop":
            self._message_stopped = True

    def build(self) -> dict[str, Any]:
        if not self._message_stopped:
            raise StreamAggregationError.from_message(
                "Claude stream is incomplete: ended before message_stop",
                error_type="upstream_stream_incomplete",
            )
        content = []
        for index in self._block_order:
            block = self._blocks[index]
            self._finalize_tool_input(block)
            content.append(copy.deepcopy(block))
        self._message["content"] = content
        if self._message.get("stop_reason") in (None, ""):
            self._message["stop_reason"] = "end_turn"
        self._message.setdefault("stop_sequence", None)
        if self._usage:
            self._message["usage"] = copy.deepcopy(self._usage)
        self._message.setdefault("type", "message")
        self._message.setdefault("role", "assistant")
        self._message.setdefault("model", self._default_model_name)
        return copy.deepcopy(self._message)

    def _merge_message_start(self, message: dict[str, Any]) -> None:
        for field, value in message.items():
            if field == "content":
                if isinstance(value, list):
                    for index, block in enumerate(value):
                        if isinstance(block, dict):
                            self._merge_block(index, block)
                continue
            if field == "usage" and isinstance(value, dict):
                self._merge_usage(value)
                continue
            if value is not None:
                self._message[field] = copy.deepcopy(value)

    def _start_content_block(self, payload: dict[str, Any]) -> None:
        index = _coerce_int(payload.get("index"), len(self._block_order))
        block = payload.get("content_block")
        if not isinstance(block, dict):
            block = {}
        self._merge_block(index, block)

    def _consume_content_delta(self, payload: dict[str, Any]) -> None:
        delta = payload.get("delta")
        if not isinstance(delta, dict):
            return
        index = _coerce_int(payload.get("index"), len(self._block_order))
        delta_type = str(delta.get("type") or "").strip().lower()
        if index not in self._blocks:
            default_type = {
                "text_delta": "text",
                "thinking_delta": "thinking",
                "input_json_delta": "tool_use",
            }.get(delta_type, "text")
            self._merge_block(index, {"type": default_type})
        block = self._blocks[index]
        if delta_type == "text_delta" and isinstance(delta.get("text"), str):
            block["text"] = str(block.get("text") or "") + delta["text"]
        elif delta_type == "thinking_delta" and isinstance(delta.get("thinking"), str):
            block["thinking"] = str(block.get("thinking") or "") + delta["thinking"]
        elif delta_type == "signature_delta" and isinstance(delta.get("signature"), str):
            block["signature"] = str(block.get("signature") or "") + delta["signature"]
        elif delta_type == "input_json_delta" and isinstance(delta.get("partial_json"), str):
            block.setdefault("_input_json_parts", []).append(delta["partial_json"])
        elif delta_type == "citations_delta" and isinstance(delta.get("citation"), dict):
            citations = block.setdefault("citations", [])
            if not isinstance(citations, list):
                citations = []
                block["citations"] = citations
            citations.append(copy.deepcopy(delta["citation"]))

    def _stop_content_block(self, payload: dict[str, Any]) -> None:
        index = _coerce_int(payload.get("index"), 0)
        block = self._blocks.get(index)
        if block is not None:
            self._finalize_tool_input(block)

    def _consume_message_delta(self, payload: dict[str, Any]) -> None:
        delta = payload.get("delta")
        if isinstance(delta, dict):
            for field in ("stop_reason", "stop_sequence"):
                if field in delta:
                    self._message[field] = copy.deepcopy(delta[field])
        usage = payload.get("usage")
        if isinstance(usage, dict):
            self._merge_usage(usage)

    def _merge_block(self, index: int, source: dict[str, Any]) -> dict[str, Any]:
        target = self._blocks.get(index)
        if target is None:
            target = {}
            self._blocks[index] = target
            self._block_order.append(index)
        for field, value in source.items():
            if field == "input" and isinstance(value, dict):
                target[field] = copy.deepcopy(value)
            elif field == "text" and isinstance(value, str):
                target[field] = value if len(value) >= len(str(target.get(field) or "")) else target.get(field)
            elif field == "thinking" and isinstance(value, str):
                target[field] = value if len(value) >= len(str(target.get(field) or "")) else target.get(field)
            elif value is not None:
                target[field] = copy.deepcopy(value)
        return target

    def _merge_usage(self, usage: dict[str, Any]) -> None:
        for field, value in usage.items():
            if value is None:
                continue
            current = self._usage.get(field)
            if current in (None, 0, "") or value not in (0, ""):
                self._usage[field] = copy.deepcopy(value)

    @staticmethod
    def _finalize_tool_input(block: dict[str, Any]) -> None:
        parts = block.pop("_input_json_parts", None)
        if not parts:
            if block.get("type") == "tool_use":
                block.setdefault("input", {})
            return
        raw_input = "".join(str(part) for part in parts)
        try:
            parsed_input = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            raise StreamAggregationError.from_message(
                "Claude tool input is not valid JSON",
                error_type="upstream_stream_processing_error",
            ) from exc
        block["input"] = parsed_input


def _merge_stream_name(target: dict[str, Any], incoming: str) -> None:
    current = str(target.get("name") or "")
    if not current:
        target["name"] = incoming
    elif incoming == current or incoming.startswith(current) or current.startswith(incoming):
        target["name"] = incoming if len(incoming) > len(current) else current
    else:
        target["name"] = current + incoming


def _stream_event_type(event: StreamEvent) -> str:
    if isinstance(event.payload, dict):
        payload_type = str(event.payload.get("type") or "").strip().lower()
        if payload_type:
            return payload_type
    return str(event.event or "").strip().lower()


def _default_responses_output_item(item: dict[str, Any]) -> dict[str, Any]:
    item_type = str(item.get("type") or "message").strip().lower()
    if item_type == "function_call":
        return {
            "id": str(item.get("id") or ""),
            "type": "function_call",
            "status": "in_progress",
            "call_id": str(item.get("call_id") or ""),
            "name": str(item.get("name") or ""),
            "arguments": "",
        }
    if item_type == "reasoning":
        return {
            "id": str(item.get("id") or ""),
            "type": "reasoning",
            "status": item.get("status") or "in_progress",
            "summary": [],
        }
    if item_type != "message":
        return copy.deepcopy(item)
    return {
        "id": str(item.get("id") or ""),
        "type": item.get("type") or "message",
        "role": item.get("role") or "assistant",
        "status": item.get("status") or "in_progress",
        "content": [],
    }


def _merge_content_lists(existing: Any, incoming: list[Any]) -> list[Any]:
    result = copy.deepcopy(existing) if isinstance(existing, list) else []
    for index, raw_block in enumerate(incoming):
        if not isinstance(raw_block, dict):
            continue
        while len(result) <= index:
            result.append({})
        if not isinstance(result[index], dict):
            result[index] = copy.deepcopy(raw_block)
            continue
        _merge_content_block(result[index], raw_block)
    return result


def _merge_content_block(target: dict[str, Any], source: dict[str, Any]) -> None:
    for field, value in source.items():
        if field in {"text", "refusal"} and isinstance(value, str):
            current = str(target.get(field) or "")
            if len(value) >= len(current):
                target[field] = value
        elif value is not None:
            target[field] = copy.deepcopy(value)


def _merge_missing_output_item(target: dict[str, Any], source: dict[str, Any]) -> None:
    for field, value in source.items():
        if field in {"content", "summary"} and isinstance(value, list):
            current = target.get(field)
            merged = _merge_content_lists(current, value)
            if not isinstance(current, list) or len(merged) > len(current) or merged != current:
                target[field] = merged
        elif field in {"arguments", "input"}:
            current = target.get(field)
            if current in (None, "", {}):
                target[field] = copy.deepcopy(value)
        elif field == "status" and value == "in_progress":
            continue
        elif target.get(field) in (None, "") and value not in (None, ""):
            target[field] = copy.deepcopy(value)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
