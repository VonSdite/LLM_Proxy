#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for bridging OpenAI chat payloads into Claude messages output."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Tuple

from ..proxy_core.contracts import DownstreamChunk
from .event_chunk_utils import build_json_event_chunk as _emit_event
from .tool_result_utils import normalize_tool_result_content


def convert_claude_request_to_openai_chat_request(
    model_name: str,
    body: Dict[str, Any],
    stream: bool,
) -> Dict[str, Any]:
    translated: Dict[str, Any] = {
        "model": model_name,
        "messages": [],
        "stream": bool(stream),
    }

    if body.get("max_tokens") is not None:
        translated["max_tokens"] = body.get("max_tokens")
    if body.get("temperature") is not None:
        translated["temperature"] = body.get("temperature")
    elif body.get("top_p") is not None:
        translated["top_p"] = body.get("top_p")

    stop_sequences = body.get("stop_sequences")
    if isinstance(stop_sequences, list):
        stops = [str(item) for item in stop_sequences if str(item).strip()]
        if len(stops) == 1:
            translated["stop"] = stops[0]
        elif stops:
            translated["stop"] = stops

    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        thinking_type = str(thinking.get("type") or "").strip().lower()
        if thinking_type == "disabled":
            translated["reasoning_effort"] = "none"
        elif thinking_type in {"enabled", "adaptive", "auto"}:
            translated["reasoning_effort"] = _budget_to_reasoning_effort(thinking.get("budget_tokens"))

    system_message = _convert_claude_system_to_openai(body.get("system"))
    if system_message is not None:
        translated["messages"].append(system_message)

    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role not in {"assistant", "user"}:
            role = "user"

        content = message.get("content")
        content_items, reasoning_parts, tool_calls, tool_results = _convert_claude_blocks_to_openai_parts(
            content,
            role,
        )

        if tool_results:
            translated["messages"].extend(tool_results)

        if content_items or reasoning_parts or tool_calls:
            openai_message: Dict[str, Any] = {
                "role": "assistant" if role == "assistant" else "user",
                "content": _compact_openai_content(content_items),
            }
            if role == "assistant" and reasoning_parts:
                openai_message["reasoning_content"] = "\n\n".join(reasoning_parts)
            if role == "assistant" and tool_calls:
                openai_message["tool_calls"] = tool_calls
            translated["messages"].append(openai_message)

    tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": str(tool.get("name")),
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    if tools:
        translated["tools"] = tools

    tool_choice = body.get("tool_choice")
    if isinstance(tool_choice, dict):
        tool_type = str(tool_choice.get("type") or "").strip().lower()
        if tool_type == "auto":
            translated["tool_choice"] = "auto"
        elif tool_type == "any":
            translated["tool_choice"] = "required"
        elif tool_type == "tool" and tool_choice.get("name"):
            translated["tool_choice"] = {
                "type": "function",
                "function": {"name": str(tool_choice.get("name"))},
            }

    return translated


def translate_openai_chat_downstream_chunk_to_claude(
    model_name: str,
    original_request: Dict[str, Any],
    translated_request: Dict[str, Any],
    chunk: DownstreamChunk,
    state: Dict[str, Any],
) -> list[DownstreamChunk]:
    if chunk.kind == "done":
        return finalize_claude_stream(model_name, original_request, translated_request, state)
    if chunk.kind == "json" and isinstance(chunk.payload, dict):
        return translate_openai_chat_stream_payload_to_claude(
            model_name,
            original_request,
            translated_request,
            chunk.payload,
            state,
        )
    if chunk.kind == "text" and isinstance(chunk.payload, str) and chunk.payload:
        synthetic_payload = {
            "id": state.get("message_id") or f"chatcmpl_{model_name}",
            "object": "chat.completion.chunk",
            "created": int(state.get("created") or time.time()),
            "model": translated_request.get("model") or model_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk.payload},
                    "finish_reason": None,
                }
            ],
        }
        return translate_openai_chat_stream_payload_to_claude(
            model_name,
            original_request,
            translated_request,
            synthetic_payload,
            state,
        )
    return []


