#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI Responses 与 Claude Messages 的直接协议桥接。"""

from __future__ import annotations

import base64
import copy
import json
import re
import secrets
import time
from typing import Any

from ..proxy_core.contracts import DownstreamChunk, StreamEvent
from ..proxy_core.usage import extract_canonical_usage, openai_usage_to_claude, safe_int
from .annotation_utils import claude_citations_to_responses, responses_annotations_to_claude
from .event_chunk_utils import build_json_event_chunk
from .reasoning_utils import (
    openai_reasoning_effort_from_claude_thinking,
    openai_reasoning_effort_to_claude_thinking,
)
from .tool_result_utils import normalize_tool_result_content

CLAUDE_REDACTED_THINKING_PREFIX = "claude-redacted-thinking:"
_CLAUDE_TOOL_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_-]")
_UNSUPPORTED_RESPONSES_TOOL_TYPES = {
    "image_generation",
    "file_search",
    "code_interpreter",
    "computer_use_preview",
}


def convert_claude_request_to_openai_responses(
    model_name: str,
    body: dict[str, Any],
    stream: bool,
) -> dict[str, Any]:
    """把下游 Claude 请求直接映射为上游 Responses 请求。"""
    translated: dict[str, Any] = {
        "model": model_name,
        "input": [],
        "stream": bool(stream),
    }

    system = body.get("system")
    if isinstance(system, str) and system:
        translated["instructions"] = system
    elif isinstance(system, list):
        for block in system:
            if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                continue
            part = {"type": "input_text", "text": block["text"]}
            translated["input"].append({"type": "message", "role": "developer", "content": [part]})

    custom_tool_names = _claude_custom_tool_names(body.get("tools"))
    custom_tool_call_ids: set[str] = set()
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().lower()
        role = "assistant" if role == "assistant" else "user"
        content = message.get("content")
        if isinstance(content, str):
            translated["input"].append(
                {
                    "type": "message",
                    "role": role,
                    "content": [
                        {
                            "type": "output_text" if role == "assistant" else "input_text",
                            "text": content,
                        }
                    ],
                }
            )
            continue
        if not isinstance(content, list):
            continue

        message_parts: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip().lower()
            if block_type in {"text", "image", "document"}:
                part = _claude_content_to_responses(block, role)
                if part is not None:
                    message_parts.append(part)
            elif block_type in {"thinking", "redacted_thinking"} and role == "assistant":
                if message_parts:
                    translated["input"].append({"type": "message", "role": role, "content": message_parts})
                    message_parts = []
                translated["input"].append(_claude_reasoning_to_responses(block))
            elif block_type == "tool_use" and role == "assistant":
                if message_parts:
                    translated["input"].append({"type": "message", "role": role, "content": message_parts})
                    message_parts = []
                tool_call = _claude_tool_use_to_responses(block, custom_tool_names)
                translated["input"].append(tool_call)
                if tool_call.get("type") == "custom_tool_call":
                    custom_tool_call_ids.add(str(tool_call.get("call_id") or ""))
            elif block_type == "tool_result":
                if message_parts:
                    translated["input"].append({"type": "message", "role": role, "content": message_parts})
                    message_parts = []
                translated["input"].append(
                    {
                        "type": "custom_tool_call_output"
                        if str(block.get("tool_use_id") or "") in custom_tool_call_ids
                        else "function_call_output",
                        "call_id": str(block.get("tool_use_id") or ""),
                        "output": normalize_tool_result_content(block.get("content")),
                    }
                )
        if message_parts:
            translated["input"].append({"type": "message", "role": role, "content": message_parts})

    max_tokens = body.get("max_tokens")
    if max_tokens is None:
        max_tokens = body.get("max_completion_tokens")
    if max_tokens is not None:
        translated["max_output_tokens"] = max_tokens
    for field in (
        "temperature",
        "top_p",
        "metadata",
        "store",
        "include",
        "parallel_tool_calls",
        "previous_response_id",
        "prompt_cache_key",
        "service_tier",
    ):
        if body.get(field) is not None:
            translated[field] = copy.deepcopy(body[field])

    reasoning_effort = openai_reasoning_effort_from_claude_thinking(body.get("thinking"), body.get("output_config"))
    if reasoning_effort is not None:
        translated["reasoning"] = {"effort": reasoning_effort}

    tools = _claude_tools_to_responses(body.get("tools"))
    if tools:
        translated["tools"] = tools
    tool_choice = _claude_tool_choice_to_responses(body.get("tool_choice"))
    if tool_choice is not None:
        translated["tool_choice"] = tool_choice
    if isinstance(body.get("tool_choice"), dict) and body["tool_choice"].get("disable_parallel_tool_use") is not None:
        translated["parallel_tool_calls"] = not bool(body["tool_choice"]["disable_parallel_tool_use"])
    return translated


def convert_openai_responses_request_to_claude(
    model_name: str,
    body: dict[str, Any],
    stream: bool,
) -> dict[str, Any]:
    """把下游 Responses 请求直接映射为上游 Claude 请求。"""
    max_tokens = body.get("max_output_tokens")
    if max_tokens is None:
        max_tokens = body.get("max_completion_tokens")
    translated: dict[str, Any] = {
        "model": model_name,
        "max_tokens": int(max_tokens or 4096),
        "messages": [],
        "stream": bool(stream),
    }
    for field in ("temperature", "top_p"):
        if body.get(field) is not None:
            translated[field] = body[field]

    thinking = openai_reasoning_effort_to_claude_thinking(
        (body.get("reasoning") or {}).get("effort") if isinstance(body.get("reasoning"), dict) else None,
        max_tokens=translated["max_tokens"],
    )
    if thinking is not None:
        translated["thinking"] = thinking
        budget_tokens = safe_int(thinking.get("budget_tokens"))
        if budget_tokens and translated["max_tokens"] <= budget_tokens:
            translated["max_tokens"] = budget_tokens + 1

    system_blocks: list[dict[str, Any]] = []
    if isinstance(body.get("instructions"), str) and body["instructions"]:
        system_blocks.append({"type": "text", "text": body["instructions"]})

    messages: list[dict[str, Any]] = translated["messages"]
    pending_role = ""
    pending_blocks: list[dict[str, Any]] = []
    pending_tool_blocks: list[dict[str, Any]] = []

    def flush_pending_message() -> None:
        nonlocal pending_role, pending_blocks, pending_tool_blocks
        blocks = list(pending_blocks)
        if pending_role == "assistant":
            blocks.extend(pending_tool_blocks)
        if pending_role and blocks:
            messages.append({"role": pending_role, "content": blocks})
        pending_role = ""
        pending_blocks = []
        pending_tool_blocks = []

    def append_blocks(role: str, blocks: list[dict[str, Any]]) -> None:
        nonlocal pending_role
        if not blocks:
            return
        if pending_role and pending_role != role:
            flush_pending_message()
        pending_role = role
        pending_blocks.extend(blocks)

    def append_tool_block(block: dict[str, Any]) -> None:
        nonlocal pending_role
        if pending_role and pending_role != "assistant":
            flush_pending_message()
        pending_role = "assistant"
        pending_tool_blocks.append(block)

    tool_call_ids: dict[str, str] = {}
    input_items = body.get("input")
    if isinstance(input_items, str):
        append_blocks("user", [{"type": "text", "text": input_items}])
    elif isinstance(input_items, list):
        for item in input_items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            role = str(item.get("role") or "").strip().lower()
            if item_type in {"", "message"}:
                if role in {"system", "developer"}:
                    blocks = _responses_system_content_to_claude(item.get("content"))
                    if blocks:
                        _copy_cache_control(item, blocks[-1])
                    system_blocks.extend(blocks)
                    continue
                blocks = _responses_content_to_claude(item.get("content"))
                if blocks:
                    _copy_cache_control(item, blocks[-1])
                    append_blocks("assistant" if role == "assistant" else "user", blocks)
            elif item_type == "reasoning":
                block = _responses_reasoning_to_claude(item)
                if block is not None:
                    append_blocks("assistant", [block])
            elif item_type in {"function_call", "custom_tool_call"}:
                tool_name = _qualify_tool_name(item.get("namespace"), item.get("name"))
                raw_call_id = str(item.get("call_id") or item.get("id") or "")
                call_id = _sanitize_claude_tool_id(raw_call_id)
                tool_call_ids[raw_call_id] = call_id
                tool_input = (
                    {"input": str(item.get("input") or "")}
                    if item_type == "custom_tool_call"
                    else _parse_json_object(item.get("arguments"))
                )
                append_tool_block(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": tool_name,
                        "input": tool_input,
                    }
                )
            elif item_type in {"function_call_output", "custom_tool_call_output"}:
                raw_call_id = str(item.get("call_id") or "")
                append_blocks(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_ids.get(raw_call_id) or _sanitize_claude_tool_id(raw_call_id),
                            "content": _responses_tool_output_to_claude(item.get("output")),
                        }
                    ],
                )
    flush_pending_message()

    if system_blocks:
        translated["system"] = system_blocks
    if not translated["messages"] and system_blocks:
        translated["messages"] = [{"role": "user", "content": [{"type": "text", "text": ""}]}]

    if not _responses_tool_choice_disables_tools(body.get("tool_choice")):
        tools = _responses_tools_to_claude(body)
        if tools:
            translated["tools"] = tools
        tool_choice = _responses_tool_choice_to_claude(body.get("tool_choice"), tools, body)
        if body.get("parallel_tool_calls") is not None and tools:
            tool_choice = tool_choice or {"type": "auto"}
            tool_choice["disable_parallel_tool_use"] = not bool(body["parallel_tool_calls"])
        if tool_choice is not None:
            translated["tool_choice"] = tool_choice
    return translated


