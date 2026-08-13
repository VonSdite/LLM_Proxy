#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for bridging OpenAI chat payloads into OpenAI responses output."""

from __future__ import annotations

import copy
import json
import time
from typing import Any

from ..proxy_core.contracts import DownstreamChunk
from .annotation_utils import chat_annotations_to_responses, copy_logprobs
from .event_chunk_utils import build_json_event_chunk as _emit_event
from .reasoning_utils import (
    extract_openai_reasoning_delta,
    extract_openai_reasoning_text,
    openai_reasoning_effort_from_responses_reasoning,
)
from .responses_claude_bridge import _responses_tool_identities, _responses_tool_winners
from .tool_result_utils import normalize_tool_result_content


def convert_openai_responses_request_to_chat_request(
    model_name: str,
    body: dict[str, Any],
    stream: bool,
) -> dict[str, Any]:
    translated: dict[str, Any] = {
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
        output_call_ids = {
            str(item.get("call_id") or "").strip()
            for item in input_items
            if isinstance(item, dict)
            and str(item.get("type") or "").strip().lower() in {"function_call_output", "custom_tool_call_output"}
            and str(item.get("call_id") or "").strip()
        }
        pending_tool_calls: list[dict[str, Any]] = []
        pending_tool_call_ids: list[str] = []
        pending_reasoning: list[str] = []
        awaiting_tool_outputs: set[str] = set()
        deferred_messages: list[dict[str, Any]] = []

        def take_reasoning() -> str:
            text = "\n\n".join(part for part in pending_reasoning if part)
            pending_reasoning.clear()
            return text

        def append_regular(message: dict[str, Any]) -> None:
            if awaiting_tool_outputs & output_call_ids:
                deferred_messages.append(message)
            else:
                translated["messages"].append(message)

        def flush_tool_calls() -> None:
            if not pending_tool_calls:
                return
            reasoning = take_reasoning()
            message: dict[str, Any]
            if (
                translated["messages"]
                and translated["messages"][-1].get("role") == "assistant"
                and not translated["messages"][-1].get("tool_calls")
            ):
                message = translated["messages"][-1]
                message["tool_calls"] = list(pending_tool_calls)
                if reasoning:
                    existing = str(message.get("reasoning_content") or "")
                    message["reasoning_content"] = "\n\n".join(part for part in (existing, reasoning) if part)
            else:
                message = {"role": "assistant", "content": "", "tool_calls": list(pending_tool_calls)}
                if reasoning:
                    message["reasoning_content"] = reasoning
                translated["messages"].append(message)
            awaiting_tool_outputs.update(call_id for call_id in pending_tool_call_ids if call_id)
            pending_tool_calls.clear()
            pending_tool_call_ids.clear()

        def flush_reasoning() -> None:
            reasoning = take_reasoning()
            if reasoning:
                append_regular({"role": "assistant", "content": "", "reasoning_content": reasoning})

        def flush_deferred() -> None:
            if awaiting_tool_outputs & output_call_ids:
                return
            translated["messages"].extend(deferred_messages)
            deferred_messages.clear()

        for item in input_items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            role = str(item.get("role") or "").strip().lower()
            if item_type not in {"function_call", "custom_tool_call"}:
                flush_tool_calls()
            if item_type in {"", "message"}:
                if role not in {"system", "developer", "assistant"}:
                    role = "user"
                content = _from_openai_responses_message_content(item.get("content"))
                message = {"role": role, "content": content if content else ""}
                if role == "assistant":
                    reasoning = take_reasoning()
                    if reasoning:
                        message["reasoning_content"] = reasoning
                else:
                    flush_reasoning()
                append_regular(message)
            elif item_type == "reasoning":
                text = _responses_reasoning_text(item)
                if text:
                    pending_reasoning.append(text)
            elif item_type in {"function_call", "custom_tool_call"}:
                call_id = str(item.get("call_id") or item.get("id") or "")
                name = _qualified_responses_tool_name(item.get("namespace"), item.get("name"))
                if item_type == "custom_tool_call":
                    pending_tool_calls.append(
                        {
                            "id": call_id,
                            "type": "custom",
                            "custom": {"name": name, "input": str(item.get("input") or "")},
                        }
                    )
                else:
                    pending_tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": str(item.get("arguments") or "{}"),
                            },
                        }
                    )
                pending_tool_call_ids.append(call_id)
            elif item_type in {"function_call_output", "custom_tool_call_output"}:
                call_id = str(item.get("call_id") or "").strip()
                translated["messages"].append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": _responses_tool_output_to_chat(item.get("output")),
                    }
                )
                awaiting_tool_outputs.discard(call_id)
                flush_deferred()
        flush_tool_calls()
        flush_reasoning()
        flush_deferred()

    max_tokens = body.get("max_output_tokens")
    if max_tokens is None:
        max_tokens = body.get("max_completion_tokens")
    if max_tokens is not None:
        translated["max_tokens"] = max_tokens
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
    for field in ("prompt_cache_key", "safety_identifier"):
        if body.get(field) is not None:
            translated[field] = copy.deepcopy(body[field])
    if body.get("top_logprobs") is not None:
        translated["top_logprobs"] = body["top_logprobs"]
        translated["logprobs"] = True
    reasoning_effort = openai_reasoning_effort_from_responses_reasoning(body.get("reasoning"))
    if reasoning_effort is not None:
        translated["reasoning_effort"] = reasoning_effort

    text_format = body.get("text")
    if isinstance(text_format, dict):
        response_format = _responses_text_format_to_chat(text_format.get("format"))
        if response_format is not None:
            translated["response_format"] = response_format
        if text_format.get("verbosity") is not None:
            translated["verbosity"] = text_format["verbosity"]

    chat_tools = []
    for descriptor in _responses_tool_winners(body, filter_apply_patch=False):
        tool = descriptor["tool"]
        tool_type = descriptor["tool_type"]
        if tool_type not in {"function", "custom"}:
            continue
        if tool_type == "custom":
            custom_tool = {
                "type": "custom",
                "custom": {
                    "name": descriptor["name"],
                    "description": _responses_tool_description(tool),
                },
            }
            if tool.get("format") is not None:
                custom_tool["custom"]["format"] = copy.deepcopy(tool["format"])
            chat_tools.append(custom_tool)
            continue
        parameters = _responses_tool_parameters(tool)
        chat_tools.append(
            {
                "type": "function",
                "function": {
                    "name": descriptor["name"],
                    "description": _responses_tool_description(tool),
                    "parameters": parameters,
                },
            }
        )
    if chat_tools:
        translated["tools"] = chat_tools

    tool_choice = body.get("tool_choice")
    if isinstance(tool_choice, dict) and str(tool_choice.get("type") or "").strip().lower() in {
        "function",
        "custom",
    }:
        choice_type = str(tool_choice.get("type") or "").strip().lower()
        nested = tool_choice.get(choice_type)
        name = nested.get("name") if isinstance(nested, dict) else tool_choice.get("name")
        qualified_name = _qualified_responses_tool_name(tool_choice.get("namespace"), name)
        translated["tool_choice"] = (
            {"type": "custom", "custom": {"name": qualified_name}}
            if choice_type == "custom"
            else {"type": "function", "function": {"name": qualified_name}}
        )
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
            image_url: dict[str, Any] = {"url": item.get("image_url")}
            if item.get("detail") is not None:
                image_url["detail"] = item["detail"]
            translated.append({"type": "image_url", "image_url": image_url})
        elif item_type == "input_file":
            file_payload = {key: item[key] for key in ("file_data", "file_id", "filename") if item.get(key) is not None}
            if file_payload:
                translated.append({"type": "file", "file": file_payload})
    return translated


