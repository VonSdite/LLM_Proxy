#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for bridging OpenAI chat payloads into OpenAI responses output."""

from __future__ import annotations

import time
from typing import Any, Dict

from ..proxy_core.contracts import DownstreamChunk


def convert_openai_responses_request_to_chat_request(
    model_name: str,
    body: Dict[str, Any],
    stream: bool,
) -> Dict[str, Any]:
    translated: Dict[str, Any] = {
        "model": model_name,
        "messages": [],
        "stream": bool(stream),
    }

    if body.get("instructions") not in (None, ""):
        translated["messages"].append(
            {
                "role": "system",
                "content": str(body.get("instructions")),
            }
        )

    input_items = body.get("input")
    if isinstance(input_items, str):
        translated["messages"].append({"role": "user", "content": input_items})
    elif isinstance(input_items, list):
        for item in input_items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            role = str(item.get("role") or "").strip().lower()
            if item_type in {"", "message"}:
                if role == "developer":
                    role = "user"
                if role not in {"system", "assistant"}:
                    role = "user"
                content = _from_openai_responses_message_content(item.get("content"))
                translated["messages"].append(
                    {
                        "role": role,
                        "content": content if content else "",
                    }
                )
            elif item_type == "function_call":
                translated["messages"].append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": str(item.get("call_id") or item.get("id") or ""),
                                "type": "function",
                                "function": {
                                    "name": str(item.get("name") or ""),
                                    "arguments": str(item.get("arguments") or "{}"),
                                },
                            }
                        ],
                    }
                )
            elif item_type == "function_call_output":
                translated["messages"].append(
                    {
                        "role": "tool",
                        "tool_call_id": str(item.get("call_id") or ""),
                        "content": _normalize_tool_result_content(item.get("output")),
                    }
                )

    if body.get("max_output_tokens") is not None:
        translated["max_tokens"] = body.get("max_output_tokens")
    if body.get("temperature") is not None:
        translated["temperature"] = body.get("temperature")
    if body.get("top_p") is not None:
        translated["top_p"] = body.get("top_p")
    if body.get("parallel_tool_calls") is not None:
        translated["parallel_tool_calls"] = body.get("parallel_tool_calls")
    if body.get("user") is not None:
        translated["user"] = body.get("user")
    if body.get("metadata") is not None:
        translated["metadata"] = body.get("metadata")

    chat_tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "").strip().lower()
        if tool_type != "function":
            continue
        chat_tools.append(
            {
                "type": "function",
                "function": {
                    "name": str(tool.get("name") or ""),
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                },
            }
        )
    if chat_tools:
        translated["tools"] = chat_tools

    tool_choice = body.get("tool_choice")
    if isinstance(tool_choice, dict) and str(tool_choice.get("type") or "").strip().lower() == "function":
        translated["tool_choice"] = {
            "type": "function",
            "function": {"name": str(tool_choice.get("name") or "")},
        }
    elif tool_choice not in (None, ""):
        translated["tool_choice"] = tool_choice

    return translated


def _from_openai_responses_message_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    translated = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"input_text", "output_text", "text"} and isinstance(item.get("text"), str):
            translated.append({"type": "text", "text": item.get("text")})
        elif item_type == "input_image" and isinstance(item.get("image_url"), str):
            translated.append({"type": "image_url", "image_url": {"url": item.get("image_url")}})
    return translated


def _normalize_tool_result_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        import json

        return json.dumps(content, ensure_ascii=False)
    return str(content or "")


def translate_openai_chat_downstream_chunk_to_responses(
    model_name: str,
    original_request: Dict[str, Any],
    translated_request: Dict[str, Any],
    chunk: DownstreamChunk,
    state: Dict[str, Any],
) -> list[DownstreamChunk]:
    if chunk.kind == "done":
        return finalize_openai_responses_stream(model_name, original_request, translated_request, state)
    if chunk.kind == "json" and isinstance(chunk.payload, dict):
        return translate_openai_chat_stream_payload_to_responses(
            model_name,
            original_request,
            translated_request,
            chunk.payload,
            state,
        )
    if chunk.kind == "text" and isinstance(chunk.payload, str) and chunk.payload:
        synthetic_payload = {
            "id": state.get("response_id") or f"chatcmpl_{model_name}",
            "object": "chat.completion.chunk",
            "created": int(state.get("created") or 0),
            "model": translated_request.get("model") or model_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk.payload},
                    "finish_reason": None,
                }
            ],
        }
        return translate_openai_chat_stream_payload_to_responses(
            model_name,
            original_request,
            translated_request,
            synthetic_payload,
            state,
        )
    return []