def convert_openai_responses_response_to_claude(
    model_name: str,
    original_request: dict[str, Any],
    translated_request: dict[str, Any],
    payload: Any,
) -> Any:
    """把 Responses 非流式响应直接映射为 Claude 响应。"""
    del original_request
    if not isinstance(payload, dict):
        return payload
    if str(payload.get("type") or "").strip().lower() in {
        "response.completed",
        "response.done",
    } and isinstance(payload.get("response"), dict):
        payload = payload["response"]

    content: list[dict[str, Any]] = []
    saw_tool = False
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "message":
            for part in item.get("content") or []:
                block = _responses_output_part_to_claude(part)
                if block is not None:
                    content.append(block)
        elif item_type == "reasoning":
            block = _responses_reasoning_to_claude(item)
            if block is not None:
                content.append(block)
        elif item_type in {"function_call", "custom_tool_call"}:
            saw_tool = True
            tool_input = (
                {"input": str(item.get("input") or "")}
                if item_type == "custom_tool_call"
                else _parse_json_object(item.get("arguments"))
            )
            content.append(
                {
                    "type": "tool_use",
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "name": _qualify_tool_name(item.get("namespace"), item.get("name")),
                    "input": tool_input,
                }
            )

    response = {
        "id": payload.get("id") or f"msg_{model_name}_{int(time.time() * 1000)}",
        "type": "message",
        "role": "assistant",
        "model": payload.get("model") or translated_request.get("model") or model_name,
        "content": content or [{"type": "text", "text": ""}],
        "stop_reason": "tool_use" if saw_tool else _responses_stop_reason(payload),
        "stop_sequence": None,
    }
    if isinstance(payload.get("usage"), dict):
        response["usage"] = openai_usage_to_claude(payload["usage"])
    return response


def convert_claude_response_to_openai_responses(
    model_name: str,
    original_request: dict[str, Any],
    translated_request: dict[str, Any],
    payload: Any,
) -> Any:
    """把 Claude 非流式响应直接映射为 Responses 响应。"""
    if not isinstance(payload, dict):
        return payload
    response_id = str(payload.get("id") or f"resp_{model_name}")
    response_model = str(payload.get("model") or translated_request.get("model") or model_name)
    tool_identities = _responses_tool_identities(original_request)
    output: list[dict[str, Any]] = []
    for index, block in enumerate(payload.get("content") or []):
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip().lower()
        if block_type == "text":
            text = str(block.get("text") or "")
            text_part: dict[str, Any] = {
                "type": "output_text",
                "annotations": claude_citations_to_responses(block.get("citations"), text),
                "logprobs": [],
                "text": text,
            }
            output.append(
                {
                    "id": f"msg_{response_id}_{index}",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [text_part],
                }
            )
        elif block_type in {"thinking", "redacted_thinking"}:
            output.append(
                {
                    "id": f"rs_{response_id}_{index}",
                    "type": "reasoning",
                    "encrypted_content": _claude_reasoning_carrier(block),
                    "summary": [
                        {
                            "type": "summary_text",
                            "text": str(block.get("thinking") or ""),
                        }
                    ],
                }
            )
        elif block_type == "tool_use":
            output.append(
                _claude_tool_use_response_to_responses(
                    block,
                    tool_identities,
                    item_id_prefix=response_id,
                )
            )

    response: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "incomplete" if payload.get("stop_reason") == "max_tokens" else "completed",
        "model": response_model,
        "output": output,
    }
    if payload.get("stop_reason") == "max_tokens":
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    _copy_response_request_fields(original_request, response)
    if isinstance(payload.get("usage"), dict):
        response["usage"] = _claude_usage_to_responses(payload["usage"])
    return response