def _responses_reasoning_text(item: dict[str, Any]) -> str:
    texts: list[str] = []
    for field in ("summary", "content"):
        for part in item.get(field) or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "".join(texts)


def _qualified_responses_tool_name(namespace: Any, name: Any) -> str:
    namespace_text = str(namespace or "").strip()
    name_text = str(name or "").strip()
    if not namespace_text or name_text.startswith("mcp__") or name_text.startswith(namespace_text):
        return name_text
    separator = "" if namespace_text.endswith("__") else "__"
    return f"{namespace_text}{separator}{name_text}"


def _responses_tool_output_to_chat(output: Any) -> Any:
    structured = output
    if isinstance(output, str):
        try:
            structured = json.loads(output)
        except (TypeError, ValueError):
            return output
    if not isinstance(structured, list):
        return normalize_tool_result_content(output)
    parts = _from_openai_responses_message_content(structured)
    if any(isinstance(part, dict) and part.get("type") in {"image_url", "file"} for part in parts):
        return parts
    return "".join(
        str(part.get("text") or "") for part in structured if isinstance(part, dict)
    ) or normalize_tool_result_content(output)


def _responses_tool_parameters(tool: dict[str, Any]) -> Any:
    for key in ("parameters", "parametersJsonSchema", "input_schema"):
        if tool.get(key) is not None:
            return tool[key]
    function = tool.get("function")
    if isinstance(function, dict):
        for key in ("parameters", "parametersJsonSchema"):
            if function.get(key) is not None:
                return function[key]
    return {"type": "object", "properties": {}}


