#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider request/response translators for OpenAI, Claude, and Codex families."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from ..proxy_core.contracts import DownstreamChunk, StreamEvent
from ..utils.compat import Protocol, dataclass
from .claude_bridge import (
    convert_claude_request_to_openai_chat_request as _convert_claude_request_to_openai_chat_request,
    convert_openai_chat_response_to_claude as _convert_openai_chat_response_to_claude,
    translate_openai_chat_downstream_chunk_to_claude as _translate_openai_chat_downstream_chunk_to_claude,
)
from .codex_bridge import (
    convert_codex_request_to_openai_chat_request as _convert_codex_request_to_openai_chat_request,
)
from .responses_bridge import (
    convert_openai_chat_response_to_responses as _convert_openai_chat_response_to_responses,
    convert_openai_responses_request_to_chat_request as _convert_openai_responses_request_to_chat_request,
    translate_openai_chat_downstream_chunk_to_responses as _translate_openai_chat_downstream_chunk_to_responses,
)


class Translator(Protocol):
    @property
    def source_format(self) -> str: ...

    @property
    def target_format(self) -> str: ...

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        ...

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        ...

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        ...


@dataclass(frozen=True, slots=True)
class OpenAIChatTranslator:
    source_format: str = "openai_chat"
    target_format: str = "openai_chat"

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        translated = dict(body)
        translated["model"] = model_name
        translated["stream"] = bool(stream)
        return translated

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        del model_name, original_request, translated_request, state
        if event.kind == "done":
            return [DownstreamChunk(kind="done")]
        if event.kind == "json":
            event_name = event.event
            if not event_name and isinstance(event.payload, dict):
                event_name = str(event.payload.get("type") or "").strip() or None
            return [DownstreamChunk(kind="json", payload=event.payload, event=event_name)]
        return [DownstreamChunk(kind="text", payload=event.payload, event=event.event)]

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        del model_name, original_request, translated_request
        return payload