def translate_openai_responses_stream_to_claude(
    model_name: str,
    original_request: dict[str, Any],
    translated_request: dict[str, Any],
    event: StreamEvent,
    state: dict[str, Any],
) -> list[DownstreamChunk]:
    """逐事件把 Responses SSE 直接映射为 Claude SSE。"""
    del original_request
    if event.kind == "done":
        return _finalize_responses_to_claude_stream(model_name, translated_request, state)
    if event.kind != "json" or not isinstance(event.payload, dict):
        return []
    payload = event.payload
    event_type = str(payload.get("type") or event.event or "").strip().lower()
    outputs: list[DownstreamChunk] = []

    if event_type == "response.created":
        response = payload.get("response") or {}
        if isinstance(response, dict):
            state["message_id"] = str(response.get("id") or state.get("message_id") or "")
            state["model"] = str(response.get("model") or translated_request.get("model") or model_name)
        outputs.extend(_ensure_claude_stream_started(model_name, translated_request, state))
        return outputs

    outputs.extend(_ensure_claude_stream_started(model_name, translated_request, state))
    if event_type in {"response.output_text.delta", "response.output_text.done"}:
        value = payload.get("delta") if event_type.endswith("delta") else payload.get("text")
        if isinstance(value, str):
            outputs.extend(_ensure_claude_stream_block(state, "text", payload))
            if event_type.endswith("delta"):
                state["active_block"]["text"] += value
                outputs.append(
                    _claude_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": state["active_block"]["index"],
                            "delta": {"type": "text_delta", "text": value},
                        },
                    )
                )
        return outputs

    if event_type == "response.output_text.annotation.added":
        annotation = payload.get("annotation")
        if isinstance(annotation, dict):
            outputs.extend(_ensure_claude_stream_block(state, "text", payload))
            text = str(state["active_block"].get("text") or "")
            citations = responses_annotations_to_claude([annotation], text)
            for citation in citations:
                outputs.append(
                    _claude_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": state["active_block"]["index"],
                            "delta": {"type": "citations_delta", "citation": citation},
                        },
                    )
                )
        return outputs

    if event_type in {
        "response.reasoning_summary_text.delta",
        "response.reasoning_summary_text.done",
    }:
        value = payload.get("delta") if event_type.endswith("delta") else payload.get("text")
        if isinstance(value, str):
            key = str(payload.get("item_id") or payload.get("output_index") or "")
            reasoning = state.setdefault("pending_reasoning", {}).setdefault(key, {"text": ""})
            if event_type.endswith("delta"):
                reasoning["text"] += value
            elif not reasoning["text"]:
                reasoning["text"] = value
        return outputs

    if event_type == "response.output_item.added":
        item = payload.get("item") or {}
        if isinstance(item, dict):
            item_type = str(item.get("type") or "").strip().lower()
            if item_type in {"function_call", "custom_tool_call"}:
                outputs.extend(_start_responses_tool_block(state, item, payload))
            elif item_type == "reasoning":
                key = str(item.get("id") or payload.get("item_id") or payload.get("output_index") or "")
                state.setdefault("pending_reasoning", {}).setdefault(key, {"text": "", "item": {}})["item"] = (
                    copy.deepcopy(item)
                )
        return outputs

    if event_type in {
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.custom_tool_call_input.delta",
        "response.custom_tool_call_input.done",
    }:
        value = payload.get("delta")
        if value is None:
            value = payload.get("arguments")
        if value is None:
            value = payload.get("input")
        if isinstance(value, str):
            active = state.get("active_block")
            if not isinstance(active, dict) or active.get("type") != "tool_use":
                item = {
                    "type": "custom_tool_call" if "custom_tool" in event_type else "function_call",
                    "call_id": payload.get("call_id") or payload.get("item_id"),
                    "name": payload.get("name"),
                }
                outputs.extend(_start_responses_tool_block(state, item, payload))
                active = state.get("active_block")
            if isinstance(active, dict):
                is_done = event_type.endswith("done")
                if is_done:
                    complete_value = value or active["arguments"]
                    if not active["arguments"]:
                        active["arguments"] = complete_value
                else:
                    active["arguments"] += value
                    complete_value = active["arguments"]
                if active.get("custom") and is_done:
                    partial_json = json.dumps({"input": complete_value}, ensure_ascii=False)
                else:
                    partial_json = value
                should_emit = (not active.get("custom") and (not is_done or not active.get("emitted_arguments"))) or (
                    active.get("custom") and is_done
                )
                if should_emit:
                    outputs.append(
                        _claude_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": active["index"],
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": partial_json,
                                },
                            },
                        )
                    )
                    active["emitted_arguments"] = True
        return outputs

    if event_type == "response.output_item.done":
        item = payload.get("item") or {}
        if isinstance(item, dict):
            if str(item.get("type") or "").strip().lower() == "reasoning":
                key = str(item.get("id") or payload.get("item_id") or payload.get("output_index") or "")
                reasoning = state.setdefault("pending_reasoning", {}).pop(key, {})
                pending_item = reasoning.get("item") if isinstance(reasoning, dict) else None
                if isinstance(pending_item, dict):
                    done_item = item
                    item = {**pending_item, **done_item}
                    if not done_item.get("encrypted_content") and pending_item.get("encrypted_content"):
                        item["encrypted_content"] = pending_item["encrypted_content"]
                text = str(reasoning.get("text") or "") if isinstance(reasoning, dict) else ""
                if text and not _responses_reasoning_text(item):
                    item = {**item, "summary": [{"type": "summary_text", "text": text}]}
            outputs.extend(_complete_responses_item_to_claude(state, item, payload))
        return outputs

    if event_type in {"response.completed", "response.done", "response.incomplete"}:
        response = payload.get("response") or {}
        if isinstance(response, dict):
            if response.get("model") not in (None, ""):
                state["response_model"] = str(response["model"])
            if isinstance(response.get("usage"), dict):
                state["usage"] = openai_usage_to_claude(response["usage"])
            state["finish_reason"] = _responses_stop_reason(response)
        outputs.extend(_finalize_responses_to_claude_stream(model_name, translated_request, state))
        return outputs

    if event_type in {"error", "response.failed"}:
        error = payload.get("error")
        if not isinstance(error, dict) and isinstance(payload.get("response"), dict):
            error = payload["response"].get("error")
        outputs.append(
            _claude_event(
                "error",
                {
                    "type": "error",
                    "error": copy.deepcopy(error)
                    if isinstance(error, dict)
                    else {"type": "upstream_error", "message": "Upstream Responses request failed"},
                },
            )
        )
        state["completed"] = True
    return outputs


def translate_claude_stream_to_openai_responses(
    model_name: str,
    original_request: dict[str, Any],
    translated_request: dict[str, Any],
    event: StreamEvent,
    state: dict[str, Any],
) -> list[DownstreamChunk]:
    """逐事件把 Claude SSE 直接映射为 Responses SSE。"""
    del translated_request
    if event.kind == "done" or event.kind != "json" or not isinstance(event.payload, dict):
        return []
    payload = event.payload
    event_type = str(payload.get("type") or event.event or "").strip().lower()
    outputs: list[DownstreamChunk] = []

    if event_type == "message_start":
        message = payload.get("message") or {}
        if not isinstance(message, dict):
            message = {}
        state.update(
            {
                "response_id": str(message.get("id") or f"resp_{model_name}"),
                "model": str(message.get("model") or model_name),
                "created_at": int(time.time()),
                "next_output_index": 0,
                "output": [],
                "tool_identities": _responses_tool_identities(original_request),
                "usage": copy.deepcopy(message.get("usage") or {}),
                "sequence": 0,
            }
        )
        response = _responses_stream_response(state, "in_progress")
        outputs.append(_responses_event(state, "response.created", {"response": response}))
        outputs.append(_responses_event(state, "response.in_progress", {"response": response}))
        return outputs

    if event_type == "content_block_start":
        block = payload.get("content_block") or {}
        if not isinstance(block, dict):
            return []
        outputs.extend(_start_claude_responses_block(state, payload, block))
        return outputs

    if event_type == "content_block_delta":
        delta = payload.get("delta") or {}
        block_state = state.get("blocks", {}).get(safe_int(payload.get("index")))
        if not isinstance(delta, dict):
            return []
        delta_type = str(delta.get("type") or "").strip().lower()
        if delta_type == "citations_delta" and not isinstance(block_state, dict):
            block_state = state.get("open_message")
        if not isinstance(block_state, dict):
            return []
        if delta_type == "text_delta" and isinstance(delta.get("text"), str):
            block_state["text"] += delta["text"]
            outputs.append(
                _responses_event(
                    state,
                    "response.output_text.delta",
                    {
                        "item_id": block_state["id"],
                        "output_index": block_state["output_index"],
                        "content_index": 0,
                        "delta": delta["text"],
                    },
                )
            )
        elif delta_type == "citations_delta" and isinstance(delta.get("citation"), dict):
            annotations = claude_citations_to_responses([delta["citation"]], block_state["text"])
            block_state.setdefault("annotations", []).extend(annotations)
            for annotation in annotations:
                outputs.append(
                    _responses_event(
                        state,
                        "response.output_text.annotation.added",
                        {
                            "item_id": block_state["id"],
                            "output_index": block_state["output_index"],
                            "content_index": 0,
                            "annotation": annotation,
                        },
                    )
                )
        elif delta_type == "thinking_delta" and isinstance(delta.get("thinking"), str):
            block_state["text"] += delta["thinking"]
            outputs.append(
                _responses_event(
                    state,
                    "response.reasoning_summary_text.delta",
                    {
                        "item_id": block_state["id"],
                        "output_index": block_state["output_index"],
                        "summary_index": 0,
                        "delta": delta["thinking"],
                    },
                )
            )
        elif delta_type == "signature_delta" and isinstance(delta.get("signature"), str):
            block_state["encrypted_content"] = delta["signature"]
        elif delta_type == "input_json_delta" and isinstance(delta.get("partial_json"), str):
            block_state["arguments"] += delta["partial_json"]
            if not block_state.get("custom"):
                outputs.append(
                    _responses_event(
                        state,
                        "response.function_call_arguments.delta",
                        {
                            "item_id": block_state["id"],
                            "output_index": block_state["output_index"],
                            "delta": delta["partial_json"],
                        },
                    )
                )
        return outputs

    if event_type == "content_block_stop":
        block_index = safe_int(payload.get("index"))
        block_state = state.get("blocks", {}).get(block_index)
        if isinstance(block_state, dict) and block_state.get("type") != "text":
            outputs.extend(_finish_claude_responses_block(state, block_index))
        return outputs

    if event_type == "message_delta":
        if isinstance(payload.get("usage"), dict):
            state.setdefault("usage", {}).update(copy.deepcopy(payload["usage"]))
        delta = payload.get("delta") or {}
        if isinstance(delta, dict):
            state["stop_reason"] = delta.get("stop_reason")
        return []

    if event_type == "message_stop":
        outputs.extend(_finish_open_responses_message(state))
        incomplete = state.get("stop_reason") == "max_tokens"
        response = _responses_stream_response(state, "incomplete" if incomplete else "completed")
        response["output"] = copy.deepcopy(state.get("output") or [])
        if incomplete:
            response["incomplete_details"] = {"reason": "max_output_tokens"}
        if state.get("usage"):
            response["usage"] = _claude_usage_to_responses(state["usage"])
        _copy_response_request_fields(original_request, response)
        event_name = "response.incomplete" if incomplete else "response.completed"
        outputs.append(_responses_event(state, event_name, {"response": response}))
        state["completed"] = True
        return outputs

    if event_type == "error":
        error = payload.get("error")
        outputs.append(
            _responses_event(
                state,
                "response.failed",
                {
                    "response": {
                        **_responses_stream_response(state, "failed"),
                        "error": copy.deepcopy(error)
                        if isinstance(error, dict)
                        else {"type": "upstream_error", "message": "Upstream Claude request failed"},
                    }
                },
            )
        )
    return outputs