def translate_openai_chat_stream_payload_to_responses(
    model_name: str,
    original_request: Dict[str, Any],
    translated_request: Dict[str, Any],
    payload: Dict[str, Any],
    state: Dict[str, Any],
) -> list[DownstreamChunk]:
    outputs: list[DownstreamChunk] = []
    outputs.extend(_ensure_stream_started(model_name, translated_request, state))

    if isinstance(payload.get("error"), dict):
        error_payload = payload["error"]
        failed_payload = {
            "type": "response.failed",
            "sequence_number": _next_sequence(state),
            "response": {
                "id": state["response_id"],
                "object": "response",
                "created_at": state["created"],
                "status": "failed",
                "error": {
                    "message": error_payload.get("message") or "Upstream chat stream failed",
                    "type": error_payload.get("type") or "upstream_error",
                    "code": error_payload.get("code"),
                },
            },
        }
        state["completed"] = True
        outputs.append(_emit_event("response.failed", failed_payload))
        return outputs

    usage = payload.get("usage")
    if isinstance(usage, dict):
        state["usage"] = {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }

    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            delta = {}

        reasoning_content = delta.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            outputs.extend(_ensure_reasoning_open(state))
            reasoning_state = state["reasoning"]
            reasoning_state["text"] += reasoning_content
            outputs.append(
                _emit_event(
                    "response.reasoning_summary_text.delta",
                    {
                        "type": "response.reasoning_summary_text.delta",
                        "sequence_number": _next_sequence(state),
                        "item_id": reasoning_state["item_id"],
                        "output_index": reasoning_state["output_index"],
                        "summary_index": 0,
                        "delta": reasoning_content,
                    },
                )
            )

        content = delta.get("content")
        if isinstance(content, str) and content:
            if state.get("reasoning", {}).get("opened") and not state.get("reasoning", {}).get("done"):
                outputs.extend(_finalize_reasoning(state))
            outputs.extend(_ensure_message_open(state))
            message_state = state["message"]
            message_state["content"] += content
            outputs.append(
                _emit_event(
                    "response.output_text.delta",
                    {
                        "type": "response.output_text.delta",
                        "sequence_number": _next_sequence(state),
                        "item_id": message_state["item_id"],
                        "output_index": message_state["output_index"],
                        "content_index": 0,
                        "delta": content,
                        "logprobs": [],
                    },
                )
            )

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            if state.get("reasoning", {}).get("opened") and not state.get("reasoning", {}).get("done"):
                outputs.extend(_finalize_reasoning(state))
            if state.get("message", {}).get("opened") and not state.get("message", {}).get("done"):
                outputs.extend(_finalize_message(state))
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
                if function.get("name"):
                    tool_state["name"] = str(function.get("name"))
                arguments_delta = function.get("arguments")
                if isinstance(arguments_delta, str) and arguments_delta:
                    tool_state["arguments"] += arguments_delta
                    outputs.append(
                        _emit_event(
                            "response.function_call_arguments.delta",
                            {
                                "type": "response.function_call_arguments.delta",
                                "sequence_number": _next_sequence(state),
                                "item_id": tool_state["item_id"],
                                "output_index": tool_state["output_index"],
                                "delta": arguments_delta,
                            },
                        )
                    )

    return outputs