def translate_openai_chat_stream_payload_to_claude(
    model_name: str,
    original_request: Dict[str, Any],
    translated_request: Dict[str, Any],
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> list[DownstreamChunk]:
    del original_request
    outputs: list[DownstreamChunk] = []
    outputs.extend(_ensure_message_started(model_name, translated_request, state))

    if isinstance(payload.get("error"), dict):
        error_payload = payload["error"]
        outputs.append(
            _emit_event(
                "error",
                {
                    "type": "error",
                    "error": {
                        "type": error_payload.get("type") or "upstream_error",
                        "message": error_payload.get("message") or "Upstream chat request failed",
                    },
                },
            )
        )
        state["completed"] = True
        return outputs

    response_model = payload.get("model")
    if response_model not in (None, ""):
        state["model"] = str(response_model)

    usage = payload.get("usage")
    if isinstance(usage, dict):
        state["usage"] = {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "cache_read_input_tokens": int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0),
        }

    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            delta = {}

        reasoning_content = delta.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            outputs.extend(_finalize_text_block(state))
            outputs.extend(_ensure_reasoning_open(state))
            reasoning_state = state["reasoning"]
            reasoning_state["text"] += reasoning_content
            outputs.append(
                _emit_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": reasoning_state["block_index"],
                        "delta": {
                            "type": "thinking_delta",
                            "thinking": reasoning_content,
                        },
                    },
                )
            )

        content = delta.get("content")
        if isinstance(content, str) and content:
            outputs.extend(_finalize_reasoning_block(state))
            outputs.extend(_ensure_text_open(state))
            text_state = state["text"]
            text_state["text"] += content
            outputs.append(
                _emit_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": text_state["block_index"],
                        "delta": {"type": "text_delta", "text": content},
                    },
                )
            )

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            outputs.extend(_finalize_reasoning_block(state))
            outputs.extend(_finalize_text_block(state))
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                tool_index = int(tool_call.get("index") or 0)
                function = tool_call.get("function") or {}
                if not isinstance(function, dict):
                    function = {}
                tool_state, open_events = _ensure_tool_open(
                    state,
                    tool_index,
                    str(tool_call.get("id") or ""),
                    str(function.get("name") or ""),
                )
                outputs.extend(open_events)
                arguments_delta = function.get("arguments")
                if isinstance(arguments_delta, str) and arguments_delta:
                    tool_state["arguments"] += arguments_delta
                    outputs.append(
                        _emit_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": tool_state["block_index"],
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": arguments_delta,
                                },
                            },
                        )
                    )

        finish_reason = choice.get("finish_reason")
        if finish_reason not in (None, ""):
            state["finish_reason"] = _map_openai_finish_reason_to_claude(finish_reason)

    return outputs


def finalize_claude_stream(
    model_name: str,
    original_request: Dict[str, Any],
    translated_request: Dict[str, Any],
    state: Dict[str, Any],
) -> list[DownstreamChunk]:
    del model_name, original_request, translated_request
    if not state.get("started") or state.get("completed"):
        return []

    outputs: list[DownstreamChunk] = []
    outputs.extend(_finalize_reasoning_block(state))
    outputs.extend(_finalize_text_block(state))
    outputs.extend(_finalize_tool_blocks(state))

    usage = state.get("usage") or {}
    outputs.append(
        _emit_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": state.get("finish_reason") or ("tool_use" if state.get("saw_tool_call") else "end_turn"),
                    "stop_sequence": None,
                },
                "usage": {
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    **(
                        {"cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0)}
                        if int(usage.get("cache_read_input_tokens") or 0) > 0
                        else {}
                    ),
                },
            },
        )
    )
    outputs.append(_emit_event("message_stop", {"type": "message_stop"}))
    state["completed"] = True
    return outputs