def _copy_cache_control(source: dict[str, Any], target: dict[str, Any]) -> None:
    if isinstance(source.get("cache_control"), dict):
        target["cache_control"] = copy.deepcopy(source["cache_control"])


def _parse_data_url(value: str) -> tuple[str, str] | None:
    if not value.startswith("data:") or ";base64," not in value:
        return None
    metadata, data = value[5:].split(";base64,", 1)
    if not data:
        return None
    return metadata or "application/octet-stream", data


def _responses_content_to_claude(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "").strip().lower()
        block: dict[str, Any] | None = None
        if part_type in {"input_text", "output_text", "text"} and isinstance(part.get("text"), str):
            block = {"type": "text", "text": part["text"]}
        elif part_type == "input_image":
            url = part.get("image_url") or part.get("url")
            if isinstance(url, str):
                parsed = _parse_data_url(url)
                if parsed is not None:
                    block = {
                        "type": "image",
                        "source": {"type": "base64", "media_type": parsed[0], "data": parsed[1]},
                    }
                else:
                    block = {"type": "image", "source": {"type": "url", "url": url}}
        elif part_type == "input_file" and isinstance(part.get("file_data"), str):
            file_data = part["file_data"]
            parsed = _parse_data_url(file_data)
            media_type, data = parsed or ("application/octet-stream", file_data)
            block = {
                "type": "document",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            }
        if block is not None:
            _copy_cache_control(part, block)
            blocks.append(block)
    return blocks


def _responses_system_content_to_claude(content: Any) -> list[dict[str, Any]]:
    """保留系统级文本，并用类型标记暴露无法无损承载的内容。"""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "unknown").strip().lower() or "unknown"
        if part_type in {"input_text", "output_text", "text"}:
            text = part.get("text")
            if not isinstance(text, str) or not text:
                continue
            block: dict[str, Any] = {"type": "text", "text": text}
        else:
            block = {"type": part_type}
        _copy_cache_control(part, block)
        blocks.append(block)
    return blocks


def _responses_output_part_to_claude(part: Any) -> dict[str, Any] | None:
    if not isinstance(part, dict):
        return None
    part_type = str(part.get("type") or "").strip().lower()
    if part_type in {"output_text", "text"} and isinstance(part.get("text"), str):
        block: dict[str, Any] = {"type": "text", "text": part["text"]}
        if isinstance(part.get("annotations"), list) and part["annotations"]:
            block["citations"] = responses_annotations_to_claude(part["annotations"], part["text"])
        return block
    if part_type == "refusal" and isinstance(part.get("refusal"), str):
        return {"type": "text", "text": part["refusal"]}
    return None


def _claude_content_to_responses(block: dict[str, Any], role: str) -> dict[str, Any] | None:
    block_type = str(block.get("type") or "").strip().lower()
    if block_type == "text":
        part: dict[str, Any] = {
            "type": "output_text" if role == "assistant" else "input_text",
            "text": str(block.get("text") or ""),
        }
        return part
    if block_type == "image":
        source = block.get("source") or {}
        if not isinstance(source, dict):
            return None
        if source.get("type") == "base64" and source.get("data"):
            url = f"data:{source.get('media_type') or 'image/png'};base64,{source['data']}"
        elif source.get("type") == "url" and source.get("url"):
            url = str(source["url"])
        else:
            return None
        part = {"type": "input_image", "image_url": url}
        return part
    if block_type == "document":
        source = block.get("source") or {}
        if not isinstance(source, dict) or not source.get("data"):
            return None
        part = {
            "type": "input_file",
            "file_data": f"data:{source.get('media_type') or 'application/octet-stream'};base64,{source['data']}",
        }
        return part
    return None


def _responses_tool_output_to_claude(output: Any) -> Any:
    if isinstance(output, list):
        blocks = _responses_content_to_claude(output)
        return blocks or normalize_tool_result_content(output)
    return normalize_tool_result_content(output)


def _append_claude_message(messages: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]) -> None:
    if not blocks:
        return
    if messages and messages[-1].get("role") == role and isinstance(messages[-1].get("content"), list):
        messages[-1]["content"].extend(blocks)
        return
    messages.append({"role": role, "content": blocks})


def _responses_reasoning_text(item: dict[str, Any]) -> str:
    for field in ("summary", "content"):
        parts = item.get(field)
        if isinstance(parts, list):
            text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
            if text:
                return text
    return ""


def _responses_reasoning_to_claude(item: dict[str, Any]) -> dict[str, Any] | None:
    encrypted = str(item.get("encrypted_content") or "").strip()
    if encrypted.startswith(CLAUDE_REDACTED_THINKING_PREFIX):
        data = encrypted[len(CLAUDE_REDACTED_THINKING_PREFIX) :].strip()
        return {"type": "redacted_thinking", "data": data} if data else None
    compatible_signature = _compatible_claude_signature(encrypted)
    if compatible_signature is None:
        return None
    return {
        "type": "thinking",
        "thinking": _responses_reasoning_text(item),
        "signature": compatible_signature,
    }


def _claude_reasoning_carrier(block: dict[str, Any]) -> str:
    if str(block.get("type") or "").strip().lower() == "redacted_thinking":
        data = str(block.get("data") or "")
        return f"{CLAUDE_REDACTED_THINKING_PREFIX}{data}" if data else ""
    return str(block.get("signature") or "")


def _claude_reasoning_to_responses(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "reasoning",
        "encrypted_content": _claude_reasoning_carrier(block),
        "summary": [{"type": "summary_text", "text": str(block.get("thinking") or "")}],
    }


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {}


