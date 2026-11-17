"""把一次客户端请求的有界快照转换为训练记录。"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any

_GENERATION_FIELDS = (
    "temperature",
    "top_p",
    "top_k",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "stop",
    "stop_sequences",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "top_logprobs",
    "n",
    "seed",
    "reasoning_effort",
    "reasoning",
    "thinking",
    "verbosity",
    "service_tier",
    "parallel_tool_calls",
)
_MEDIA_TYPES = {
    "audio",
    "document",
    "file",
    "image",
    "image_url",
    "input_audio",
    "input_file",
    "input_image",
    "input_video",
    "video",
}
_MEDIA_DATA_FIELDS = {"base64", "b64_json", "bytes", "data"}
_TRUNCATED_MARKER = "$llm_recording_truncated"


def _add_issue(issues: list[str], issue: str) -> None:
    if issue not in issues:
        issues.append(issue)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sanitize_media(value: Any, issues: list[str], *, media_context: bool = False) -> Any:
    """保留媒体引用和结构，媒体正文以明确的未归档标记替换。"""
    if isinstance(value, list):
        return [_sanitize_media(item, issues, media_context=media_context) for item in value]
    if not isinstance(value, dict):
        return value

    kind = str(value.get("type") or "").strip().lower()
    is_media = (
        media_context
        or kind in _MEDIA_TYPES
        or "image_url" in value
        or any(field in value for field in ("media_type", "mime_type"))
        and any(field in value for field in _MEDIA_DATA_FIELDS)
    )
    result: dict[str, Any] = {}
    found_media_body = False
    for key, item in value.items():
        nested_media = is_media or key in {"audio", "file", "image", "image_url", "source", "video"}
        if is_media and key in _MEDIA_DATA_FIELDS and isinstance(item, str):
            result[key] = {
                "archived": False,
                "reason": "media_not_archived",
                "content_sha256": hashlib.sha256(item.encode("utf-8")).hexdigest(),
            }
            found_media_body = True
            continue
        if is_media and key in {"url", "image_url"} and isinstance(item, str) and item.startswith("data:"):
            media_type = item[5:].split(";", 1)[0] or None
            result[key] = {
                "archived": False,
                "reason": "media_not_archived",
                "source": "data_url",
                "media_type": media_type,
                "content_sha256": hashlib.sha256(item.encode("utf-8")).hexdigest(),
            }
            found_media_body = True
            continue
        result[key] = _sanitize_media(item, issues, media_context=nested_media)
    if is_media:
        result["capture_media_archived"] = False
        _add_issue(issues, "media_not_archived")
        if found_media_body:
            _add_issue(issues, "media_body_omitted")
    return result


def _prompt_ref(
    message: dict[str, Any], prompts: dict[str, dict[str, Any]], *, source: str | None = None
) -> dict[str, Any]:
    prompt = {"role": message.get("role"), "content": message.get("content")}
    digest = hashlib.sha256(_canonical_json(prompt)).hexdigest()
    reference = f"sha256:{digest}"
    prompts[reference] = prompt
    result: dict[str, Any] = {"prompt_ref": reference}
    if source is not None:
        result["source"] = source
    for key, value in message.items():
        if key not in {"role", "content"}:
            result[key] = copy.deepcopy(value)
    return result


def _prepare_input(
    request_data: dict[str, Any],
    source_format: str,
    issues: list[str],
) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    prompts: dict[str, dict[str, Any]] = {}
    result: list[Any] = []

    def append_message(message: Any) -> None:
        if isinstance(message, dict) and message.get("role") in {"system", "developer"} and "content" in message:
            result.append(_prompt_ref(message, prompts))
        else:
            result.append(copy.deepcopy(message))

    if source_format == "openai_chat":
        messages = request_data.get("messages")
        if isinstance(messages, list):
            for message in messages:
                append_message(message)
        return result, prompts

    if source_format == "claude_chat":
        system = request_data.get("system")
        if system is not None:
            result.append(_prompt_ref({"role": "system", "content": system}, prompts, source="claude.system"))
        messages = request_data.get("messages")
        if isinstance(messages, list):
            for message in messages:
                append_message(message)
        return result, prompts

    instructions = request_data.get("instructions")
    if instructions is not None:
        result.append(
            _prompt_ref(
                {"role": "developer", "content": instructions},
                prompts,
                source="responses.instructions",
            )
        )
    previous_response_id = request_data.get("previous_response_id")
    if previous_response_id is not None:
        result.append(
            {
                "type": "response_reference",
                "previous_response_id": previous_response_id,
                "context_complete": False,
            }
        )
        _add_issue(issues, "referenced_context_missing")
    responses_input = request_data.get("input")
    if isinstance(responses_input, list):
        for message in responses_input:
            append_message(message)
    elif isinstance(responses_input, str):
        result.append({"role": "user", "content": responses_input})
    elif responses_input is not None:
        result.append(copy.deepcopy(responses_input))
    return result, prompts


def _response_format(request_data: dict[str, Any]) -> Any:
    if "response_format" in request_data:
        return copy.deepcopy(request_data["response_format"])
    text = request_data.get("text")
    if isinstance(text, dict) and "format" in text:
        return copy.deepcopy(text["format"])
    output_config = request_data.get("output_config")
    if isinstance(output_config, dict) and "format" in output_config:
        return copy.deepcopy(output_config["format"])
    return None


def _new_parse_result() -> dict[str, Any]:
    return {
        "output": [],
        "finish_reason": None,
        "terminal": False,
        "failure": False,
        "cancelled": False,
        "parse_error": False,
        "stream": False,
        "issues": [],
    }


def _event_type(event: Any) -> str:
    payload = getattr(event, "payload", None)
    if isinstance(payload, dict) and payload.get("type"):
        return str(payload["type"]).strip().lower()
    return str(getattr(event, "event", "") or "").strip().lower()


def _decode_events(data: bytes) -> tuple[list[Any], bool]:
    """完整解码已采集的下游字节，畸形事件保留为文本并显式报告。"""
    from ..proxy_core import StreamEvent

    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError:
        text = data.decode("utf-8", "replace")
        decode_failed = True
    else:
        decode_failed = False
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_events = normalized.split("\n\n")
    events: list[Any] = []
    malformed = decode_failed
    for raw_event in raw_events:
        if not raw_event.strip():
            continue
        data_lines = [line[5:].lstrip(" ") for line in raw_event.split("\n") if line.startswith("data:")]
        if not data_lines:
            if raw_event.strip() and not raw_event.lstrip().startswith(":"):
                malformed = True
            continue
        event_text = "\n".join(data_lines)
        stripped = event_text.strip()
        if stripped == "[DONE]":
            events.append(StreamEvent(kind="done", payload="[DONE]", raw=stripped))
            continue
        try:
            payload = json.loads(stripped)
        except (TypeError, ValueError):
            malformed = True
            events.append(StreamEvent(kind="text", payload=stripped, raw=raw_event))
        else:
            event_name = next(
                (line[6:].strip() for line in raw_event.split("\n") if line.startswith("event:")),
                None,
            )
            events.append(StreamEvent(kind="json", payload=payload, raw=stripped, event=event_name))
    return events, malformed


def _parse_chat_nonstream(payload: dict[str, Any], result: dict[str, Any]) -> None:
    output = []
    finish_reasons = []
    choices = payload.get("choices")
    if not isinstance(choices, list):
        if payload.get("error") is not None:
            result["failure"] = True
        else:
            result["parse_error"] = True
        result["output"] = output
        result["terminal"] = True
        return
    for position, choice in enumerate(choices):
        if not isinstance(choice, dict):
            result["parse_error"] = True
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            message = {}
            result["parse_error"] = True
        item = copy.deepcopy(message)
        item["choice_index"] = _integer(choice.get("index"), position)
        item["finish_reason"] = choice.get("finish_reason")
        output.append(item)
        finish_reasons.append(choice.get("finish_reason"))
    result["output"] = output
    result["finish_reason"] = finish_reasons[0] if len(finish_reasons) == 1 else None
    result["terminal"] = True


def _merge_text(target: dict[str, Any], field: str, value: Any, *, replace: bool = False) -> None:
    if not isinstance(value, str):
        return
    if replace:
        target[field] = value
    else:
        target[field] = str(target.get(field) or "") + value


def _merge_name(target: dict[str, Any], incoming: Any) -> None:
    if not isinstance(incoming, str) or not incoming:
        return
    current = str(target.get("name") or "")
    if not current or incoming.startswith(current):
        target["name"] = incoming
    elif current.startswith(incoming) or incoming == current:
        return
    else:
        target["name"] = current + incoming


def _merge_chat_tool_calls(message: dict[str, Any], calls: Any) -> None:
    if not isinstance(calls, list):
        return
    target_calls = message.setdefault("tool_calls", [])
    if not isinstance(target_calls, list):
        target_calls = []
        message["tool_calls"] = target_calls
    for position, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        index = _integer(call.get("index"), position)
        while len(target_calls) <= index:
            target_calls.append({"type": "function", "function": {}})
        target = target_calls[index]
        if not isinstance(target, dict):
            target = {"type": "function", "function": {}}
            target_calls[index] = target
        if call.get("id") not in (None, ""):
            target["id"] = call["id"]
        if call.get("type") not in (None, ""):
            target["type"] = call["type"]
        function = call.get("function")
        if not isinstance(function, dict):
            continue
        target_function = target.setdefault("function", {})
        if not isinstance(target_function, dict):
            target_function = {}
            target["function"] = target_function
        _merge_name(target_function, function.get("name"))
        _merge_text(target_function, "arguments", function.get("arguments"))


def _merge_chat_delta(message: dict[str, Any], delta: dict[str, Any], *, full: bool = False) -> None:
    for field, value in delta.items():
        if field in {"content", "refusal", "reasoning", "reasoning_content"} and isinstance(value, str):
            _merge_text(message, field, value, replace=full)
        elif field == "tool_calls":
            if full:
                message[field] = copy.deepcopy(value)
            else:
                _merge_chat_tool_calls(message, value)
        elif field == "function_call" and isinstance(value, dict) and not full:
            function = message.setdefault("function_call", {})
            if not isinstance(function, dict):
                function = {}
                message["function_call"] = function
            _merge_name(function, value.get("name"))
            _merge_text(function, "arguments", value.get("arguments"))
        elif value is not None:
            message[field] = copy.deepcopy(value)


def _parse_chat_stream(events: list[Any], result: dict[str, Any]) -> None:
    choices: dict[int, dict[str, Any]] = {}
    saw_done = False
    for event in events:
        if getattr(event, "kind", None) == "done":
            saw_done = True
            continue
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            continue
        if payload.get("error") is not None or str(payload.get("type") or "").lower() == "error":
            result["failure"] = True
        raw_choices = payload.get("choices")
        if not isinstance(raw_choices, list):
            continue
        for position, choice in enumerate(raw_choices):
            if not isinstance(choice, dict):
                continue
            index = _integer(choice.get("index"), position)
            entry = choices.setdefault(index, {"message": {}, "finish_reason": None})
            full_message = choice.get("message")
            delta = choice.get("delta")
            if isinstance(full_message, dict):
                _merge_chat_delta(entry["message"], full_message, full=True)
            elif isinstance(delta, dict):
                _merge_chat_delta(entry["message"], delta)
            if choice.get("finish_reason") not in (None, ""):
                entry["finish_reason"] = choice["finish_reason"]
    output = []
    for index in sorted(choices):
        entry = choices[index]
        message = entry["message"]
        message.setdefault("role", "assistant")
        message["choice_index"] = index
        message["finish_reason"] = entry["finish_reason"]
        output.append(message)
    result["output"] = output
    result["finish_reason"] = output[0]["finish_reason"] if len(output) == 1 else None
    result["terminal"] = saw_done or bool(output) and all(item["finish_reason"] is not None for item in output)


def _coerce_content_list(target: dict[str, Any], field: str) -> list[Any]:
    value = target.get(field)
    if not isinstance(value, list):
        value = []
        target[field] = value
    return value


def _merge_block(target: dict[str, Any], source: dict[str, Any]) -> None:
    for field, value in source.items():
        if field in {"text", "refusal", "thinking", "signature"} and isinstance(value, str):
            current = str(target.get(field) or "")
            if len(value) >= len(current):
                target[field] = value
        elif value is not None:
            target[field] = copy.deepcopy(value)


def _ensure_response_item(
    items: dict[int, dict[str, Any]],
    ids: dict[str, int],
    payload: dict[str, Any],
    default: dict[str, Any],
) -> dict[str, Any]:
    item_id = str(payload.get("item_id") or default.get("id") or "")
    index_value = payload.get("output_index")
    if item_id and item_id in ids:
        index = ids[item_id]
    else:
        index = _integer(index_value, len(items))
    item = items.setdefault(index, {})
    if not item:
        item.update(copy.deepcopy(default))
    if item_id:
        ids[item_id] = index
        item.setdefault("id", item_id)
    return item


def _response_content_block(item: dict[str, Any], payload: dict[str, Any], kind: str) -> dict[str, Any]:
    content = _coerce_content_list(item, "content")
    index = _integer(payload.get("content_index"), 0)
    while len(content) <= index:
        content.append({"type": kind})
    block = content[index]
    if not isinstance(block, dict):
        block = {"type": kind}
        content[index] = block
    block.setdefault("type", kind)
    return block


def _merge_response_output(terminal: list[Any], accumulated: dict[int, dict[str, Any]]) -> list[Any]:
    output = [copy.deepcopy(item) for item in terminal if isinstance(item, dict)]
    positions = {str(item.get("id")): index for index, item in enumerate(output) if item.get("id")}
    for index, item in sorted(accumulated.items()):
        item_id = str(item.get("id") or "")
        position = positions.get(item_id)
        if position is None and index < len(output):
            position = index
        if position is None:
            output.append(copy.deepcopy(item))
            continue
        target = output[position]
        for field, value in item.items():
            if field in {"content", "summary"} and isinstance(value, list):
                current = target.get(field)
                if not isinstance(current, list) or not current:
                    target[field] = copy.deepcopy(value)
                else:
                    for block_index, block in enumerate(value):
                        if block_index >= len(current):
                            current.append(copy.deepcopy(block))
                        elif isinstance(block, dict) and isinstance(current[block_index], dict):
                            for block_field, block_value in block.items():
                                if current[block_index].get(block_field) in (None, ""):
                                    current[block_index][block_field] = copy.deepcopy(block_value)
            elif target.get(field) in (None, ""):
                target[field] = copy.deepcopy(value)
    return output


def _apply_terminal_response(response: dict[str, Any], result: dict[str, Any], status: str | None = None) -> None:
    output = response.get("output")
    result["output"] = copy.deepcopy(output) if isinstance(output, list) else []
    incomplete = response.get("incomplete_details")
    reason = incomplete.get("reason") if isinstance(incomplete, dict) else None
    actual_status = status or response.get("status")
    result["finish_reason"] = reason or actual_status
    result["failure"] = actual_status in {"failed", "error"}
    result["cancelled"] = actual_status in {"cancelled", "canceled"}


def _parse_responses_nonstream(payload: dict[str, Any], result: dict[str, Any]) -> None:
    if not isinstance(payload.get("output"), list):
        if payload.get("error") is not None:
            result["failure"] = True
        else:
            result["parse_error"] = True
    _apply_terminal_response(payload, result)
    result["terminal"] = True


def _parse_responses_stream(events: list[Any], result: dict[str, Any]) -> None:
    items: dict[int, dict[str, Any]] = {}
    ids: dict[str, int] = {}
    terminal_response: dict[str, Any] | None = None
    terminal_status: str | None = None
    for event in events:
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            continue
        kind = _event_type(event)
        if kind == "error" or payload.get("error") is not None and not kind.startswith("response."):
            result["failure"] = True
        if kind in {
            "response.completed",
            "response.done",
            "response.incomplete",
            "response.failed",
            "response.cancelled",
            "response.canceled",
        }:
            response = payload.get("response")
            if isinstance(response, dict):
                terminal_response = response
                terminal_status = response.get("status") or kind.removeprefix("response.")
            else:
                result["parse_error"] = True
            continue
        if kind == "response.output_item.added" or kind == "response.output_item.done":
            raw_item = payload.get("item")
            if isinstance(raw_item, dict):
                item = _ensure_response_item(
                    items, ids, {**payload, "item_id": raw_item.get("id") or payload.get("item_id")}, raw_item
                )
                _merge_block(item, raw_item)
                for field in ("content", "summary"):
                    if isinstance(raw_item.get(field), list):
                        target_list = _coerce_content_list(item, field)
                        for index, block in enumerate(raw_item[field]):
                            while len(target_list) <= index:
                                target_list.append({})
                            if isinstance(block, dict) and isinstance(target_list[index], dict):
                                _merge_block(target_list[index], block)
            continue
        if kind in {"response.content_part.added", "response.content_part.done"}:
            part = payload.get("part")
            if isinstance(part, dict):
                item = _ensure_response_item(items, ids, payload, {"type": "message", "role": "assistant"})
                block = _response_content_block(item, payload, str(part.get("type") or "output_text"))
                _merge_block(block, part)
            continue
        if kind in {
            "response.output_text.delta",
            "response.output_text.done",
            "response.refusal.delta",
            "response.refusal.done",
        }:
            item = _ensure_response_item(items, ids, payload, {"type": "message", "role": "assistant"})
            refusal = kind.startswith("response.refusal")
            block = _response_content_block(item, payload, "refusal" if refusal else "output_text")
            done = kind.endswith(".done")
            field = "refusal" if refusal else "text"
            value = payload.get(field) if done else payload.get("delta")
            _merge_text(block, field, value, replace=done)
            continue
        if kind in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
            item = _ensure_response_item(items, ids, payload, {"type": "function_call"})
            done = kind.endswith(".done")
            _merge_text(item, "arguments", payload.get("arguments") if done else payload.get("delta"), replace=done)
            continue
        if kind in {"response.custom_tool_call_input.delta", "response.custom_tool_call_input.done"}:
            item = _ensure_response_item(items, ids, payload, {"type": "custom_tool_call"})
            done = kind.endswith(".done")
            _merge_text(item, "input", payload.get("input") if done else payload.get("delta"), replace=done)
            continue
        if kind in {"response.reasoning_part.added", "response.reasoning_part.done"}:
            part = payload.get("part")
            if isinstance(part, dict):
                item = _ensure_response_item(items, ids, payload, {"type": "reasoning"})
                block = _response_content_block(item, payload, str(part.get("type") or "reasoning_text"))
                _merge_block(block, part)
            continue
        if kind in {"response.reasoning_text.delta", "response.reasoning_text.done"}:
            item = _ensure_response_item(items, ids, payload, {"type": "reasoning"})
            block = _response_content_block(item, payload, "reasoning_text")
            done = kind.endswith(".done")
            _merge_text(block, "text", payload.get("text") if done else payload.get("delta"), replace=done)
            continue
        if kind == "response.output_text.annotation.added" and isinstance(payload.get("annotation"), dict):
            item = _ensure_response_item(items, ids, payload, {"type": "message", "role": "assistant"})
            block = _response_content_block(item, payload, "output_text")
            block.setdefault("annotations", []).append(copy.deepcopy(payload["annotation"]))
            continue
        if kind in {"response.reasoning_summary_part.added", "response.reasoning_summary_part.done"}:
            part = payload.get("part")
            if isinstance(part, dict):
                item = _ensure_response_item(items, ids, payload, {"type": "reasoning"})
                summary = _coerce_content_list(item, "summary")
                index = _integer(payload.get("summary_index"), len(summary))
                while len(summary) <= index:
                    summary.append({})
                if isinstance(summary[index], dict):
                    _merge_block(summary[index], part)
            continue
        if kind in {"response.reasoning_summary_text.delta", "response.reasoning_summary_text.done"}:
            item = _ensure_response_item(items, ids, payload, {"type": "reasoning"})
            summary = _coerce_content_list(item, "summary")
            index = _integer(payload.get("summary_index"), 0)
            while len(summary) <= index:
                summary.append({"type": "summary_text", "text": ""})
            block = summary[index]
            if not isinstance(block, dict):
                block = {"type": "summary_text"}
                summary[index] = block
            done = kind.endswith(".done")
            _merge_text(block, "text", payload.get("text") if done else payload.get("delta"), replace=done)

    if terminal_response is not None:
        _apply_terminal_response(terminal_response, result, terminal_status)
        terminal_output = terminal_response.get("output")
        result["output"] = _merge_response_output(terminal_output if isinstance(terminal_output, list) else [], items)
        result["terminal"] = True
    else:
        result["output"] = [copy.deepcopy(items[index]) for index in sorted(items)]


def _claude_block(blocks: dict[int, dict[str, Any]], order: list[int], index: int) -> dict[str, Any]:
    if index not in blocks:
        blocks[index] = {}
        order.append(index)
    return blocks[index]


def _finish_claude_tool(block: dict[str, Any], result: dict[str, Any]) -> None:
    parts = block.pop("_input_json_parts", None)
    if not parts:
        return
    raw = "".join(parts)
    try:
        block["input"] = json.loads(raw)
    except (TypeError, ValueError):
        block["partial_json"] = raw
        _add_issue(result["issues"], "incomplete_tool_arguments")


def _parse_claude_nonstream(payload: dict[str, Any], result: dict[str, Any]) -> None:
    content = payload.get("content")
    if not isinstance(content, list):
        if payload.get("error") is not None:
            result["failure"] = True
        else:
            result["parse_error"] = True
        content = []
    result["output"] = [{"role": payload.get("role") or "assistant", "content": copy.deepcopy(content)}]
    result["finish_reason"] = payload.get("stop_reason")
    result["terminal"] = True


def _parse_claude_stream(events: list[Any], result: dict[str, Any]) -> None:
    blocks: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    role = "assistant"
    stop_reason = None
    for event in events:
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            continue
        kind = _event_type(event)
        if kind == "error" or payload.get("error") is not None:
            result["failure"] = True
        if kind == "message_start":
            message = payload.get("message")
            if isinstance(message, dict):
                role = message.get("role") or role
                if isinstance(message.get("content"), list):
                    for index, source in enumerate(message["content"]):
                        if isinstance(source, dict):
                            _merge_block(_claude_block(blocks, order, index), source)
        elif kind == "content_block_start":
            index = _integer(payload.get("index"), len(order))
            source = payload.get("content_block")
            if isinstance(source, dict):
                _merge_block(_claude_block(blocks, order, index), source)
        elif kind == "content_block_delta":
            index = _integer(payload.get("index"), len(order))
            block = _claude_block(blocks, order, index)
            delta = payload.get("delta")
            if not isinstance(delta, dict):
                continue
            delta_type = str(delta.get("type") or "")
            if delta_type == "text_delta":
                block.setdefault("type", "text")
                _merge_text(block, "text", delta.get("text"))
            elif delta_type == "thinking_delta":
                block.setdefault("type", "thinking")
                _merge_text(block, "thinking", delta.get("thinking"))
            elif delta_type == "signature_delta":
                _merge_text(block, "signature", delta.get("signature"))
            elif delta_type == "input_json_delta" and isinstance(delta.get("partial_json"), str):
                block.setdefault("type", "tool_use")
                block.setdefault("_input_json_parts", []).append(delta["partial_json"])
            elif delta_type == "citations_delta" and isinstance(delta.get("citation"), dict):
                block.setdefault("citations", []).append(copy.deepcopy(delta["citation"]))
        elif kind == "content_block_stop":
            index = _integer(payload.get("index"), 0)
            if index in blocks:
                _finish_claude_tool(blocks[index], result)
        elif kind == "message_delta":
            delta = payload.get("delta")
            if isinstance(delta, dict) and delta.get("stop_reason") is not None:
                stop_reason = delta["stop_reason"]
        elif kind == "message_stop":
            result["terminal"] = True
    for block in blocks.values():
        _finish_claude_tool(block, result)
    content = []
    for index in order:
        block = blocks[index]
        if block.get("type") == "tool_use" and "partial_json" in block:
            _add_issue(result["issues"], "incomplete_tool_arguments")
        content.append(copy.deepcopy(block))
    result["output"] = [{"role": role, "content": content}]
    result["finish_reason"] = stop_reason


def _parse_output(data: bytes, source_format: str, outcome: str) -> dict[str, Any]:
    result = _new_parse_result()
    if not data:
        if outcome == "success":
            result["parse_error"] = True
        return result
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        if source_format == "openai_chat":
            _parse_chat_nonstream(payload, result)
        elif source_format == "openai_responses":
            _parse_responses_nonstream(payload, result)
        else:
            _parse_claude_nonstream(payload, result)
        return result

    result["stream"] = True
    events, malformed = _decode_events(data)
    result["parse_error"] = malformed
    if source_format == "openai_chat":
        _parse_chat_stream(events, result)
    elif source_format == "openai_responses":
        _parse_responses_stream(events, result)
    else:
        _parse_claude_stream(events, result)
    if not events and not malformed:
        result["parse_error"] = True
    return result


def build_record(
    *,
    request_data: dict[str, Any],
    source_format: str,
    output_data: bytes,
    input_truncated: bool,
    output_truncated: bool,
    request_model: str | None,
    response_model: str | None,
    started_at: datetime,
    outcome: str,
    upstream_protocol: str | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """在写入线程中聚合正文、提取提示词并确定采集完整性。"""
    issues: list[str] = []
    if input_truncated:
        issues.append("input_truncated")
    if output_truncated:
        issues.append("output_truncated")
    sanitized_request = _sanitize_media(request_data, issues)
    input_messages, prompts = _prepare_input(sanitized_request, source_format, issues)
    parsed = _parse_output(output_data, source_format, outcome)
    for issue in parsed["issues"]:
        _add_issue(issues, issue)
    output = _sanitize_media(parsed["output"], issues)

    if parsed["stream"] and outcome == "success" and not parsed["terminal"]:
        _add_issue(issues, "stream_terminal_missing")
    if input_truncated or output_truncated:
        capture_status = "truncated"
    elif parsed["parse_error"]:
        capture_status = "parse_error"
        _add_issue(issues, "response_parse_failed")
    elif outcome == "error" or parsed["failure"]:
        capture_status = "failed"
    elif outcome == "cancelled" or parsed["cancelled"]:
        capture_status = "interrupted"
    elif parsed["stream"] and not parsed["terminal"]:
        capture_status = "interrupted"
    elif issues:
        capture_status = "partial"
    else:
        capture_status = "complete"

    generation_params = {
        field: copy.deepcopy(sanitized_request[field]) for field in _GENERATION_FIELDS if field in sanitized_request
    }
    record: dict[str, Any] = {
        "schema_version": 1,
        "time": started_at.isoformat(),
        "request_model": request_model,
        "response_model": response_model,
        "upstream_protocol": upstream_protocol,
        "generation_params": generation_params,
        "input": input_messages,
        "output": output,
        "finish_reason": parsed["finish_reason"],
        "capture_status": capture_status,
    }
    if "tools" in sanitized_request:
        record["tools"] = copy.deepcopy(sanitized_request["tools"])
    if "tool_choice" in sanitized_request:
        record["tool_choice"] = copy.deepcopy(sanitized_request["tool_choice"])
    response_format = _response_format(sanitized_request)
    if response_format is not None:
        record["response_format"] = response_format
    if issues:
        record["capture_issues"] = issues
    return record, prompts


def _integer(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = ["build_record"]