def convert_openai_chat_response_to_claude(
    model_name: str,
    original_request: Dict[str, Any],
    translated_request: Dict[str, Any],
    payload: Any,
) -> Any:
    del original_request
    if not isinstance(payload, dict):
        return payload

    message: Dict[str, Any] = {}
    finish_reason = None
    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        if isinstance(choice.get("message"), dict):
            message = choice.get("message") or {}
            finish_reason = choice.get("finish_reason")
            break

    message_id = payload.get("id") or f"msg_{model_name}_{int(time.time() * 1000)}"
    response_model = payload.get("model") or translated_request.get("model") or model_name
    content_blocks = _convert_openai_message_to_claude_blocks(message)
    if not content_blocks:
        content_blocks = [{"type": "text", "text": ""}]

    response = {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": response_model,
        "content": content_blocks,
        "stop_reason": _map_openai_finish_reason_to_claude(finish_reason),
        "stop_sequence": None,
    }
    if isinstance(payload.get("usage"), dict):
        usage_payload: Dict[str, Any] = {
            "input_tokens": int(payload["usage"].get("prompt_tokens") or 0),
            "output_tokens": int(payload["usage"].get("completion_tokens") or 0),
        }
        response["usage"] = usage_payload
        cached_tokens = int(((payload["usage"].get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        if cached_tokens > 0:
            usage_payload["cache_read_input_tokens"] = cached_tokens
    return response


def _convert_claude_system_to_openai(system: Any) -> Dict[str, Any] | None:
    if isinstance(system, str) and system.strip():
        return {"role": "system", "content": system}
    if not isinstance(system, list):
        return None

    content_items: List[Dict[str, Any]] = []
    for part in system:
        if not isinstance(part, dict):
            continue
        converted = _convert_claude_part_to_openai_content(part)
        if converted is not None:
            content_items.append(converted)
    if not content_items:
        return None
    return {"role": "system", "content": _compact_openai_content(content_items)}


def _convert_claude_blocks_to_openai_parts(content: Any, role: str) -> Tuple[List[Dict[str, Any]], List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    if isinstance(content, str):
        return ([{"type": "text", "text": content}] if content else []), [], [], []
    if not isinstance(content, list):
        return [], [], [], []

    content_items: List[Dict[str, Any]] = []
    reasoning_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    tool_results: List[Dict[str, Any]] = []

    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "").strip().lower()
        if part_type in {"text", "image"}:
            converted = _convert_claude_part_to_openai_content(part)
            if converted is not None:
                content_items.append(converted)
        elif part_type == "thinking" and role == "assistant":
            thinking_text = str(part.get("thinking") or part.get("text") or "").strip()
            if thinking_text:
                reasoning_parts.append(thinking_text)
        elif part_type == "tool_use" and role == "assistant":
            tool_calls.append(
                {
                    "id": str(part.get("id") or f"toolu_{part.get('name') or 'call'}"),
                    "type": "function",
                    "function": {
                        "name": str(part.get("name") or ""),
                        "arguments": json.dumps(part.get("input") or {}, ensure_ascii=False),
                    },
                }
            )
        elif part_type == "tool_result":
            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": str(part.get("tool_use_id") or ""),
                    "content": normalize_tool_result_content(part.get("content")),
                }
            )

    return content_items, reasoning_parts, tool_calls, tool_results


def _convert_claude_part_to_openai_content(part: Dict[str, Any]) -> Dict[str, Any] | None:
    part_type = str(part.get("type") or "").strip().lower()
    if part_type == "text":
        text = str(part.get("text") or "")
        return {"type": "text", "text": text}
    if part_type == "image":
        source = part.get("source") or {}
        if isinstance(source, dict):
            source_type = str(source.get("type") or "").strip().lower()
            if source_type == "base64" and source.get("data"):
                media_type = str(source.get("media_type") or "image/png")
                return {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{source.get('data')}",
                    },
                }
            if source_type == "url" and source.get("url"):
                return {
                    "type": "image_url",
                    "image_url": {"url": str(source.get("url"))},
                }
        return None
    return None


def _compact_openai_content(content_items: List[Dict[str, Any]]) -> Any:
    if not content_items:
        return ""
    if len(content_items) == 1 and content_items[0].get("type") == "text":
        return content_items[0].get("text") or ""
    return content_items


def _ensure_message_started(model_name: str, translated_request: Dict[str, Any], state: Dict[str, Any]) -> list[DownstreamChunk]:
    if state.get("started"):
        return []

    message_id = str(state.get("message_id") or f"msg_{model_name}_{int(time.time() * 1000)}")
    response_model = str(state.get("model") or translated_request.get("model") or model_name)
    state.update(
        {
            "started": True,
            "message_id": message_id,
            "model": response_model,
            "created": int(state.get("created") or time.time()),
            "next_block_index": int(state.get("next_block_index") or 0),
            "text": state.get("text") or {"opened": False, "closed": False, "block_index": None, "text": ""},
            "reasoning": state.get("reasoning") or {"opened": False, "closed": False, "block_index": None, "text": ""},
            "tool_blocks": state.get("tool_blocks") or {},
            "usage": state.get("usage") or {},
            "saw_tool_call": bool(state.get("saw_tool_call")),
        }
    )
    return [
        _emit_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": response_model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )
    ]


def _ensure_text_open(state: Dict[str, Any]) -> list[DownstreamChunk]:
    text_state = state.setdefault("text", {"opened": False, "closed": False, "block_index": None, "text": ""})
    if text_state.get("opened"):
        return []

    block_index = int(state.get("next_block_index") or 0)
    state["next_block_index"] = block_index + 1
    text_state.update({"opened": True, "closed": False, "block_index": block_index})
    return [
        _emit_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {"type": "text", "text": ""},
            },
        )
    ]