@dataclass(frozen=True, slots=True)
class OpenAIResponsesTranslator:
    source_format: str = "openai_responses"
    target_format: str = "openai_chat"

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        translated: Dict[str, Any] = {
            "model": model_name,
            "input": [],
            "stream": bool(stream),
        }

        instructions, input_items = _to_openai_responses_input(body.get("messages"))
        if instructions:
            translated["instructions"] = instructions
        if input_items:
            translated["input"] = input_items

        if body.get("max_tokens") is not None:
            translated["max_output_tokens"] = body.get("max_tokens")
        if body.get("temperature") is not None:
            translated["temperature"] = body.get("temperature")
        if body.get("top_p") is not None:
            translated["top_p"] = body.get("top_p")
        if body.get("user") is not None:
            translated["user"] = body.get("user")
        if body.get("metadata") is not None:
            translated["metadata"] = body.get("metadata")
        if body.get("parallel_tool_calls") is not None:
            translated["parallel_tool_calls"] = body.get("parallel_tool_calls")

        tools = _to_openai_responses_tools(body.get("tools"))
        if tools:
            translated["tools"] = tools

        tool_choice = _to_openai_responses_tool_choice(body.get("tool_choice"))
        if tool_choice is not None:
            translated["tool_choice"] = tool_choice

        return translated

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        del original_request
        if event.kind == "done":
            return [DownstreamChunk(kind="done")]
        if event.kind != "json" or not isinstance(event.payload, dict):
            return []

        payload = event.payload
        event_type = str(payload.get("type") or event.event or "").strip()
        response_id = str(state.get("response_id") or f"chatcmpl_{model_name}")
        created = int(state.get("created") or 0)
        response_model = str(state.get("response_model") or translated_request.get("model") or model_name)
        outputs: list[DownstreamChunk] = []

        if event_type == "response.created":
            response = payload.get("response") or {}
            if isinstance(response, dict):
                response_id = str(response.get("id") or response_id)
                created = int(response.get("created_at") or created or 0)
                response_model = str(response.get("model") or response_model)
            state["response_id"] = response_id
            state["created"] = created
            state["response_model"] = response_model
            state.setdefault("response_items", {})
            state.setdefault("saw_text", False)
            state.setdefault("saw_tool_call", False)
            return outputs

        items = state.setdefault("response_items", {})

        if event_type == "response.output_item.added":
            item = payload.get("item") or {}
            if isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "function_call":
                item_id = str(item.get("id") or payload.get("item_id") or "").strip()
                if item_id:
                    call_id = str(item.get("call_id") or "").strip()
                    if not call_id:
                        call_id = item_id[3:] if item_id.startswith("fc_") else item_id
                    items[item_id] = {
                        "id": call_id,
                        "name": str(item.get("name") or ""),
                        "index": int(payload.get("output_index") or 0),
                        "delta_emitted": False,
                    }
                    state["saw_tool_call"] = True
            return outputs

        if event_type == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                state["saw_text"] = True
                outputs.append(
                    DownstreamChunk(
                        kind="json",
                        payload=_build_openai_delta_chunk(
                            response_model,
                            int(payload.get("output_index") or 0),
                            response_id=response_id,
                            created=created,
                            content=delta,
                        ),
                    )
                )
            return outputs

        if event_type == "response.reasoning_summary_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                outputs.append(
                    DownstreamChunk(
                        kind="json",
                        payload=_build_openai_delta_chunk(
                            response_model,
                            int(payload.get("output_index") or 0),
                            response_id=response_id,
                            created=created,
                            delta_fields={"reasoning_content": delta},
                        ),
                    )
                )
            return outputs

        if event_type == "response.function_call_arguments.delta":
            item_id = str(payload.get("item_id") or "")
            accumulator = items.get(item_id)
            delta = payload.get("delta")
            if accumulator is not None and isinstance(delta, str):
                accumulator.setdefault("arguments", [])
                accumulator["arguments"].append(delta)
                outputs.append(
                    DownstreamChunk(
                        kind="json",
                        payload=_build_openai_delta_chunk(
                            response_model,
                            int(accumulator.get("index") or 0),
                            response_id=response_id,
                            created=created,
                            tool_call={
                                "index": int(accumulator.get("index") or 0),
                                "id": accumulator.get("id") or item_id,
                                "type": "function",
                                "function": {
                                    "name": accumulator.get("name") or "",
                                    "arguments": delta,
                                },
                            },
                        ),
                    )
                )
            return outputs

        if event_type in {"response.output_text.done", "response.function_call_arguments.done"}:
            return outputs

        if event_type == "response.completed":
            response = payload.get("response") or {}
            if isinstance(response, dict):
                if response.get("model") is not None:
                    response_model = str(response.get("model"))
                    state["response_model"] = response_model
                finish_reason = _resolve_openai_responses_finish_reason(
                    saw_text=bool(state.get("saw_text")),
                    saw_tool_call=bool(state.get("saw_tool_call")),
                )
                outputs.append(
                    DownstreamChunk(
                        kind="json",
                        payload=_build_openai_delta_chunk(
                            response_model,
                            0,
                            response_id=response_id,
                            created=created,
                            finish_reason=finish_reason,
                        ),
                    )
                )
                usage_chunk = _build_openai_usage_chunk_from_usage(
                    _extract_openai_responses_usage(response.get("usage")),
                    response_model,
                    response_id=response_id,
                    created=created,
                )
                if usage_chunk is not None:
                    outputs.append(usage_chunk)
            return outputs

        if event_type == "response.failed":
            response = payload.get("response") or {}
            error_payload = response.get("error") if isinstance(response, dict) else None
            outputs.append(
                DownstreamChunk(
                    kind="json",
                    payload={
                        "error": {
                            "message": (
                                (error_payload or {}).get("message")
                                if isinstance(error_payload, dict)
                                else "Upstream responses request failed"
                            ),
                            "type": (
                                (error_payload or {}).get("type")
                                if isinstance(error_payload, dict)
                                else "upstream_error"
                            ),
                            "param": None,
                            "code": (
                                (error_payload or {}).get("code")
                                if isinstance(error_payload, dict)
                                else None
                            ),
                        }
                    },
                )
            )
            return outputs

        return outputs

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        del original_request
        if not isinstance(payload, dict):
            return payload

        response = payload.get("response") if isinstance(payload.get("response"), dict) else payload
        if not isinstance(response, dict):
            return payload

        message, tool_calls = _extract_openai_responses_message_and_tool_calls(response.get("output"))
        if tool_calls:
            message["tool_calls"] = tool_calls
        reasoning_content = _extract_openai_responses_reasoning_content(response.get("output"))
        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        response_model = str(response.get("model") or translated_request.get("model") or model_name)
        finish_reason = _resolve_openai_responses_finish_reason(
            saw_text=bool(message.get("content")),
            saw_tool_call=bool(tool_calls),
        )
        result = {
            "id": response.get("id") or f"chatcmpl_{response_model}",
            "object": "chat.completion",
            "created": int(response.get("created_at") or 0),
            "model": response_model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
        }
        usage = _extract_openai_responses_usage(response.get("usage"))
        if usage:
            result["usage"] = usage
        return result