def _ensure_stream_started(
    model_name: str,
    translated_request: Dict[str, Any],
    state: Dict[str, Any],
) -> list[DownstreamChunk]:
    if state.get("started"):
        return []

    response_id = str(state.get("response_id") or f"resp_{model_name}_{int(time.time() * 1000)}")
    created = int(state.get("created") or time.time())
    response_model = str(state.get("response_model") or translated_request.get("model") or model_name)
    state.update(
        {
            "started": True,
            "response_id": response_id,
            "created": created,
            "response_model": response_model,
            "seq": int(state.get("seq") or 0),
            "next_output_index": int(state.get("next_output_index") or 0),
            "usage": state.get("usage") or {},
            "message": state.get("message") or {"opened": False, "done": False, "content": "", "output_index": None, "item_id": None},
            "reasoning": state.get("reasoning") or {"opened": False, "done": False, "text": "", "output_index": None, "item_id": None},
            "tool_calls": state.get("tool_calls") or {},
        }
    )
    return [
        _emit_event(
            "response.created",
            {
                "type": "response.created",
                "sequence_number": _next_sequence(state),
                "response": {
                    "id": response_id,
                    "object": "response",
                    "created_at": created,
                    "status": "in_progress",
                    "background": False,
                    "error": None,
                    "output": [],
                    "model": response_model,
                },
            },
        ),
        _emit_event(
            "response.in_progress",
            {
                "type": "response.in_progress",
                "sequence_number": _next_sequence(state),
                "response": {
                    "id": response_id,
                    "object": "response",
                    "created_at": created,
                    "status": "in_progress",
                    "model": response_model,
                },
            },
        ),
    ]


def _ensure_message_open(state: Dict[str, Any]) -> list[DownstreamChunk]:
    message_state = state.setdefault("message", {"opened": False, "done": False, "content": "", "output_index": None, "item_id": None})
    if message_state.get("opened"):
        return []

    output_index = int(state.get("next_output_index") or 0)
    state["next_output_index"] = output_index + 1
    item_id = f"msg_{state['response_id']}_{output_index}"
    message_state.update({"opened": True, "done": False, "output_index": output_index, "item_id": item_id})
    return [
        _emit_event(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "sequence_number": _next_sequence(state),
                "output_index": output_index,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "status": "in_progress",
                    "content": [],
                    "role": "assistant",
                },
            },
        ),
        _emit_event(
            "response.content_part.added",
            {
                "type": "response.content_part.added",
                "sequence_number": _next_sequence(state),
                "item_id": item_id,
                "output_index": output_index,
                "content_index": 0,
                "part": {"type": "output_text", "annotations": [], "logprobs": [], "text": ""},
            },
        ),
    ]


def _ensure_reasoning_open(state: Dict[str, Any]) -> list[DownstreamChunk]:
    reasoning_state = state.setdefault("reasoning", {"opened": False, "done": False, "text": "", "output_index": None, "item_id": None})
    if reasoning_state.get("opened"):
        return []

    output_index = int(state.get("next_output_index") or 0)
    state["next_output_index"] = output_index + 1
    item_id = f"rs_{state['response_id']}_{output_index}"
    reasoning_state.update({"opened": True, "done": False, "output_index": output_index, "item_id": item_id})
    return [
        _emit_event(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "sequence_number": _next_sequence(state),
                "output_index": output_index,
                "item": {"id": item_id, "type": "reasoning", "status": "in_progress", "summary": []},
            },
        ),
        _emit_event(
            "response.reasoning_summary_part.added",
            {
                "type": "response.reasoning_summary_part.added",
                "sequence_number": _next_sequence(state),
                "item_id": item_id,
                "output_index": output_index,
                "summary_index": 0,
                "part": {"type": "summary_text", "text": ""},
            },
        ),
    ]


def _ensure_tool_open(
    state: Dict[str, Any],
    tool_index: int,
    tool_call_id: str,
    tool_name: str,
) -> tuple[Dict[str, Any], list[DownstreamChunk]]:
    tool_calls = state.setdefault("tool_calls", {})
    tool_state = tool_calls.get(tool_index)
    if tool_state is None:
        output_index = int(state.get("next_output_index") or 0)
        state["next_output_index"] = output_index + 1
        resolved_call_id = tool_call_id or f"call_{state['response_id']}_{tool_index}"
        tool_state = {
            "opened": False,
            "done": False,
            "output_index": output_index,
            "item_id": f"fc_{resolved_call_id}",
            "call_id": resolved_call_id,
            "name": tool_name,
            "arguments": "",
        }
        tool_calls[tool_index] = tool_state

    if tool_call_id:
        tool_state["call_id"] = tool_call_id
        tool_state["item_id"] = f"fc_{tool_call_id}"
    if tool_name:
        tool_state["name"] = tool_name

    if tool_state.get("opened"):
        return tool_state, []

    tool_state["opened"] = True
    return tool_state, [
        _emit_event(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "sequence_number": _next_sequence(state),
                "output_index": tool_state["output_index"],
                "item": {
                    "id": tool_state["item_id"],
                    "type": "function_call",
                    "status": "in_progress",
                    "arguments": "",
                    "call_id": tool_state["call_id"],
                    "name": tool_state["name"],
                },
            },
        )
    ]