def _qualify_tool_name(namespace: Any, name: Any) -> str:
    namespace_text = str(namespace or "").strip()
    name_text = str(name or "").strip()
    if not namespace_text or name_text.startswith("mcp__"):
        return name_text
    if name_text == namespace_text or name_text.startswith(f"{namespace_text}__"):
        return name_text
    separator = "" if namespace_text.endswith("__") else "__"
    return f"{namespace_text}{separator}{name_text}"


def _sanitize_claude_tool_id(value: Any) -> str:
    sanitized = _CLAUDE_TOOL_ID_PATTERN.sub("_", str(value or ""))
    return sanitized or f"toolu_{secrets.token_urlsafe(18)}"


def _compatible_claude_signature(value: Any) -> str | None:
    """识别可向 Claude 原样重放的 E/R/CAIS thinking 签名。"""
    signature = str(value or "").strip()
    if not signature:
        return None
    if "#" in signature:
        prefix, payload = signature.split("#", 1)
        if prefix.strip().lower() not in {
            "claude",
            "anthropic",
            "cais",
            "claude-cais",
            "claude_cais",
            "ccmax",
            "claude-code-max",
            "claude_code_max",
        }:
            return None
        signature = payload.strip()
    if not signature or len(signature) > 32 * 1024 * 1024:
        return None
    if signature.startswith("R"):
        decoded = _decode_base64(signature)
        if decoded is None:
            return None
        try:
            signature = decoded.decode("ascii")
        except UnicodeDecodeError:
            return None
    decoded = _decode_base64(signature)
    if decoded is None:
        return None
    if signature.startswith("E") and _valid_classic_claude_signature(decoded):
        return signature
    if signature.startswith("C") and _valid_cais_claude_signature(decoded):
        return signature
    return None