@dataclass(frozen=True, slots=True)
class OpenAIResponsesPassthroughTranslator:
    source_format: str = "openai_responses"
    target_format: str = "openai_responses"

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        translated = dict(body)
        translated["model"] = model_name
        translated["stream"] = bool(stream)
        return translated

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        del model_name, original_request, translated_request, state
        if event.kind == "done":
            return []
        if event.kind == "json":
            event_name = event.event
            if not event_name and isinstance(event.payload, dict):
                event_name = str(event.payload.get("type") or "").strip() or None
            return [DownstreamChunk(kind="json", payload=event.payload, event=event_name)]
        return [DownstreamChunk(kind="text", payload=event.payload, event=event.event)]

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        del model_name, original_request, translated_request
        return payload


@dataclass(frozen=True, slots=True)
class OpenAIChatResponsesTranslator:
    source_format: str = "openai_chat"
    target_format: str = "openai_responses"

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        return _convert_openai_responses_request_to_chat_request(model_name, body, stream)

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        return _translate_openai_chat_downstream_chunk_to_responses(
            model_name,
            original_request,
            translated_request,
            DownstreamChunk(kind=event.kind, payload=event.payload, event=event.event),
            state.setdefault("responses_bridge", {}),
        )

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        return _convert_openai_chat_response_to_responses(
            model_name,
            original_request,
            translated_request,
            payload,
        )