def _responses_tool_description(tool: dict[str, Any]) -> str:
    if tool.get("description") is not None:
        return str(tool["description"])
    function = tool.get("function")
    return str(function.get("description") or "") if isinstance(function, dict) else ""


def _responses_text_format_to_chat(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    format_type = str(value.get("type") or "").strip().lower()
    if format_type in {"text", "json_object"}:
        return {"type": format_type}
    if format_type != "json_schema":
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            key: value[key] for key in ("name", "description", "strict", "schema") if value.get(key) is not None
        },
    }


def translate_openai_chat_downstream_chunk_to_responses(
    model_name: str,
    original_request: dict[str, Any],
    translated_request: dict[str, Any],
    chunk: DownstreamChunk,
    state: dict[str, Any],
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
    original_request: dict[str, Any],
    translated_request: dict[str, Any],
    payload: dict[str, Any],
    state: dict[str, Any],
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
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        state["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": int(usage.get("total_tokens") or (input_tokens + output_tokens)),
        }
        prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
        if isinstance(prompt_details, dict):
            cached_present, cached_tokens = _first_present_usage_int(prompt_details, "cached_tokens")
            cache_creation_present, cache_creation_tokens = _first_present_usage_int(
                prompt_details,
                "cache_write_tokens",
                "cache_creation_tokens",
                "cache_creation_input_tokens",
                "cached_creation_tokens",
            )
            if cached_present or cache_creation_present:
                input_details: dict[str, Any] = {}
                if cached_present:
                    input_details["cached_tokens"] = cached_tokens
                if cache_creation_present:
                    input_details["cache_write_tokens"] = cache_creation_tokens
                state["usage"]["input_tokens_details"] = input_details
        completion_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details")
        if isinstance(completion_details, dict) and int(completion_details.get("reasoning_tokens") or 0) > 0:
            state["usage"]["output_tokens_details"] = {
                "reasoning_tokens": int(completion_details.get("reasoning_tokens") or 0),
            }

    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            delta = {}

        reasoning_content = extract_openai_reasoning_delta(
            delta,
            state,
            f"reasoning_details:{int(choice.get('index') or 0)}",
        )
        if reasoning_content:
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
            choice_logprobs = choice.get("logprobs")
            logprobs = copy_logprobs(choice_logprobs.get("content")) if isinstance(choice_logprobs, dict) else []
            if logprobs:
                message_state.setdefault("logprobs", []).extend(logprobs)
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
                        "logprobs": logprobs,
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
                custom = tool_call.get("custom") or {}
                if not isinstance(custom, dict):
                    custom = {}
                tool_name = str(custom.get("name") or function.get("name") or "")
                tool_state, open_events = _ensure_tool_open(
                    state,
                    tool_index,
                    str(tool_call.get("id") or ""),
                    tool_name,
                    original_request,
                )
                outputs.extend(open_events)
                if tool_name:
                    _update_responses_tool_identity(tool_state, tool_name, original_request)
                arguments_delta = custom.get("input") if custom else function.get("arguments")
                if isinstance(arguments_delta, str) and arguments_delta:
                    tool_state["arguments"] += arguments_delta
                    if not tool_state.get("custom"):
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

        if choice.get("finish_reason") not in (None, ""):
            state["finish_reason"] = choice["finish_reason"]

    return outputs


def _ensure_stream_started(
    model_name: str,
    translated_request: dict[str, Any],
    state: dict[str, Any],
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
            "message": state.get("message")
            or {"opened": False, "done": False, "content": "", "output_index": None, "item_id": None},
            "reasoning": state.get("reasoning")
            or {"opened": False, "done": False, "text": "", "output_index": None, "item_id": None},
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


def _ensure_message_open(state: dict[str, Any]) -> list[DownstreamChunk]:
    message_state = state.setdefault(
        "message", {"opened": False, "done": False, "content": "", "output_index": None, "item_id": None}
    )
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


def _ensure_reasoning_open(state: dict[str, Any]) -> list[DownstreamChunk]:
    reasoning_state = state.setdefault(
        "reasoning", {"opened": False, "done": False, "text": "", "output_index": None, "item_id": None}
    )
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
    state: dict[str, Any],
    tool_index: int,
    tool_call_id: str,
    tool_name: str,
    original_request: dict[str, Any],
) -> tuple[dict[str, Any], list[DownstreamChunk]]:
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
            "arguments": "",
        }
        _update_responses_tool_identity(tool_state, tool_name, original_request)
        tool_calls[tool_index] = tool_state

    if tool_call_id:
        tool_state["call_id"] = tool_call_id
        prefix = "ctc" if tool_state.get("custom") else "fc"
        tool_state["item_id"] = f"{prefix}_{tool_call_id}"
    if tool_name:
        _update_responses_tool_identity(tool_state, tool_name, original_request)

    if tool_state.get("opened"):
        return tool_state, []

    tool_state["opened"] = True
    item: dict[str, Any] = {
        "id": tool_state["item_id"],
        "type": "custom_tool_call" if tool_state.get("custom") else "function_call",
        "status": "in_progress",
        "call_id": tool_state["call_id"],
        "name": tool_state["name"],
    }
    if tool_state.get("custom"):
        item["input"] = ""
    else:
        item["arguments"] = ""
    if tool_state.get("namespace"):
        item["namespace"] = tool_state["namespace"]
    return tool_state, [
        _emit_event(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "sequence_number": _next_sequence(state),
                "output_index": tool_state["output_index"],
                "item": item,
            },
        )
    ]