def _decode_base64(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None


def _read_protobuf_fields(payload: bytes) -> list[tuple[int, int, Any]] | None:
    fields: list[tuple[int, int, Any]] = []
    offset = 0
    while offset < len(payload):
        tag, offset = _read_protobuf_varint(payload, offset)
        if tag is None or tag == 0:
            return None
        field_number = tag >> 3
        wire_type = tag & 7
        if wire_type == 0:
            value, offset = _read_protobuf_varint(payload, offset)
            if value is None:
                return None
        elif wire_type == 1:
            if offset + 8 > len(payload):
                return None
            value = payload[offset : offset + 8]
            offset += 8
        elif wire_type == 2:
            length, offset = _read_protobuf_varint(payload, offset)
            if length is None or offset + length > len(payload):
                return None
            value = payload[offset : offset + length]
            offset += length
        elif wire_type == 5:
            if offset + 4 > len(payload):
                return None
            value = payload[offset : offset + 4]
            offset += 4
        else:
            return None
        fields.append((field_number, wire_type, value))
    return fields


def _read_protobuf_varint(payload: bytes, offset: int) -> tuple[int | None, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(payload):
            return None, offset
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
    return None, offset


def _protobuf_bytes_field(payload: bytes, field_number: int) -> bytes | None:
    fields = _read_protobuf_fields(payload)
    if fields is None:
        return None
    values = [value for number, wire_type, value in fields if number == field_number and wire_type == 2]
    return values[-1] if values else None


def _valid_classic_claude_signature(payload: bytes) -> bool:
    if not payload or payload[0] != 0x12:
        return False
    container = _protobuf_bytes_field(payload, 2)
    channel = _protobuf_bytes_field(container, 1) if container is not None else None
    fields = _read_protobuf_fields(channel) if channel is not None else None
    return bool(fields and any(number == 1 and wire_type == 0 for number, wire_type, _value in fields))


def _valid_cais_claude_signature(payload: bytes) -> bool:
    if not payload or payload[0] != 0x08:
        return False
    fields = _read_protobuf_fields(payload)
    if not fields or not any(number == 1 and wire_type == 0 for number, wire_type, _value in fields):
        return False
    container = _protobuf_bytes_field(payload, 2)
    channel = _protobuf_bytes_field(container, 1) if container is not None else None
    channel_fields = _read_protobuf_fields(channel) if channel is not None else None
    if not channel_fields:
        return False
    has_signature = any(number == 5 and wire_type == 2 and value for number, wire_type, value in channel_fields)
    has_model = any(
        number == 6 and wire_type == 2 and bytes(value).startswith(b"claude-")
        for number, wire_type, value in channel_fields
    )
    return has_signature and has_model


def _split_qualified_tool_name(
    qualified_name: str, identities: dict[str, tuple[str, str, str]]
) -> tuple[str, str, str]:
    return identities.get(qualified_name, (qualified_name, "", "function"))


def _responses_tool_name(tool: dict[str, Any]) -> str:
    name = str(tool.get("name") or "").strip()
    if name:
        return name
    function = tool.get("function")
    return str(function.get("name") or "").strip() if isinstance(function, dict) else ""


def _responses_tool_descriptors(body: Any, *, filter_apply_patch: bool = True) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    sources: list[tuple[list[Any], int]] = []
    if isinstance(body.get("tools"), list):
        sources.append((body["tools"], 0))
    if isinstance(body.get("input"), list):
        for item in body["input"]:
            if (
                isinstance(item, dict)
                and str(item.get("type") or "").strip().lower() == "additional_tools"
                and isinstance(item.get("tools"), list)
            ):
                sources.append((item["tools"], 1))

    descriptors: list[dict[str, Any]] = []

    def append_descriptor(
        tool: dict[str, Any],
        name: str,
        namespace: str,
        tool_type: str,
        priority: int,
        direct: bool,
    ) -> None:
        if not name or tool_type in _UNSUPPORTED_RESPONSES_TOOL_TYPES:
            return
        if filter_apply_patch and tool_type == "custom" and _responses_tool_name(tool) == "apply_patch":
            return
        descriptors.append(
            {
                "tool": tool,
                "name": name,
                "child_name": _responses_tool_name(tool) if namespace else "",
                "namespace": namespace,
                "tool_type": tool_type,
                "priority": priority,
                "direct": direct,
                "order": len(descriptors),
            }
        )

    for tools, priority in sources:
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_type = str(tool.get("type") or "function").strip().lower()
            if tool_type == "namespace":
                namespace = str(tool.get("name") or "").strip()
                for child in tool.get("tools") or []:
                    if not isinstance(child, dict):
                        continue
                    child_type = str(child.get("type") or "function").strip().lower()
                    if child_type not in {"function", "custom"}:
                        continue
                    child_name = _responses_tool_name(child)
                    append_descriptor(
                        child,
                        _qualify_tool_name(namespace, child_name),
                        namespace,
                        child_type,
                        priority,
                        False,
                    )
                continue
            if tool_type == "web_search" and tool.get("external_web_access") is False:
                continue
            name = _responses_tool_name(tool)
            if tool_type == "web_search" and not name:
                name = "web_search"
            append_descriptor(tool, name, "", tool_type, priority, True)
    return descriptors


def _responses_tool_winners(body: Any, *, filter_apply_patch: bool = True) -> list[dict[str, Any]]:
    descriptors = _responses_tool_descriptors(body, filter_apply_patch=filter_apply_patch)
    winners: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors:
        current = winners.get(descriptor["name"])
        rank = (descriptor["priority"], 0 if descriptor["direct"] else 1, descriptor["order"])
        if current is None:
            winners[descriptor["name"]] = descriptor
            continue
        current_rank = (current["priority"], 0 if current["direct"] else 1, current["order"])
        if rank < current_rank:
            winners[descriptor["name"]] = descriptor
    return [descriptor for descriptor in descriptors if winners.get(descriptor["name"]) is descriptor]


def _responses_tool_identities(
    body: dict[str, Any], *, filter_apply_patch: bool = True
) -> dict[str, tuple[str, str, str]]:
    return {
        descriptor["name"]: (
            descriptor["child_name"] or descriptor["name"],
            descriptor["namespace"],
            descriptor["tool_type"],
        )
        for descriptor in _responses_tool_winners(body, filter_apply_patch=filter_apply_patch)
    }


def _responses_tools_to_claude(body: Any) -> list[dict[str, Any]]:
    translated: list[dict[str, Any]] = []
    for descriptor in _responses_tool_winners(body):
        tool = descriptor["tool"]
        qualified_name = descriptor["name"]
        tool_type = descriptor["tool_type"]
        if tool_type == "web_search":
            translated_tool: dict[str, Any] = {
                "type": "web_search_20250305",
                "name": qualified_name,
            }
            if tool.get("max_uses") is not None:
                translated_tool["max_uses"] = safe_int(tool["max_uses"])
            filters = tool.get("filters")
            if isinstance(filters, dict) and isinstance(filters.get("allowed_domains"), list):
                translated_tool["allowed_domains"] = copy.deepcopy(filters["allowed_domains"])
            elif isinstance(tool.get("allowed_domains"), list):
                translated_tool["allowed_domains"] = copy.deepcopy(tool["allowed_domains"])
            if isinstance(tool.get("user_location"), dict):
                translated_tool["user_location"] = copy.deepcopy(tool["user_location"])
            translated.append(translated_tool)
            continue
        if tool_type not in {"function", "custom"}:
            translated.append(copy.deepcopy(tool))
            continue
        input_schema = _responses_tool_schema(tool)
        if tool_type == "custom":
            input_schema = {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
            }
        translated_tool = {
            "name": qualified_name,
            "description": _responses_tool_description(tool),
            "input_schema": _normalize_claude_tool_schema(input_schema),
        }
        _copy_cache_control(tool, translated_tool)
        function = tool.get("function")
        if "cache_control" not in translated_tool and isinstance(function, dict):
            _copy_cache_control(function, translated_tool)
        translated.append(translated_tool)
    return translated


def _responses_tool_description(tool: dict[str, Any]) -> str:
    if tool.get("description") is not None:
        return str(tool["description"])
    function = tool.get("function")
    return str(function.get("description") or "") if isinstance(function, dict) else ""


def _responses_tool_schema(tool: dict[str, Any]) -> Any:
    for key in ("parameters", "parametersJsonSchema", "input_schema"):
        if tool.get(key) is not None:
            return tool[key]
    function = tool.get("function")
    if isinstance(function, dict):
        for key in ("parameters", "parametersJsonSchema"):
            if function.get(key) is not None:
                return function[key]
    return None


def _normalize_claude_tool_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    normalized = copy.deepcopy(schema)
    properties = normalized.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    for union_name in ("anyOf", "oneOf", "allOf"):
        branches = normalized.pop(union_name, None)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            branch_type = branch.get("type")
            if branch_type not in (None, "object") and not (isinstance(branch_type, list) and "object" in branch_type):
                continue
            branch_properties = branch.get("properties")
            if isinstance(branch_properties, dict):
                for name, value in branch_properties.items():
                    properties.setdefault(name, copy.deepcopy(value))
            if union_name == "allOf" and isinstance(branch.get("required"), list):
                required = normalized.setdefault("required", [])
                if isinstance(required, list):
                    for name in branch["required"]:
                        if name not in required:
                            required.append(name)
    normalized["type"] = "object"
    normalized["properties"] = properties
    return normalized


def _claude_tools_to_responses(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    translated = []
    for tool in tools:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        translated.append(
            {
                "type": "function",
                "name": str(tool["name"]),
                "description": str(tool.get("description") or ""),
                "parameters": copy.deepcopy(tool.get("input_schema") or {"type": "object", "properties": {}}),
            }
        )
    return translated


def _claude_custom_tool_names(tools: Any) -> set[str]:
    if not isinstance(tools, list):
        return set()
    return {
        str(tool.get("name"))
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("type") or "").strip().lower() == "custom"
    }


def _claude_tool_use_to_responses(block: dict[str, Any], custom_names: set[str]) -> dict[str, Any]:
    name = str(block.get("name") or "")
    tool_input = block.get("input") or {}
    if name in custom_names:
        return {
            "type": "custom_tool_call",
            "call_id": str(block.get("id") or ""),
            "name": name,
            "input": str(tool_input.get("input") or "") if isinstance(tool_input, dict) else str(tool_input),
        }
    return {
        "type": "function_call",
        "call_id": str(block.get("id") or ""),
        "name": name,
        "arguments": json.dumps(tool_input, ensure_ascii=False),
    }


def _claude_tool_use_response_to_responses(
    block: dict[str, Any],
    identities: dict[str, tuple[str, str, str]],
    *,
    item_id_prefix: str,
) -> dict[str, Any]:
    qualified_name = str(block.get("name") or "")
    name, namespace, tool_type = _split_qualified_tool_name(qualified_name, identities)
    call_id = str(block.get("id") or "")
    tool_input = block.get("input") or {}
    if tool_type == "custom":
        result: dict[str, Any] = {
            "id": f"ctc_{call_id or item_id_prefix}",
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": call_id,
            "name": name,
            "input": str(tool_input.get("input") or "") if isinstance(tool_input, dict) else str(tool_input),
        }
    else:
        result = {
            "id": f"fc_{call_id or item_id_prefix}",
            "type": "function_call",
            "status": "completed",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(tool_input, ensure_ascii=False),
        }
    if namespace:
        result["namespace"] = namespace
    return result


def _claude_tool_choice_to_responses(tool_choice: Any) -> Any:
    if not isinstance(tool_choice, dict):
        return None
    tool_type = str(tool_choice.get("type") or "").strip().lower()
    if tool_type == "auto":
        return "auto"
    if tool_type == "any":
        return "required"
    if tool_type == "tool" and tool_choice.get("name"):
        return {"type": "function", "name": str(tool_choice["name"])}
    if tool_type == "none":
        return "none"
    return None


def _responses_tool_choice_disables_tools(tool_choice: Any) -> bool:
    if isinstance(tool_choice, str):
        return tool_choice.strip().lower() == "none"
    return isinstance(tool_choice, dict) and str(tool_choice.get("type") or "").strip().lower() == "none"


def _responses_tool_choice_to_claude(
    tool_choice: Any,
    tools: list[dict[str, Any]],
    body: dict[str, Any],
) -> Any:
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"type": "auto"}
        if tool_choice == "required" and tools:
            return {"type": "any"}
        return None
    if not isinstance(tool_choice, dict):
        return None
    choice_type = str(tool_choice.get("type") or "").strip().lower()
    if choice_type in {"function", "custom"}:
        name = tool_choice.get("name")
        nested = tool_choice.get(choice_type)
        if isinstance(nested, dict):
            name = nested.get("name") or name
        qualified = _qualify_tool_name(tool_choice.get("namespace"), name)
        accepted_names = {str(tool.get("name") or "") for tool in tools}
        if qualified not in accepted_names:
            identities = _responses_tool_identities(body)
            aliases = {
                identity[0]: qualified_name
                for qualified_name, identity in identities.items()
                if identity[1] and identity[0] not in identities
            }
            qualified = aliases.get(str(name or ""), qualified)
        if qualified in accepted_names:
            return {"type": "tool", "name": qualified}
    return None


def _claude_usage_to_responses(usage: dict[str, Any]) -> dict[str, Any]:
    canonical = extract_canonical_usage(usage, "claude_chat") or {}
    input_tokens = safe_int(canonical.get("prompt_tokens"))
    output_tokens = safe_int(canonical.get("completion_tokens"))
    result: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    if canonical.get("cache_usage_status") == "known":
        details: dict[str, Any] = {}
        if canonical.get("_cache_read_present"):
            details["cached_tokens"] = safe_int(canonical.get("cache_read_input_tokens"))
        if canonical.get("_cache_creation_present"):
            details["cache_write_tokens"] = safe_int(canonical.get("cache_creation_input_tokens"))
        if details:
            result["input_tokens_details"] = details
    reasoning_tokens = safe_int(canonical.get("reasoning_tokens"))
    if reasoning_tokens:
        result["output_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return result


def _responses_stop_reason(payload: dict[str, Any]) -> str:
    incomplete = payload.get("incomplete_details")
    if isinstance(incomplete, dict) and incomplete.get("reason") == "max_output_tokens":
        return "max_tokens"
    return "end_turn"


def _copy_response_request_fields(request: dict[str, Any], response: dict[str, Any]) -> None:
    for field in (
        "instructions",
        "max_output_tokens",
        "max_tool_calls",
        "parallel_tool_calls",
        "previous_response_id",
        "prompt_cache_key",
        "reasoning",
        "safety_identifier",
        "service_tier",
        "store",
        "temperature",
        "text",
        "top_p",
        "metadata",
    ):
        if field in request:
            response[field] = copy.deepcopy(request[field])


def _claude_event(event_name: str, payload: dict[str, Any]) -> DownstreamChunk:
    return build_json_event_chunk(event_name, payload)


def _ensure_claude_stream_started(
    model_name: str,
    translated_request: dict[str, Any],
    state: dict[str, Any],
) -> list[DownstreamChunk]:
    if state.get("started"):
        return []
    state.update(
        {
            "started": True,
            "message_id": state.get("message_id") or f"msg_{model_name}_{int(time.time() * 1000)}",
            "model": state.get("model") or translated_request.get("model") or model_name,
            "next_block_index": 0,
            "active_block": None,
            "usage": {},
        }
    )
    return [
        _claude_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": state["message_id"],
                    "type": "message",
                    "role": "assistant",
                    "model": state["model"],
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )
    ]


def _close_claude_stream_block(state: dict[str, Any]) -> list[DownstreamChunk]:
    active = state.get("active_block")
    if not isinstance(active, dict):
        return []
    state["active_block"] = None
    return [
        _claude_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": active["index"]},
        )
    ]