@dataclass(frozen=True, slots=True)
class ClaudeChatTranslator:
    source_format: str = "claude_chat"
    target_format: str = "openai_chat"

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        translated: Dict[str, Any] = {
            "model": model_name,
            "max_tokens": int(body.get("max_tokens") or 4096),
            "messages": [],
            "stream": bool(stream),
        }
        if body.get("temperature") is not None:
            translated["temperature"] = body.get("temperature")
        elif body.get("top_p") is not None:
            translated["top_p"] = body.get("top_p")

        stop = body.get("stop")
        if isinstance(stop, list):
            translated["stop_sequences"] = [str(item) for item in stop if str(item).strip()]
        elif stop not in (None, ""):
            translated["stop_sequences"] = [str(stop)]

        system_parts: list[str] = []
        for message in body.get("messages", []) or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            if role == "system":
                text = _extract_text_content(message.get("content"))
                if text:
                    system_parts.append(text)
                continue
            if role == "tool":
                tool_use_id = str(message.get("tool_call_id") or "").strip()
                if tool_use_id:
                    translated["messages"].append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_use_id,
                                    "content": _normalize_tool_result_content(message.get("content")),
                                }
                            ],
                        }
                    )
                continue

            content_blocks = _to_claude_content_blocks(message.get("content"))
            if role == "assistant":
                content_blocks.extend(_to_claude_tool_use_blocks(message.get("tool_calls")))
            translated["messages"].append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": content_blocks or [{"type": "text", "text": ""}],
                }
            )

        if system_parts:
            translated["system"] = "\n\n".join(system_parts)

        tools = _to_claude_tools(body.get("tools"))
        if tools:
            translated["tools"] = tools
        tool_choice = _to_claude_tool_choice(body.get("tool_choice"))
        if tool_choice:
            translated["tool_choice"] = tool_choice
        return translated

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        del original_request, translated_request
        if event.kind == "done":
            return [DownstreamChunk(kind="done")]
        if event.kind != "json" or not isinstance(event.payload, dict):
            return []

        payload = event.payload
        event_type = str(payload.get("type") or "").strip()
        response_id = state.get("response_id") or f"chatcmpl_{model_name}"
        created = int(state.get("created") or 0)
        response_model = str(state.get("response_model") or model_name)
        if created == 0:
            created = int(time.time())
            state["created"] = created
        outputs: list[DownstreamChunk] = []

        if event_type == "message_start":
            message = payload.get("message") or {}
            if isinstance(message, dict):
                response_id = str(message.get("id") or response_id)
                response_model = str(message.get("model") or response_model)
                state["response_id"] = response_id
                state["response_model"] = response_model
            outputs.append(
                DownstreamChunk(
                    kind="json",
                    payload=_build_openai_delta_chunk(
                        response_model,
                        0,
                        response_id=response_id,
                        created=created,
                        delta_fields={"role": "assistant"},
                    ),
                )
            )
            return outputs

        if event_type == "content_block_start":
            content_block = payload.get("content_block") or {}
            if isinstance(content_block, dict) and content_block.get("type") == "tool_use":
                tool_calls = state.setdefault("tool_calls", {})
                tool_calls[int(payload.get("index") or 0)] = {
                    "id": str(content_block.get("id") or ""),
                    "name": str(content_block.get("name") or ""),
                    "arguments": [],
                }
            return []

        if event_type == "content_block_delta":
            delta = payload.get("delta") or {}
            if not isinstance(delta, dict):
                return []
            delta_type = str(delta.get("type") or "").strip()
            if delta_type == "text_delta" and isinstance(delta.get("text"), str):
                outputs.append(
                    DownstreamChunk(
                        kind="json",
                        payload=_build_openai_delta_chunk(
                            response_model,
                            0,
                            response_id=response_id,
                            created=created,
                            content=delta["text"],
                        ),
                    )
                )
            elif delta_type == "thinking_delta" and isinstance(delta.get("thinking"), str):
                outputs.append(
                    DownstreamChunk(
                        kind="json",
                        payload=_build_openai_delta_chunk(
                            response_model,
                            0,
                            response_id=response_id,
                            created=created,
                            delta_fields={"reasoning_content": delta["thinking"]},
                        ),
                    )
                )
            elif delta_type == "input_json_delta":
                tool_calls = state.setdefault("tool_calls", {})
                accumulator = tool_calls.get(int(payload.get("index") or 0))
                if accumulator is not None and isinstance(delta.get("partial_json"), str):
                    accumulator["arguments"].append(delta["partial_json"])
            return outputs

        if event_type == "content_block_stop":
            tool_calls = state.setdefault("tool_calls", {})
            index = int(payload.get("index") or 0)
            accumulator = tool_calls.pop(index, None)
            if accumulator:
                arguments = "".join(accumulator.get("arguments", [])) or "{}"
                outputs.append(
                    DownstreamChunk(
                        kind="json",
                        payload=_build_openai_delta_chunk(
                            response_model,
                            0,
                            response_id=response_id,
                            created=created,
                            tool_call={
                                "index": index,
                                "id": accumulator.get("id") or f"toolu_{index}",
                                "type": "function",
                                "function": {
                                    "name": accumulator.get("name") or "",
                                    "arguments": arguments,
                                },
                            },
                        ),
                    )
                )
            return outputs

        if event_type == "message_delta":
            delta = payload.get("delta") or {}
            usage_chunk = _build_openai_usage_chunk_from_usage(
                _extract_claude_usage(payload.get("usage")),
                response_model,
                response_id=response_id,
                created=created,
            )
            if isinstance(delta, dict):
                finish_reason = _map_claude_stop_reason(delta.get("stop_reason"))
                if finish_reason:
                    outputs.append(
                        DownstreamChunk(
                            kind="json",
                            payload=_build_openai_delta_chunk(
                                response_model,
                                0,
                                response_id=response_id,
                                created=created,
                                finish_reason=finish_reason,
                            ),
                        )
                    )
            if usage_chunk is not None:
                outputs.append(usage_chunk)
            return outputs

        if event_type == "error":
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                outputs.append(
                    DownstreamChunk(
                        kind="json",
                        payload={
                            "error": {
                                "message": error_payload.get("message") or "Upstream Claude error",
                                "type": error_payload.get("type") or "upstream_error",
                                "param": None,
                                "code": error_payload.get("code"),
                            }
                        },
                    )
                )
            return outputs

        return outputs

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        del original_request, translated_request
        if not isinstance(payload, dict):
            return payload

        response_model = str(payload.get("model") or model_name)
        message: Dict[str, Any] = {"role": "assistant", "content": ""}
        tool_calls = []
        reasoning_parts: list[str] = []
        for block in payload.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip().lower()
            if block_type == "text" and isinstance(block.get("text"), str):
                message["content"] += block["text"]
            elif block_type == "thinking" and isinstance(block.get("thinking"), str):
                reasoning_parts.append(block["thinking"])
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": str(block.get("id") or ""),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name") or ""),
                            "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                        },
                    }
                )
        if tool_calls:
            message["tool_calls"] = tool_calls
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)

        response = {
            "id": payload.get("id") or f"chatcmpl_{response_model}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": response_model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": _map_claude_stop_reason(payload.get("stop_reason")) or "stop",
                }
            ],
        }
        usage = _extract_claude_usage(payload.get("usage"))
        if usage:
            response["usage"] = usage
        return response