def _update_responses_tool_identity(
    tool_state: dict[str, Any], qualified_name: str, original_request: dict[str, Any]
) -> None:
    name, namespace, tool_type = _responses_tool_identities(original_request, filter_apply_patch=False).get(
        qualified_name,
        (qualified_name, "", "function"),
    )
    tool_state["qualified_name"] = qualified_name
    tool_state["name"] = name
    tool_state["namespace"] = namespace
    tool_state["custom"] = tool_type == "custom"
    if tool_state.get("call_id"):
        prefix = "ctc" if tool_state["custom"] else "fc"
        tool_state["item_id"] = f"{prefix}_{tool_state['call_id']}"


def finalize_openai_responses_stream(
    model_name: str,
    original_request: dict[str, Any],
    translated_request: dict[str, Any],
    state: dict[str, Any],
) -> list[DownstreamChunk]:
    if not state.get("started") or state.get("completed"):
        return []

    outputs: list[DownstreamChunk] = []
    outputs.extend(_finalize_reasoning(state))
    outputs.extend(_finalize_message(state))
    outputs.extend(_finalize_tool_calls(state))
    completed_payload = _build_completed_payload(model_name, original_request, translated_request, state)
    outputs.append(_emit_event(completed_payload["type"], completed_payload))
    state["completed"] = True
    return outputs


def _finalize_message(state: dict[str, Any]) -> list[DownstreamChunk]:
    message_state = state.get("message") or {}
    if not message_state.get("opened") or message_state.get("done"):
        return []

    text = str(message_state.get("content") or "")
    logprobs = copy_logprobs(message_state.get("logprobs"))
    output_index = int(message_state.get("output_index") or 0)
    item_id = str(message_state.get("item_id") or "")
    message_state["done"] = True
    return [
        _emit_event(
            "response.output_text.done",
            {
                "type": "response.output_text.done",
                "sequence_number": _next_sequence(state),
                "item_id": item_id,
                "output_index": output_index,
                "content_index": 0,
                "text": text,
                "logprobs": logprobs,
            },
        ),
        _emit_event(
            "response.content_part.done",
            {
                "type": "response.content_part.done",
                "sequence_number": _next_sequence(state),
                "item_id": item_id,
                "output_index": output_index,
                "content_index": 0,
                "part": {"type": "output_text", "annotations": [], "logprobs": logprobs, "text": text},
            },
        ),
        _emit_event(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "sequence_number": _next_sequence(state),
                "output_index": output_index,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "annotations": [], "logprobs": logprobs, "text": text}],
                },
            },
        ),
    ]