def finalize_openai_responses_stream(
    model_name: str,
    original_request: Dict[str, Any],
    translated_request: Dict[str, Any],
    state: Dict[str, Any],
) -> list[DownstreamChunk]:
    if not state.get("started") or state.get("completed"):
        return []

    outputs: list[DownstreamChunk] = []
    outputs.extend(_finalize_reasoning(state))
    outputs.extend(_finalize_message(state))
    outputs.extend(_finalize_tool_calls(state))
    outputs.append(
        _emit_event(
            "response.completed",
            _build_completed_payload(model_name, original_request, translated_request, state),
        )
    )
    state["completed"] = True
    return outputs


def _finalize_message(state: Dict[str, Any]) -> list[DownstreamChunk]:
    message_state = state.get("message") or {}
    if not message_state.get("opened") or message_state.get("done"):
        return []

    text = str(message_state.get("content") or "")
    output_index = int(message_state.get("output_index") or 0)
    item_id = str(message_state.get("item_id") or "")
    message_state["done"] = True
    return [
        _emit_event("response.output_text.done", {"type": "response.output_text.done", "sequence_number": _next_sequence(state), "item_id": item_id, "output_index": output_index, "content_index": 0, "text": text, "logprobs": []}),
        _emit_event("response.content_part.done", {"type": "response.content_part.done", "sequence_number": _next_sequence(state), "item_id": item_id, "output_index": output_index, "content_index": 0, "part": {"type": "output_text", "annotations": [], "logprobs": [], "text": text}}),
        _emit_event("response.output_item.done", {"type": "response.output_item.done", "sequence_number": _next_sequence(state), "output_index": output_index, "item": {"id": item_id, "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "annotations": [], "logprobs": [], "text": text}]}}),
    ]


def _finalize_reasoning(state: Dict[str, Any]) -> list[DownstreamChunk]:
    reasoning_state = state.get("reasoning") or {}
    if not reasoning_state.get("opened") or reasoning_state.get("done"):
        return []

    text = str(reasoning_state.get("text") or "")
    output_index = int(reasoning_state.get("output_index") or 0)
    item_id = str(reasoning_state.get("item_id") or "")
    reasoning_state["done"] = True
    return [
        _emit_event("response.reasoning_summary_text.done", {"type": "response.reasoning_summary_text.done", "sequence_number": _next_sequence(state), "item_id": item_id, "output_index": output_index, "summary_index": 0, "text": text}),
        _emit_event("response.reasoning_summary_part.done", {"type": "response.reasoning_summary_part.done", "sequence_number": _next_sequence(state), "item_id": item_id, "output_index": output_index, "summary_index": 0, "part": {"type": "summary_text", "text": text}}),
        _emit_event("response.output_item.done", {"type": "response.output_item.done", "sequence_number": _next_sequence(state), "output_index": output_index, "item": {"id": item_id, "type": "reasoning", "summary": [{"type": "summary_text", "text": text}]}}),
    ]


def _finalize_tool_calls(state: Dict[str, Any]) -> list[DownstreamChunk]:
    outputs: list[DownstreamChunk] = []
    for tool_index in sorted((state.get("tool_calls") or {}).keys()):
        tool_state = state["tool_calls"][tool_index]
        if tool_state.get("done"):
            continue
        arguments = str(tool_state.get("arguments") or "{}")
        outputs.append(_emit_event("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "sequence_number": _next_sequence(state), "item_id": tool_state["item_id"], "output_index": tool_state["output_index"], "arguments": arguments}))
        outputs.append(_emit_event("response.output_item.done", {"type": "response.output_item.done", "sequence_number": _next_sequence(state), "output_index": tool_state["output_index"], "item": {"id": tool_state["item_id"], "type": "function_call", "status": "completed", "arguments": arguments, "call_id": tool_state["call_id"], "name": tool_state["name"]}}))
        tool_state["done"] = True
    return outputs


def _build_completed_payload(
    model_name: str,
    original_request: Dict[str, Any],
    translated_request: Dict[str, Any],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    response_model = str(state.get("response_model") or translated_request.get("model") or model_name)
    response = {
        "id": state["response_id"],
        "object": "response",
        "created_at": int(state["created"]),
        "status": "completed",
        "background": False,
        "error": None,
        "model": response_model,
        "output": _build_output_items(state),
    }
    usage = state.get("usage")
    if isinstance(usage, dict) and usage:
        response["usage"] = usage
    response.update(_extract_echo_fields(original_request))
    return {"type": "response.completed", "sequence_number": _next_sequence(state), "response": response}


def _build_output_items(state: Dict[str, Any]) -> list[Dict[str, Any]]:
    items: list[tuple[int, Dict[str, Any]]] = []
    message_state = state.get("message") or {}
    if message_state.get("opened"):
        items.append((int(message_state.get("output_index") or 0), {"id": message_state.get("item_id"), "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "annotations": [], "logprobs": [], "text": str(message_state.get("content") or "")}]}))
    reasoning_state = state.get("reasoning") or {}
    if reasoning_state.get("opened"):
        items.append((int(reasoning_state.get("output_index") or 0), {"id": reasoning_state.get("item_id"), "type": "reasoning", "summary": [{"type": "summary_text", "text": str(reasoning_state.get("text") or "")}]}))
    for tool_state in (state.get("tool_calls") or {}).values():
        items.append((int(tool_state.get("output_index") or 0), {"id": tool_state.get("item_id"), "type": "function_call", "status": "completed", "arguments": str(tool_state.get("arguments") or "{}"), "call_id": tool_state.get("call_id"), "name": tool_state.get("name")}))
    return [item for _, item in sorted(items, key=lambda pair: pair[0])]


def _extract_echo_fields(original_request: Dict[str, Any]) -> Dict[str, Any]:
    echoed: Dict[str, Any] = {}
    for field in ("instructions", "max_output_tokens", "model", "parallel_tool_calls", "temperature", "top_p", "metadata", "user", "tools", "tool_choice"):
        if field in original_request:
            echoed[field] = original_request.get(field)
    return echoed


def convert_openai_chat_response_to_responses(
    model_name: str,
    original_request: Dict[str, Any],
    translated_request: Dict[str, Any],
    payload: Any,
) -> Any:
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

    response_id = payload.get("id") or f"resp_{model_name}_{int(time.time() * 1000)}"
    response_model = payload.get("model") or translated_request.get("model") or model_name
    output_items: list[Dict[str, Any]] = []
    reasoning_content = str(message.get("reasoning_content") or "")
    if reasoning_content:
        output_items.append({"id": f"rs_{response_id}_0", "type": "reasoning", "summary": [{"type": "summary_text", "text": reasoning_content}]})
    output_items.append({"id": f"msg_{response_id}_0", "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "annotations": [], "logprobs": [], "text": str(message.get("content") or "")}]})
    for index, tool_call in enumerate(message.get("tool_calls") or []):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        if not isinstance(function, dict):
            function = {}
        output_items.append({"id": f"fc_{tool_call.get('id') or index}", "type": "function_call", "status": "completed", "arguments": str(function.get("arguments") or "{}"), "call_id": str(tool_call.get("id") or ""), "name": str(function.get("name") or "")})

    response = {"id": response_id, "object": "response", "created_at": int(payload.get("created") or time.time()), "status": "completed", "background": False, "error": None, "model": response_model, "output": output_items}
    if isinstance(payload.get("usage"), dict):
        response["usage"] = {"input_tokens": int(payload["usage"].get("prompt_tokens") or 0), "output_tokens": int(payload["usage"].get("completion_tokens") or 0), "total_tokens": int(payload["usage"].get("total_tokens") or 0)}
    response.update(_extract_echo_fields(original_request))
    if finish_reason is not None:
        response["finish_reason"] = finish_reason
    return response


def _emit_event(event_name: str, payload: Dict[str, Any]) -> DownstreamChunk:
    return DownstreamChunk(kind="json", payload=payload, event=event_name)


def _next_sequence(state: Dict[str, Any]) -> int:
    sequence = int(state.get("seq") or 0) + 1
    state["seq"] = sequence
    return sequence