@dataclass(frozen=True, slots=True)
class ClaudePassthroughTranslator:
    source_format: str = "claude_chat"
    target_format: str = "claude_chat"

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        translated = dict(body)
        translated["model"] = model_name
        translated["stream"] = bool(stream)
        return translated

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        del model_name, original_request, translated_request, state
        if event.kind == "done":
            return []
        if event.kind == "json":
            event_name = event.event
            if not event_name and isinstance(event.payload, dict):
                event_name = str(event.payload.get("type") or "").strip() or None
            return [DownstreamChunk(kind="json", payload=event.payload, event=event_name)]
        return [DownstreamChunk(kind="text", payload=event.payload, event=event.event)]

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        del model_name, original_request, translated_request
        return payload


@dataclass(frozen=True, slots=True)
class OpenAIChatClaudeTranslator:
    source_format: str = "openai_chat"
    target_format: str = "claude_chat"

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        return _convert_claude_request_to_openai_chat_request(model_name, body, stream)

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        return _translate_openai_chat_downstream_chunk_to_claude(
            model_name,
            original_request,
            translated_request,
            DownstreamChunk(kind=event.kind, payload=event.payload, event=event.event),
            state.setdefault("claude_bridge", {}),
        )

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        return _convert_openai_chat_response_to_claude(
            model_name,
            original_request,
            translated_request,
            payload,
        )


@dataclass(frozen=True, slots=True)
class CodexChatTranslator:
    source_format: str = "codex"
    target_format: str = "openai_chat"

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        responses_request = OpenAIResponsesTranslator().translate_request(model_name, body, stream)
        return _normalize_codex_request(responses_request, model_name, stream)

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        return OpenAIResponsesTranslator().translate_stream_event(
            model_name,
            original_request,
            translated_request,
            event,
            state,
        )

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        return OpenAIResponsesTranslator().translate_nonstream_response(
            model_name,
            original_request,
            translated_request,
            _unwrap_codex_nonstream_payload(payload),
        )


@dataclass(frozen=True, slots=True)
class CodexPassthroughTranslator:
    source_format: str = "codex"
    target_format: str = "codex"

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        return _normalize_codex_request(body, model_name, stream)

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        del model_name, original_request, translated_request, state
        if event.kind == "done":
            return []
        if event.kind == "json":
            event_name = event.event
            if not event_name and isinstance(event.payload, dict):
                event_name = str(event.payload.get("type") or "").strip() or None
            return [DownstreamChunk(kind="json", payload=event.payload, event=event_name)]
        return [DownstreamChunk(kind="text", payload=event.payload, event=event.event)]

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        del model_name, original_request, translated_request
        return _unwrap_codex_nonstream_payload(payload)


@dataclass(frozen=True, slots=True)
class OpenAIChatCodexTranslator:
    source_format: str = "openai_chat"
    target_format: str = "codex"

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        return _convert_codex_request_to_openai_chat_request(model_name, body, stream)

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        return _translate_openai_chat_downstream_chunk_to_responses(
            model_name,
            original_request,
            translated_request,
            DownstreamChunk(kind=event.kind, payload=event.payload, event=event.event),
            state.setdefault("responses_bridge", {}),
        )

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        return _convert_openai_chat_response_to_responses(
            model_name,
            original_request,
            translated_request,
            payload,
        )