def _finalize_reasoning(state: dict[str, Any]) -> list[DownstreamChunk]:
    reasoning_state = state.get("reasoning") or {}
    if not reasoning_state.get("opened") or reasoning_state.get("done"):
        return []

    text = str(reasoning_state.get("text") or "")
    output_index = int(reasoning_state.get("output_index") or 0)
    item_id = str(reasoning_state.get("item_id") or "")
    reasoning_state["done"] = True
    return [
        _emit_event(
            "response.reasoning_summary_text.done",
            {
                "type": "response.reasoning_summary_text.done",
                "sequence_number": _next_sequence(state),
                "item_id": item_id,
                "output_index": output_index,
                "summary_index": 0,
                "text": text,
            },
        ),
        _emit_event(
            "response.reasoning_summary_part.done",
            {
                "type": "response.reasoning_summary_part.done",
                "sequence_number": _next_sequence(state),
                "item_id": item_id,
                "output_index": output_index,
                "summary_index": 0,
                "part": {"type": "summary_text", "text": text},
            },
        ),
        _emit_event(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "sequence_number": _next_sequence(state),
                "output_index": output_index,
                "item": {"id": item_id, "type": "reasoning", "summary": [{"type": "summary_text", "text": text}]},
            },
        ),
    ]


def _finalize_tool_calls(state: dict[str, Any]) -> list[DownstreamChunk]:
    outputs: list[DownstreamChunk] = []
    for tool_index in sorted((state.get("tool_calls") or {}).keys()):
        tool_state = state["tool_calls"][tool_index]
        if tool_state.get("done"):
            continue
        arguments = str(tool_state.get("arguments") or ("" if tool_state.get("custom") else "{}"))
        if tool_state.get("custom"):
            custom_input = _unwrap_custom_tool_arguments(arguments)
            outputs.append(
                _emit_event(
                    "response.custom_tool_call_input.done",
                    {
                        "type": "response.custom_tool_call_input.done",
                        "sequence_number": _next_sequence(state),
                        "item_id": tool_state["item_id"],
                        "output_index": tool_state["output_index"],
                        "input": custom_input,
                    },
                )
            )
            item = {
                "id": tool_state["item_id"],
                "type": "custom_tool_call",
                "status": "completed",
                "input": custom_input,
                "call_id": tool_state["call_id"],
                "name": tool_state["name"],
            }
            if tool_state.get("namespace"):
                item["namespace"] = tool_state["namespace"]
            outputs.append(
                _emit_event(
                    "response.output_item.done",
                    {
                        "type": "response.output_item.done",
                        "sequence_number": _next_sequence(state),
                        "output_index": tool_state["output_index"],
                        "item": item,
                    },
                )
            )
            tool_state["done"] = True
            continue
        outputs.append(
            _emit_event(
                "response.function_call_arguments.done",
                {
                    "type": "response.function_call_arguments.done",
                    "sequence_number": _next_sequence(state),
                    "item_id": tool_state["item_id"],
                    "output_index": tool_state["output_index"],
                    "arguments": arguments,
                },
            )
        )
        outputs.append(
            _emit_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "sequence_number": _next_sequence(state),
                    "output_index": tool_state["output_index"],
                    "item": {
                        "id": tool_state["item_id"],
                        "type": "function_call",
                        "status": "completed",
                        "arguments": arguments,
                        "call_id": tool_state["call_id"],
                        "name": tool_state["name"],
                    },
                },
            )
        )
        tool_state["done"] = True
    return outputs