def _ensure_claude_stream_block(
    state: dict[str, Any], block_type: str, payload: dict[str, Any]
) -> list[DownstreamChunk]:
    active = state.get("active_block")
    key = payload.get("item_id") or payload.get("output_index")
    if isinstance(active, dict) and active.get("type") == block_type and active.get("key") == key:
        return []
    outputs = _close_claude_stream_block(state)
    index = safe_int(state.get("next_block_index"))
    state["next_block_index"] = index + 1
    active = {"type": block_type, "index": index, "key": key, "text": "", "thinking": ""}
    state["active_block"] = active
    content_block = {"type": block_type, block_type: ""}
    if block_type == "thinking":
        content_block["signature"] = ""
    outputs.append(
        _claude_event(
            "content_block_start",
            {"type": "content_block_start", "index": index, "content_block": content_block},
        )
    )
    return outputs


def _start_responses_tool_block(
    state: dict[str, Any], item: dict[str, Any], payload: dict[str, Any]
) -> list[DownstreamChunk]:
    outputs = _close_claude_stream_block(state)
    index = safe_int(state.get("next_block_index"))
    state["next_block_index"] = index + 1
    item_type = str(item.get("type") or "").strip().lower()
    active = {
        "type": "tool_use",
        "index": index,
        "key": item.get("id") or payload.get("item_id") or payload.get("output_index"),
        "custom": item_type == "custom_tool_call",
        "arguments": "",
    }
    state["active_block"] = active
    outputs.append(
        _claude_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": _sanitize_claude_tool_id(item.get("call_id") or item.get("id")),
                    "name": _qualify_tool_name(item.get("namespace"), item.get("name")),
                    "input": {},
                },
            },
        )
    )
    return outputs


def _start_responses_reasoning_block(
    state: dict[str, Any], item: dict[str, Any], payload: dict[str, Any]
) -> list[DownstreamChunk]:
    encrypted = str(item.get("encrypted_content") or "")
    if encrypted.startswith(CLAUDE_REDACTED_THINKING_PREFIX):
        outputs = _close_claude_stream_block(state)
        index = safe_int(state.get("next_block_index"))
        state["next_block_index"] = index + 1
        outputs.append(
            _claude_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {
                        "type": "redacted_thinking",
                        "data": encrypted[len(CLAUDE_REDACTED_THINKING_PREFIX) :],
                    },
                },
            )
        )
        outputs.append(_claude_event("content_block_stop", {"type": "content_block_stop", "index": index}))
        return outputs
    compatible_signature = _compatible_claude_signature(encrypted)
    if compatible_signature is None:
        return []
    outputs = _ensure_claude_stream_block(state, "thinking", payload)
    active = state.get("active_block")
    if isinstance(active, dict):
        active["signature"] = compatible_signature
    return outputs


def _complete_responses_item_to_claude(
    state: dict[str, Any], item: dict[str, Any], payload: dict[str, Any]
) -> list[DownstreamChunk]:
    item_type = str(item.get("type") or "").strip().lower()
    outputs: list[DownstreamChunk] = []
    active = state.get("active_block")
    if item_type == "message" and not (isinstance(active, dict) and active.get("type") == "text"):
        for part in item.get("content") or []:
            if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                continue
            outputs.extend(_ensure_claude_stream_block(state, "text", payload))
            text = part["text"]
            if text:
                outputs.append(
                    _claude_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": state["active_block"]["index"],
                            "delta": {"type": "text_delta", "text": text},
                        },
                    )
                )
    elif item_type in {"function_call", "custom_tool_call"}:
        if not (isinstance(active, dict) and active.get("type") == "tool_use"):
            outputs.extend(_start_responses_tool_block(state, item, payload))
            active = state.get("active_block")
        if isinstance(active, dict) and not active.get("arguments"):
            value = item.get("input") if item_type == "custom_tool_call" else item.get("arguments")
            active["arguments"] = str(value or "")
            partial_json = (
                json.dumps({"input": active["arguments"]}, ensure_ascii=False)
                if item_type == "custom_tool_call"
                else active["arguments"] or "{}"
            )
            outputs.append(
                _claude_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": active["index"],
                        "delta": {"type": "input_json_delta", "partial_json": partial_json},
                    },
                )
            )
    elif item_type == "reasoning" and not (isinstance(active, dict) and active.get("type") == "thinking"):
        outputs.extend(_start_responses_reasoning_block(state, item, payload))
        text = _responses_reasoning_text(item)
        if text and isinstance(state.get("active_block"), dict):
            outputs.append(
                _claude_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": state["active_block"]["index"],
                        "delta": {"type": "thinking_delta", "thinking": text},
                    },
                )
            )
    return outputs


def _finalize_responses_to_claude_stream(
    model_name: str,
    translated_request: dict[str, Any],
    state: dict[str, Any],
) -> list[DownstreamChunk]:
    if state.get("completed"):
        return []
    outputs = _ensure_claude_stream_started(model_name, translated_request, state)
    active = state.get("active_block")
    if isinstance(active, dict) and active.get("type") == "thinking" and active.get("signature"):
        outputs.append(
            _claude_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": active["index"],
                    "delta": {"type": "signature_delta", "signature": active["signature"]},
                },
            )
        )
    outputs.extend(_close_claude_stream_block(state))
    usage = state.get("usage") or {}
    outputs.append(
        _claude_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": state.get("finish_reason") or "end_turn",
                    "stop_sequence": None,
                },
                "usage": copy.deepcopy(usage),
            },
        )
    )
    outputs.append(_claude_event("message_stop", {"type": "message_stop"}))
    state["completed"] = True
    return outputs