@dataclass(frozen=True, slots=True)
class ComposedTranslator:
    source_format: str
    target_format: str
    source_to_chat: Translator
    chat_to_target: Translator

    def translate_request(self, model_name: str, body: Dict[str, Any], stream: bool) -> Dict[str, Any]:
        chat_request = self.chat_to_target.translate_request(model_name, body, stream)
        return self.source_to_chat.translate_request(model_name, chat_request, stream)

    def translate_stream_event(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        event: StreamEvent,
        state: Dict[str, Any],
    ) -> list[DownstreamChunk]:
        chat_request = state.setdefault(
            "chat_request",
            self.chat_to_target.translate_request(
                model_name,
                original_request,
                bool(original_request.get("stream", False)),
            ),
        )
        chat_state = state.setdefault("chat_state", {})
        target_state = state.setdefault("target_state", {})
        chat_chunks = self.source_to_chat.translate_stream_event(
            model_name,
            chat_request,
            translated_request,
            event,
            chat_state,
        )
        outputs: list[DownstreamChunk] = []
        for chat_chunk in chat_chunks:
            outputs.extend(
                self.chat_to_target.translate_stream_event(
                    model_name,
                    original_request,
                    chat_request,
                    StreamEvent(
                        kind=chat_chunk.kind,
                        payload=chat_chunk.payload,
                        event=chat_chunk.event,
                    ),
                    target_state,
                )
            )
        return outputs

    def translate_nonstream_response(
        self,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        payload: Any,
    ) -> Any:
        chat_request = self.chat_to_target.translate_request(
            model_name,
            original_request,
            bool(original_request.get("stream", False)),
        )
        chat_response = self.source_to_chat.translate_nonstream_response(
            model_name,
            chat_request,
            translated_request,
            payload,
        )
        return self.chat_to_target.translate_nonstream_response(
            model_name,
            original_request,
            chat_request,
            chat_response,
        )


class TranslatorRegistry:
    def __init__(self) -> None:
        self._translators: Dict[str, Dict[str, Translator]] = {}

    def register(self, translator: Translator) -> None:
        source_key = str(translator.source_format).strip().lower()
        target_key = str(translator.target_format).strip().lower()
        self._translators.setdefault(source_key, {})[target_key] = translator

    def get(self, source_format: str, target_format: str) -> Translator:
        source_key = str(source_format or "").strip().lower()
        target_key = str(target_format or "").strip().lower()
        translator = self._translators.get(source_key, {}).get(target_key)
        if translator is None:
            raise ValueError(f"Unsupported translator pair: {source_format} -> {target_format}")
        return translator


def build_default_translator_registry() -> TranslatorRegistry:
    registry = TranslatorRegistry()

    openai_chat = OpenAIChatTranslator()
    openai_responses = OpenAIResponsesTranslator()
    openai_responses_passthrough = OpenAIResponsesPassthroughTranslator()
    openai_chat_responses = OpenAIChatResponsesTranslator()
    claude_chat = ClaudeChatTranslator()
    claude_passthrough = ClaudePassthroughTranslator()
    openai_chat_claude = OpenAIChatClaudeTranslator()
    codex_chat = CodexChatTranslator()
    codex_passthrough = CodexPassthroughTranslator()
    openai_chat_codex = OpenAIChatCodexTranslator()

    builtin_translators: tuple[Translator, ...] = (
        openai_chat,
        openai_responses,
        openai_responses_passthrough,
        openai_chat_responses,
        claude_chat,
        claude_passthrough,
        openai_chat_claude,
        codex_chat,
        codex_passthrough,
        openai_chat_codex,
    )
    for translator in builtin_translators:
        registry.register(translator)

    composed_translators: tuple[Translator, ...] = (
        ComposedTranslator("openai_responses", "claude_chat", openai_responses, openai_chat_claude),
        ComposedTranslator("openai_responses", "codex", openai_responses, openai_chat_codex),
        ComposedTranslator("claude_chat", "openai_responses", claude_chat, openai_chat_responses),
        ComposedTranslator("claude_chat", "codex", claude_chat, openai_chat_codex),
        ComposedTranslator("codex", "openai_responses", codex_chat, openai_chat_responses),
        ComposedTranslator("codex", "claude_chat", codex_chat, openai_chat_claude),
    )
    for translator in composed_translators:
        registry.register(translator)

    return registry