def _build_completed_payload(
    model_name: str,
    original_request: dict[str, Any],
    translated_request: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    response_model = str(state.get("response_model") or translated_request.get("model") or model_name)
    finish_reason = str(state.get("finish_reason") or "").strip().lower()
    incomplete_reason = _responses_incomplete_reason(finish_reason)
    response = {
        "id": state["response_id"],
        "object": "response",
        "created_at": int(state["created"]),
        "status": "incomplete" if incomplete_reason else "completed",
        "background": False,
        "error": None,
        "model": response_model,
        "output": _build_output_items(state),
    }
    if incomplete_reason:
        response["incomplete_details"] = {"reason": incomplete_reason}
    usage = state.get("usage")
    if isinstance(usage, dict) and usage:
        response["usage"] = usage
    response.update(_extract_echo_fields(original_request))
    event_type = "response.incomplete" if incomplete_reason else "response.completed"
    return {"type": event_type, "sequence_number": _next_sequence(state), "response": response}


def _build_output_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[tuple[int, dict[str, Any]]] = []
    message_state = state.get("message") or {}
    if message_state.get("opened"):
        items.append(
            (
                int(message_state.get("output_index") or 0),
                {
                    "id": message_state.get("item_id"),
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "annotations": [],
                            "logprobs": copy_logprobs(message_state.get("logprobs")),
                            "text": str(message_state.get("content") or ""),
                        }
                    ],
                },
            )
        )
    reasoning_state = state.get("reasoning") or {}
    if reasoning_state.get("opened"):
        items.append(
            (
                int(reasoning_state.get("output_index") or 0),
                {
                    "id": reasoning_state.get("item_id"),
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": str(reasoning_state.get("text") or "")}],
                },
            )
        )
    for tool_state in (state.get("tool_calls") or {}).values():
        if tool_state.get("custom"):
            item = {
                "id": tool_state.get("item_id"),
                "type": "custom_tool_call",
                "status": "completed",
                "input": _unwrap_custom_tool_arguments(str(tool_state.get("arguments") or "{}")),
                "call_id": tool_state.get("call_id"),
                "name": tool_state.get("name"),
            }
            if tool_state.get("namespace"):
                item["namespace"] = tool_state["namespace"]
            items.append((int(tool_state.get("output_index") or 0), item))
            continue
        items.append(
            (
                int(tool_state.get("output_index") or 0),
                {
                    "id": tool_state.get("item_id"),
                    "type": "function_call",
                    "status": "completed",
                    "arguments": str(tool_state.get("arguments") or "{}"),
                    "call_id": tool_state.get("call_id"),
                    "name": tool_state.get("name"),
                },
            )
        )
    return [item for _, item in sorted(items, key=lambda pair: pair[0])]


def _extract_echo_fields(original_request: dict[str, Any]) -> dict[str, Any]:
    echoed: dict[str, Any] = {}
    for field in (
        "instructions",
        "max_output_tokens",
        "model",
        "store",
        "include",
        "parallel_tool_calls",
        "temperature",
        "top_p",
        "metadata",
        "user",
        "tools",
        "tool_choice",
    ):
        if field in original_request:
            echoed[field] = original_request.get(field)
    return echoed