def _ensure_reasoning_open(state: Dict[str, Any]) -> list[DownstreamChunk]:
    reasoning_state = state.setdefault("reasoning", {"opened": False, "closed": False, "block_index": None, "text": ""})
    if reasoning_state.get("opened"):
        return []

    block_index = int(state.get("next_block_index") or 0)
    state["next_block_index"] = block_index + 1
    reasoning_state.update({"opened": True, "closed": False, "block_index": block_index})
    return [
        _emit_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {"type": "thinking", "thinking": ""},
            },
        )
    ]


def _ensure_tool_open(
    state: Dict[str, Any],
    tool_index: int,
    tool_call_id: str,
    tool_name: str,
) -> Tuple[Dict[str, Any], list[DownstreamChunk]]:
    tool_blocks = state.setdefault("tool_blocks", {})
    tool_state = tool_blocks.get(tool_index)
    if tool_state is None:
        block_index = int(state.get("next_block_index") or 0)
        state["next_block_index"] = block_index + 1
        tool_state = {
            "opened": False,
            "closed": False,
            "block_index": block_index,
            "id": tool_call_id or f"toolu_{tool_index}",
            "name": tool_name,
            "arguments": "",
        }
        tool_blocks[tool_index] = tool_state

    if tool_call_id:
        tool_state["id"] = tool_call_id
    if tool_name:
        tool_state["name"] = tool_name

    if tool_state.get("opened"):
        return tool_state, []

    state["saw_tool_call"] = True
    tool_state["opened"] = True
    return tool_state, [
        _emit_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": tool_state["block_index"],
                "content_block": {
                    "type": "tool_use",
                    "id": tool_state["id"],
                    "name": tool_state["name"],
                    "input": {},
                },
            },
        )
    ]


def _finalize_text_block(state: Dict[str, Any]) -> list[DownstreamChunk]:
    text_state = state.get("text") or {}
    if not text_state.get("opened") or text_state.get("closed"):
        return []
    text_state["closed"] = True
    text_state["opened"] = False
    return [
        _emit_event(
            "content_block_stop",
            {
                "type": "content_block_stop",
                "index": text_state["block_index"],
            },
        )
    ]


def _finalize_reasoning_block(state: Dict[str, Any]) -> list[DownstreamChunk]:
    reasoning_state = state.get("reasoning") or {}
    if not reasoning_state.get("opened") or reasoning_state.get("closed"):
        return []
    reasoning_state["closed"] = True
    reasoning_state["opened"] = False
    return [
        _emit_event(
            "content_block_stop",
            {
                "type": "content_block_stop",
                "index": reasoning_state["block_index"],
            },
        )
    ]


def _finalize_tool_blocks(state: Dict[str, Any]) -> list[DownstreamChunk]:
    outputs: list[DownstreamChunk] = []
    for tool_index in sorted((state.get("tool_blocks") or {}).keys()):
        tool_state = state["tool_blocks"][tool_index]
        if tool_state.get("closed") or not tool_state.get("opened"):
            continue
        outputs.append(
            _emit_event(
                "content_block_stop",
                {
                    "type": "content_block_stop",
                    "index": tool_state["block_index"],
                },
            )
        )
        tool_state["closed"] = True
        tool_state["opened"] = False
    return outputs


def _convert_openai_message_to_claude_blocks(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    content_blocks: List[Dict[str, Any]] = []
    reasoning_content = str(message.get("reasoning_content") or "").strip()
    if reasoning_content:
        content_blocks.append({"type": "thinking", "thinking": reasoning_content})

    content = message.get("content")
    if isinstance(content, str):
        if content:
            content_blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type in {"text", "input_text", "output_text"} and isinstance(item.get("text"), str):
                content_blocks.append({"type": "text", "text": item.get("text")})

    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        if not isinstance(function, dict):
            function = {}
        content_blocks.append(
            {
                "type": "tool_use",
                "id": str(tool_call.get("id") or f"toolu_{function.get('name') or 'call'}"),
                "name": str(function.get("name") or ""),
                "input": _coerce_tool_input(function.get("arguments")),
            }
        )
    return content_blocks


def _coerce_tool_input(arguments: Any) -> Dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"raw": arguments}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {}
def _budget_to_reasoning_effort(budget_tokens: Any) -> str:
    try:
        budget = int(budget_tokens)
    except (TypeError, ValueError):
        return "high"
    if budget <= 0:
        return "none"
    if budget < 2048:
        return "low"
    if budget < 8192:
        return "medium"
    return "high"


def _map_openai_finish_reason_to_claude(reason: Any) -> str:
    normalized = str(reason or "").strip().lower()
    if normalized == "tool_calls":
        return "tool_use"
    if normalized == "length":
        return "max_tokens"
    return "end_turn"