def _normalize_codex_request(request: Dict[str, Any], model_name: str, stream: bool) -> Dict[str, Any]:
    normalized = dict(request)
    normalized["model"] = model_name
    normalized["stream"] = bool(stream)
    normalized["store"] = False
    normalized["parallel_tool_calls"] = True
    normalized["include"] = ["reasoning.encrypted_content"]

    for field in (
        "max_output_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
        "truncation",
        "user",
    ):
        normalized.pop(field, None)

    if normalized.get("service_tier") != "priority":
        normalized.pop("service_tier", None)

    input_items = normalized.get("input")
    if isinstance(input_items, str):
        normalized["input"] = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": input_items}],
            }
        ]
    elif isinstance(input_items, list):
        rewritten_input = []
        for item in input_items:
            if not isinstance(item, dict):
                rewritten_input.append(item)
                continue
            rewritten = dict(item)
            if (
                str(rewritten.get("type") or "").strip().lower() == "message"
                and str(rewritten.get("role") or "").strip().lower() == "system"
            ):
                rewritten["role"] = "developer"
            rewritten_input.append(rewritten)
        normalized["input"] = rewritten_input

    return normalized


def _unwrap_codex_nonstream_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    response = payload.get("response")
    payload_type = str(payload.get("type") or "").strip().lower()
    if isinstance(response, dict) and payload_type == "response.completed":
        return response
    return payload


def _to_claude_content_blocks(content: Any) -> list[Dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []

    blocks: list[Dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"text", "input_text"} and isinstance(item.get("text"), str):
            blocks.append({"type": "text", "text": item["text"]})
        elif item_type == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                blocks.append({"type": "text", "text": f"[image] {image_url['url']}"})
    return blocks


def _to_claude_tool_use_blocks(tool_calls: Any) -> list[Dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []
    blocks = []
    for item in tool_calls:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict) or not function.get("name"):
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                parsed_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                parsed_arguments = {}
        elif isinstance(arguments, dict):
            parsed_arguments = arguments
        else:
            parsed_arguments = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": str(item.get("id") or f"toolu_{function['name']}"),
                "name": str(function.get("name")),
                "input": parsed_arguments,
            }
        )
    return blocks


def _to_claude_tools(tools: Any) -> list[Dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    translated = []
    for tool in tools:
        if not isinstance(tool, dict) or str(tool.get("type") or "").strip().lower() != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict) or not function.get("name"):
            continue
        translated.append(
            {
                "name": str(function.get("name")),
                "description": str(function.get("description") or ""),
                "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return translated


def _to_claude_tool_choice(tool_choice: Any) -> Optional[Dict[str, Any]]:
    if tool_choice in (None, ""):
        return None
    if isinstance(tool_choice, str):
        normalized = tool_choice.strip().lower()
        if normalized == "auto":
            return {"type": "auto"}
        if normalized == "none":
            return {"type": "auto", "disable_parallel_tool_use": True}
        if normalized in {"required", "any"}:
            return {"type": "any"}
        return None
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function")
        if isinstance(function, dict) and function.get("name"):
            return {"type": "tool", "name": str(function.get("name"))}
    return None


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _normalize_tool_result_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False)
    return str(content or "")


def _to_openai_responses_input(messages: Any) -> tuple[str, list[Dict[str, Any]]]:
    if not isinstance(messages, list):
        return "", []

    instructions: list[str] = []
    items: list[Dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role == "system":
            text = _extract_text_content(message.get("content"))
            if text:
                instructions.append(text)
            continue
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "").strip()
            if tool_call_id:
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call_id,
                        "output": _normalize_tool_result_content(message.get("content")),
                    }
                )
            continue

        content = _to_openai_responses_message_content(message.get("content"), role)
        if content:
            items.append(
                {
                    "type": "message",
                    "role": "assistant" if role == "assistant" else "user",
                    "content": content,
                }
            )

        if role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict) or not function.get("name"):
                    continue
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(tool_call.get("id") or ""),
                        "name": str(function.get("name")),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                )

    return "\n\n".join(part for part in instructions if part), items


