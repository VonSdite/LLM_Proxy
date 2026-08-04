#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把上游流式事件聚合为 OpenAI Chat 非流式响应。"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from ..proxy_core import DownstreamChunk, StreamEvent
from .reasoning_utils import extract_openai_reasoning_delta
from .registry import Translator


class StreamAggregationError(RuntimeError):
    """上游流返回了无法转换为完整响应的错误事件。"""


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

    for event in events:
        if event.kind == "done":
            saw_done_event = True
        if event.kind == "json" and isinstance(event.payload, dict):
            event_type = str(event.payload.get("type") or event.event or "").strip().lower()
            if event_type in {"response.completed", "response.done"}:
                full_payload = translator.translate_nonstream_response(
                    model_name,
                    original_request,
                    translated_request,
                    event.payload,
                )
                if isinstance(full_payload, dict):
                    accumulator.consume_full_response(full_payload)
        downstream_chunks = translator.translate_stream_event(
            model_name,
            original_request,
            translated_request,
            event,
            state,
        )
        for chunk in downstream_chunks:
            accumulator.consume(chunk)

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
            message = str(error_payload.get("message") or "Upstream stream failed")
            raise StreamAggregationError(message)

        self._update_response_metadata(payload)

        choices = payload.get("choices")
        if not isinstance(choices, list):
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

    def build(self) -> dict[str, Any]:
        choices = []
        for index in sorted(self._choices):
            entry = self._choices[index]
            message = dict(entry["message"])
            message.setdefault("role", "assistant")
            message.setdefault("content", "")
            finish_reason = entry.get("finish_reason")
            if finish_reason in (None, ""):
                finish_reason = "tool_calls" if message.get("tool_calls") else "stop"
            choices.append(
                {
                    "index": index,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            )

        if not choices:
            choices.append(
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            )

        response: dict[str, Any] = {
            "id": self._response_id,
            "object": "chat.completion",
            "created": self._created or int(time.time()),
            "model": self._response_model or self._default_model_name,
            "choices": choices,
        }
        if self._usage:
            response["usage"] = self._usage
        return response

    def consume_full_response(self, payload: dict[str, Any]) -> None:
        """在流仅提供完整终止 payload 时补充正文。"""
        self._update_response_metadata(payload)
        if self._has_content():
            return
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
            self._merge_choice(index, message, raw_choice.get("finish_reason"))

    def _merge_choice(self, index: int, delta: dict[str, Any], finish_reason: Any) -> None:
        entry = self._choices.setdefault(
            index,
            {
                "message": {"role": "assistant", "content": ""},
                "finish_reason": None,
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

        if finish_reason not in (None, ""):
            entry["finish_reason"] = finish_reason

    def _has_content(self) -> bool:
        for entry in self._choices.values():
            message = entry.get("message") or {}
            if message.get("content") or message.get("reasoning_content") or message.get("tool_calls"):
                return True
        return False

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
            self._usage = dict(usage)

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
            if raw_tool_call.get("id") not in (None, ""):
                target["id"] = str(raw_tool_call["id"])
            if raw_tool_call.get("type") not in (None, ""):
                target["type"] = raw_tool_call["type"]
            function = raw_tool_call.get("function")
            if not isinstance(function, dict):
                continue
            target_function = target.setdefault("function", {})
            if function.get("name") not in (None, ""):
                target_function["name"] = str(target_function.get("name") or "") + str(function["name"])
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                target_function["arguments"] = str(target_function.get("arguments") or "") + arguments

    @staticmethod
    def _merge_function_call(message: dict[str, Any], function_call: dict[str, Any]) -> None:
        target = message.setdefault("function_call", {"name": "", "arguments": ""})
        if not isinstance(target, dict):
            target = {"name": "", "arguments": ""}
            message["function_call"] = target
        if function_call.get("name") not in (None, ""):
            target["name"] = str(target.get("name") or "") + str(function_call["name"])
        arguments = function_call.get("arguments")
        if isinstance(arguments, str):
            target["arguments"] = str(target.get("arguments") or "") + arguments


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