def _responses_event(state: dict[str, Any], event_name: str, fields: dict[str, Any]) -> DownstreamChunk:
    state["sequence"] = safe_int(state.get("sequence")) + 1
    return build_json_event_chunk(
        event_name,
        {"type": event_name, "sequence_number": state["sequence"], **fields},
    )


def _responses_stream_response(state: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "id": state.get("response_id") or "",
        "object": "response",
        "created_at": safe_int(state.get("created_at")),
        "status": status,
        "model": state.get("model") or "",
        "output": [],
    }


def _start_claude_responses_block(
    state: dict[str, Any], payload: dict[str, Any], block: dict[str, Any]
) -> list[DownstreamChunk]:
    block_index = safe_int(payload.get("index"))
    block_type = str(block.get("type") or "").strip().lower()
    if block_type not in {"text", "thinking", "redacted_thinking", "tool_use"}:
        return []
    if block_type == "text" and isinstance(state.get("open_message"), dict):
        block_state = state["open_message"]
        state.setdefault("blocks", {})[block_index] = block_state
        initial_text = str(block.get("text") or "")
        if not initial_text:
            return []
        block_state["text"] += initial_text
        return [
            _responses_event(
                state,
                "response.output_text.delta",
                {
                    "item_id": block_state["id"],
                    "output_index": block_state["output_index"],
                    "content_index": 0,
                    "delta": initial_text,
                },
            )
        ]

    outputs = _finish_open_responses_message(state)
    output_index = safe_int(state.get("next_output_index"))
    state["next_output_index"] = output_index + 1
    block_state: dict[str, Any] = {
        "block_index": block_index,
        "output_index": output_index,
        "type": block_type,
        "text": str(block.get("text") or block.get("thinking") or ""),
        "annotations": claude_citations_to_responses(block.get("citations"), str(block.get("text") or "")),
        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False)
        if block_type == "tool_use" and block.get("input")
        else "",
    }
    if block_type == "text":
        block_state["id"] = f"msg_{state.get('response_id')}_{block_index}"
        item = {
            "id": block_state["id"],
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        }
        outputs.append(
            _responses_event(state, "response.output_item.added", {"output_index": output_index, "item": item})
        )
        outputs.append(
            _responses_event(
                state,
                "response.content_part.added",
                {
                    "item_id": block_state["id"],
                    "output_index": output_index,
                    "content_index": 0,
                    "part": {"type": "output_text", "annotations": [], "text": ""},
                },
            )
        )
        state["open_message"] = block_state
        initial_text = block_state["text"]
        if initial_text:
            outputs.append(
                _responses_event(
                    state,
                    "response.output_text.delta",
                    {
                        "item_id": block_state["id"],
                        "output_index": output_index,
                        "content_index": 0,
                        "delta": initial_text,
                    },
                )
            )
    elif block_type in {"thinking", "redacted_thinking"}:
        block_state["id"] = f"rs_{state.get('response_id')}_{block_index}"
        block_state["encrypted_content"] = _claude_reasoning_carrier(block)
        item = {
            "id": block_state["id"],
            "type": "reasoning",
            "status": "in_progress",
            "encrypted_content": block_state["encrypted_content"],
            "summary": [],
        }
        outputs.append(
            _responses_event(state, "response.output_item.added", {"output_index": output_index, "item": item})
        )
        outputs.append(
            _responses_event(
                state,
                "response.reasoning_summary_part.added",
                {
                    "item_id": block_state["id"],
                    "output_index": output_index,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": ""},
                },
            )
        )
    elif block_type == "tool_use":
        tool_identities = state.get("tool_identities") or {}
        qualified_name = str(block.get("name") or "")
        name, namespace, tool_type = _split_qualified_tool_name(qualified_name, tool_identities)
        block_state.update(
            {
                "call_id": str(block.get("id") or ""),
                "name": name,
                "namespace": namespace,
                "custom": tool_type == "custom",
                "id": f"{'ctc' if tool_type == 'custom' else 'fc'}_{block.get('id') or block_index}",
            }
        )
        item = {
            "id": block_state["id"],
            "type": "custom_tool_call" if block_state["custom"] else "function_call",
            "status": "in_progress",
            "call_id": block_state["call_id"],
            "name": name,
            **({"input": ""} if block_state["custom"] else {"arguments": ""}),
        }
        if namespace:
            item["namespace"] = namespace
        outputs.append(
            _responses_event(state, "response.output_item.added", {"output_index": output_index, "item": item})
        )
    state.setdefault("blocks", {})[block_index] = block_state
    return outputs


def _finish_open_responses_message(state: dict[str, Any]) -> list[DownstreamChunk]:
    block = state.pop("open_message", None)
    if not isinstance(block, dict):
        return []
    return _finish_claude_responses_block(state, safe_int(block.get("block_index")))


def _finish_claude_responses_block(state: dict[str, Any], block_index: int) -> list[DownstreamChunk]:
    block = state.get("blocks", {}).get(block_index)
    if not isinstance(block, dict) or block.get("finished"):
        return []
    block["finished"] = True
    output_index = block["output_index"]
    outputs: list[DownstreamChunk] = []
    output_item: dict[str, Any]
    if block["type"] == "text":
        part = {
            "type": "output_text",
            "annotations": copy.deepcopy(block.get("annotations") or []),
            "text": block["text"],
        }
        outputs.append(
            _responses_event(
                state,
                "response.output_text.done",
                {
                    "item_id": block["id"],
                    "output_index": output_index,
                    "content_index": 0,
                    "text": block["text"],
                },
            )
        )
        outputs.append(
            _responses_event(
                state,
                "response.content_part.done",
                {
                    "item_id": block["id"],
                    "output_index": output_index,
                    "content_index": 0,
                    "part": part,
                },
            )
        )
        output_item = {
            "id": block["id"],
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [part],
        }
    elif block["type"] in {"thinking", "redacted_thinking"}:
        summary = [{"type": "summary_text", "text": block["text"]}]
        outputs.append(
            _responses_event(
                state,
                "response.reasoning_summary_text.done",
                {
                    "item_id": block["id"],
                    "output_index": output_index,
                    "summary_index": 0,
                    "text": block["text"],
                },
            )
        )
        output_item = {
            "id": block["id"],
            "type": "reasoning",
            "encrypted_content": block.get("encrypted_content") or "",
            "summary": summary,
        }
    else:
        arguments = block.get("arguments") or "{}"
        if block.get("custom"):
            parsed = _parse_json_object(arguments)
            custom_input = str(parsed.get("input") or "")
            outputs.append(
                _responses_event(
                    state,
                    "response.custom_tool_call_input.done",
                    {
                        "item_id": block["id"],
                        "output_index": output_index,
                        "input": custom_input,
                    },
                )
            )
            output_item = {
                "id": block["id"],
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": block["call_id"],
                "name": block["name"],
                "input": custom_input,
            }
        else:
            outputs.append(
                _responses_event(
                    state,
                    "response.function_call_arguments.done",
                    {
                        "item_id": block["id"],
                        "output_index": output_index,
                        "arguments": arguments,
                    },
                )
            )
            output_item = {
                "id": block["id"],
                "type": "function_call",
                "status": "completed",
                "call_id": block["call_id"],
                "name": block["name"],
                "arguments": arguments,
            }
        if block.get("namespace"):
            output_item["namespace"] = block["namespace"]
    outputs.append(
        _responses_event(
            state,
            "response.output_item.done",
            {"output_index": output_index, "item": output_item},
        )
    )
    state.setdefault("output", []).append(copy.deepcopy(output_item))
    return outputs