def _to_openai_responses_message_content(content: Any, role: str) -> list[Dict[str, Any]]:
    content_type = "output_text" if str(role).strip().lower() == "assistant" else "input_text"
    if isinstance(content, str):
        return [{"type": content_type, "text": content}]
    if not isinstance(content, list):
        return []

    translated: list[Dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"text", "input_text", "output_text"} and isinstance(item.get("text"), str):
            translated.append({"type": content_type, "text": item.get("text")})
        elif item_type == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                translated.append({"type": "input_image", "image_url": image_url["url"]})
    return translated


def _to_openai_responses_tools(tools: Any) -> list[Dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    translated: list[Dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict) or str(tool.get("type") or "").strip().lower() != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict) or not function.get("name"):
            continue
        translated.append(
            {
                "type": "function",
                "name": str(function.get("name")),
                "description": str(function.get("description") or ""),
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return translated


def _to_openai_responses_tool_choice(tool_choice: Any) -> Any:
    if tool_choice in (None, ""):
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function")
        if isinstance(function, dict) and function.get("name"):
            return {
                "type": "function",
                "name": str(function.get("name")),
            }
    return tool_choice


def _extract_openai_responses_usage(usage: Any) -> Dict[str, int]:
    if not isinstance(usage, dict):
        return {}

    prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    if prompt_tokens <= 0 and completion_tokens <= 0 and total_tokens <= 0:
        return {}
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _extract_openai_responses_message_and_tool_calls(outputs: Any) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    message: Dict[str, Any] = {"role": "assistant", "content": ""}
    tool_calls: list[Dict[str, Any]] = []

    if not isinstance(outputs, list):
        return message, tool_calls

    for item in outputs:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "").strip().lower()
                if part_type in {"output_text", "text"} and isinstance(part.get("text"), str):
                    message["content"] += part["text"]
        elif item_type == "function_call":
            tool_calls.append(
                {
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or "{}"),
                    },
                }
            )

    return message, tool_calls


def _extract_openai_responses_reasoning_content(outputs: Any) -> str:
    if not isinstance(outputs, list):
        return ""

    parts: list[str] = []
    for item in outputs:
        if not isinstance(item, dict) or str(item.get("type") or "").strip().lower() != "reasoning":
            continue
        for summary in item.get("summary") or []:
            if isinstance(summary, dict) and isinstance(summary.get("text"), str):
                parts.append(summary["text"])
    return "".join(parts)


def _resolve_openai_responses_finish_reason(*, saw_text: bool, saw_tool_call: bool) -> str:
    if saw_tool_call and not saw_text:
        return "tool_calls"
    return "stop"


def _build_openai_delta_chunk(
    model: str,
    index: int,
    *,
    content: Optional[str] = None,
    tool_call: Optional[Dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
    response_id: Optional[str] = None,
    created: int = 0,
    delta_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    delta: Dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if tool_call is not None:
        delta["tool_calls"] = [tool_call]
    if delta_fields:
        delta.update(delta_fields)
    return {
        "id": response_id or f"chatcmpl_{model}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": index,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


def _build_openai_usage_chunk_from_usage(
    usage: Dict[str, Any],
    model: str,
    *,
    response_id: Optional[str] = None,
    created: int = 0,
) -> Optional[DownstreamChunk]:
    if not usage:
        return None
    return DownstreamChunk(
        kind="json",
        payload={
            "id": response_id or f"chatcmpl_{model}",
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [],
            "usage": usage,
        },
    )


def _extract_claude_usage(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    prompt_tokens = int(payload.get("input_tokens") or 0)
    completion_tokens = int(payload.get("output_tokens") or 0)
    usage: Dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    cached_tokens = int(payload.get("cache_read_input_tokens") or 0)
    if cached_tokens > 0:
        usage.setdefault("prompt_tokens_details", {})["cached_tokens"] = cached_tokens
    reasoning_tokens = int(payload.get("thinking_tokens") or 0)
    if reasoning_tokens > 0:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return usage


def _map_claude_stop_reason(reason: Any) -> Optional[str]:
    if reason in (None, ""):
        return None
    normalized = str(reason).strip().lower()
    if normalized == "end_turn":
        return "stop"
    if normalized == "tool_use":
        return "tool_calls"
    if normalized == "max_tokens":
        return "length"
    if normalized == "stop_sequence":
        return "stop"
    return "stop"