def convert_openai_chat_response_to_responses(
    model_name: str,
    original_request: dict[str, Any],
    translated_request: dict[str, Any],
    payload: Any,
) -> Any:
    if not isinstance(payload, dict):
        return payload

    message: dict[str, Any] = {}
    choice_logprobs: list[dict[str, Any]] = []
    finish_reason = None
    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        if isinstance(choice.get("message"), dict):
            message = choice.get("message") or {}
            finish_reason = choice.get("finish_reason")
            logprobs = choice.get("logprobs")
            if isinstance(logprobs, dict):
                choice_logprobs = copy_logprobs(logprobs.get("content"))
            break

    response_id = payload.get("id") or f"resp_{model_name}_{int(time.time() * 1000)}"
    response_model = payload.get("model") or translated_request.get("model") or model_name
    output_items: list[dict[str, Any]] = []
    reasoning_content = extract_openai_reasoning_text(message)
    if reasoning_content:
        output_items.append(
            {
                "id": f"rs_{response_id}_0",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": reasoning_content}],
            }
        )
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        message_content = [{"type": "refusal", "refusal": refusal}]
    else:
        annotations = chat_annotations_to_responses(message.get("annotations"))
        message_content = [
            {
                "type": "output_text",
                "annotations": annotations,
                "logprobs": choice_logprobs,
                "text": str(message.get("content") or ""),
            }
        ]
    if message.get("content") not in (None, "") or refusal:
        output_items.append(
            {
                "id": f"msg_{response_id}_0",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": message_content,
            }
        )
    for index, tool_call in enumerate(message.get("tool_calls") or []):
        if not isinstance(tool_call, dict):
            continue
        tool_type = str(tool_call.get("type") or "").strip().lower()
        if tool_type == "custom":
            custom = tool_call.get("custom")
            if not isinstance(custom, dict) or not custom.get("name"):
                continue
            qualified_name = str(custom.get("name") or "")
            call_id = str(tool_call.get("id") or "")
            name, namespace, _ = _responses_tool_identities(original_request, filter_apply_patch=False).get(
                qualified_name,
                (qualified_name, "", "custom"),
            )
            item = {
                "id": f"ctc_{call_id or index}",
                "type": "custom_tool_call",
                "status": "completed",
                "input": str(custom.get("input") or ""),
                "call_id": call_id,
                "name": name,
            }
            if namespace:
                item["namespace"] = namespace
            output_items.append(item)
            continue
        function = tool_call.get("function") or {}
        if not isinstance(function, dict):
            function = {}
        call_id = str(tool_call.get("id") or "")
        qualified_name = str(function.get("name") or "")
        name, namespace, tool_type = _responses_tool_identities(original_request, filter_apply_patch=False).get(
            qualified_name,
            (qualified_name, "", "function"),
        )
        if tool_type == "custom":
            item = {
                "id": f"ctc_{call_id or index}",
                "type": "custom_tool_call",
                "status": "completed",
                "input": _unwrap_custom_tool_arguments(str(function.get("arguments") or "{}")),
                "call_id": call_id,
                "name": name,
            }
        else:
            item = {
                "id": f"fc_{call_id or index}",
                "type": "function_call",
                "status": "completed",
                "arguments": str(function.get("arguments") or "{}"),
                "call_id": call_id,
                "name": name,
            }
        if namespace:
            item["namespace"] = namespace
        output_items.append(item)
    legacy_function_call = message.get("function_call")
    if isinstance(legacy_function_call, dict) and legacy_function_call.get("name"):
        legacy_call_id = f"call_{response_id}_legacy"
        output_items.append(
            {
                "id": f"fc_{response_id}_legacy",
                "type": "function_call",
                "status": "completed",
                "arguments": str(legacy_function_call.get("arguments") or "{}"),
                "call_id": legacy_call_id,
                "name": str(legacy_function_call.get("name")),
            }
        )

    incomplete_reason = _responses_incomplete_reason(finish_reason)
    response = {
        "id": response_id,
        "object": "response",
        "created_at": int(payload.get("created") or time.time()),
        "status": "incomplete" if incomplete_reason else "completed",
        "background": False,
        "error": None,
        "model": response_model,
        "output": output_items,
    }
    if incomplete_reason:
        response["incomplete_details"] = {"reason": incomplete_reason}
    if isinstance(payload.get("usage"), dict):
        usage = payload["usage"]
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        response["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": int(usage.get("total_tokens") or (input_tokens + output_tokens)),
        }
        prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
        if isinstance(prompt_details, dict):
            cached_present, cached_tokens = _first_present_usage_int(prompt_details, "cached_tokens")
            cache_creation_present, cache_creation_tokens = _first_present_usage_int(
                prompt_details,
                "cache_write_tokens",
                "cache_creation_tokens",
                "cache_creation_input_tokens",
                "cached_creation_tokens",
            )
            if cached_present or cache_creation_present:
                input_details: dict[str, Any] = {}
                if cached_present:
                    input_details["cached_tokens"] = cached_tokens
                if cache_creation_present:
                    input_details["cache_write_tokens"] = cache_creation_tokens
                response["usage"]["input_tokens_details"] = input_details
        completion_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details")
        if isinstance(completion_details, dict) and int(completion_details.get("reasoning_tokens") or 0) > 0:
            response["usage"]["output_tokens_details"] = {
                "reasoning_tokens": int(completion_details.get("reasoning_tokens") or 0),
            }
    response.update(_extract_echo_fields(original_request))
    if finish_reason is not None:
        response["finish_reason"] = finish_reason
    return response


def _next_sequence(state: dict[str, Any]) -> int:
    sequence = int(state.get("seq") or 0) + 1
    state["seq"] = sequence
    return sequence


def _first_present_usage_int(payload: dict[str, Any], *keys: str) -> tuple[bool, int]:
    for key in keys:
        if key not in payload:
            continue
        try:
            return True, max(int(payload[key] or 0), 0)
        except (TypeError, ValueError):
            return True, 0
    return False, 0


def _unwrap_custom_tool_arguments(arguments: str) -> str:
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError):
        return arguments
    if isinstance(parsed, dict) and "input" in parsed:
        value = parsed["input"]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return arguments


def _responses_incomplete_reason(finish_reason: Any) -> str | None:
    normalized = str(finish_reason or "").strip().lower()
    if normalized == "length":
        return "max_output_tokens"
    if normalized == "content_filter":
        return "content_filter"
    return None
