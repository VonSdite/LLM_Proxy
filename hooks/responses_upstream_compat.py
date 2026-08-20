from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import OrderedDict
from threading import Lock
from typing import Any, NamedTuple

from src.hooks import BaseHook, HookContext
from src.proxy_core import DownstreamChunk

_SUPPORTED_TOOL_TYPES = {"function", "mcp", "knowledge_search"}
_TERMINAL_EVENT_TYPES = {
    "error",
    "response.cancelled",
    "response.canceled",
    "response.completed",
    "response.failed",
    "response.incomplete",
}
_FUNCTION_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")
_MAX_FUNCTION_NAME_LENGTH = 64
_MAX_REQUEST_STATES = 1024


class _ToolIdentity(NamedTuple):
    name: str
    namespace: str
    tool_type: str


class _RequestState:
    def __init__(self) -> None:
        self.aliases: dict[str, _ToolIdentity] = {}
        self.identity_aliases: dict[_ToolIdentity, str] = {}
        self.item_identities: dict[str, _ToolIdentity] = {}
        self.used_names: set[str] = set()
        self.converted_count = 0
        self.dropped_count = 0


class Hook(BaseHook):
    """兼容 OpenAI Responses 上游的角色和旧工具方言。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._request_states: OrderedDict[int, _RequestState] = OrderedDict()

    def request_guard(self, ctx: HookContext, body: dict[str, Any]) -> dict[str, Any]:
        if not _is_responses_upstream(ctx):
            return body

        normalized_body = _normalize_developer_roles(body)
        if not _should_apply_legacy_tools(ctx):
            return normalized_body

        updated = copy.deepcopy(normalized_body)
        state = _RequestState()
        tool_sources = _extract_tool_sources(updated)
        _reserve_direct_tool_names(tool_sources, state)
        converted_tools = _convert_tool_sources(tool_sources, state)
        if converted_tools:
            updated["tools"] = converted_tools
        else:
            updated.pop("tools", None)

        _remove_additional_tools_input(updated)
        _convert_input_history(updated, state)
        allowed_names = _convert_tool_choice(updated, state)
        if allowed_names is not None:
            updated["tools"] = [tool for tool in updated.get("tools", []) if _tool_name(tool) in allowed_names]
            if not updated["tools"]:
                updated.pop("tools", None)

        if state.aliases:
            with self._lock:
                self._request_states[id(ctx)] = state
                self._request_states.move_to_end(id(ctx))
                while len(self._request_states) > _MAX_REQUEST_STATES:
                    self._request_states.popitem(last=False)

        if state.converted_count or state.dropped_count:
            ctx.logger.info(
                "Responses legacy tool compatibility applied: provider=%s converted=%s dropped=%s",
                ctx.provider_name,
                state.converted_count,
                state.dropped_count,
            )
        return updated

    def response_guard(self, ctx: HookContext, body: Any) -> Any:
        if not _should_apply_legacy_tools(ctx) or not isinstance(body, dict):
            return body

        with self._lock:
            state = self._request_states.get(id(ctx))
            if state is None:
                return body
            restored, event_type_changed = _restore_response_payload(body, state)
            payload_type = str(restored.get("type") or "").strip().lower()
            if not ctx.stream or payload_type in _TERMINAL_EVENT_TYPES:
                self._request_states.pop(id(ctx), None)

        if ctx.stream and event_type_changed:
            return DownstreamChunk(
                kind="json",
                payload=restored,
                event=str(restored.get("type") or "").strip() or None,
            )
        return restored


def _is_responses_upstream(ctx: HookContext) -> bool:
    source_format = str(ctx.provider_source_format or "").strip().lower()
    return source_format == "openai_responses"


def _normalize_developer_roles(body: dict[str, Any]) -> dict[str, Any]:
    """把 Responses 输入中的 developer 角色映射为 system。"""
    input_items = body.get("input")
    if not isinstance(input_items, list):
        return body

    normalized_items: list[Any] = []
    changed = False
    for item in input_items:
        if not isinstance(item, dict) or str(item.get("role") or "").strip().lower() != "developer":
            normalized_items.append(item)
            continue
        normalized_item = dict(item)
        normalized_item["role"] = "system"
        normalized_items.append(normalized_item)
        changed = True

    if not changed:
        return body

    updated = dict(body)
    updated["input"] = normalized_items
    return updated


def _should_apply_legacy_tools(ctx: HookContext) -> bool:
    source_format = str(ctx.provider_source_format or "").strip().lower()
    target_format = str(ctx.provider_target_format or "").strip().lower()
    return source_format == "openai_responses" and target_format == "openai_responses"


def _extract_tool_sources(body: dict[str, Any]) -> list[list[Any]]:
    sources: list[list[Any]] = []
    if isinstance(body.get("tools"), list):
        sources.append(body["tools"])
    if isinstance(body.get("input"), list):
        for item in body["input"]:
            if (
                isinstance(item, dict)
                and str(item.get("type") or "").strip().lower() == "additional_tools"
                and isinstance(item.get("tools"), list)
            ):
                sources.append(item["tools"])
    return sources


def _reserve_direct_tool_names(sources: list[list[Any]], state: _RequestState) -> None:
    for tools in sources:
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_type = str(tool.get("type") or "function").strip().lower()
            if tool_type in _SUPPORTED_TOOL_TYPES:
                name = _tool_name(tool)
                if name:
                    state.used_names.add(name)


def _convert_tool_sources(sources: list[list[Any]], state: _RequestState) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    emitted_names: set[str] = set()
    for tools in sources:
        for tool in tools:
            for converted_tool in _convert_tool(tool, state):
                name = _tool_name(converted_tool)
                dedupe_key = name or json.dumps(converted_tool, ensure_ascii=False, sort_keys=True)
                if dedupe_key in emitted_names:
                    continue
                emitted_names.add(dedupe_key)
                converted.append(converted_tool)
    return converted


def _convert_tool(tool: Any, state: _RequestState) -> list[dict[str, Any]]:
    if not isinstance(tool, dict):
        state.dropped_count += 1
        return []
    tool_type = str(tool.get("type") or "function").strip().lower()
    if tool_type == "namespace":
        namespace = str(tool.get("name") or "").strip()
        converted: list[dict[str, Any]] = []
        for child in tool.get("tools") or []:
            if not isinstance(child, dict):
                state.dropped_count += 1
                continue
            child_type = str(child.get("type") or "function").strip().lower()
            if child_type not in {"function", "custom"}:
                state.dropped_count += 1
                continue
            name = _tool_name(child)
            if not namespace or not name:
                state.dropped_count += 1
                continue
            identity = _ToolIdentity(name=name, namespace=namespace, tool_type=child_type)
            alias = _alias_for(identity, state)
            converted.append(_to_function_tool(child, alias, custom=child_type == "custom"))
            state.converted_count += 1
        return converted
    if tool_type == "custom":
        name = _tool_name(tool)
        if not name:
            state.dropped_count += 1
            return []
        identity = _ToolIdentity(name=name, namespace="", tool_type="custom")
        alias = _alias_for(identity, state)
        state.converted_count += 1
        return [_to_function_tool(tool, alias, custom=True)]
    if tool_type == "function":
        name = _tool_name(tool)
        if not name:
            state.dropped_count += 1
            return []
        return [_to_function_tool(tool, name, custom=False)]
    if tool_type in _SUPPORTED_TOOL_TYPES:
        return [copy.deepcopy(tool)]
    state.dropped_count += 1
    return []


def _to_function_tool(tool: dict[str, Any], name: str, *, custom: bool) -> dict[str, Any]:
    converted: dict[str, Any] = {
        "type": "function",
        "name": name,
        "description": _tool_description(tool),
        "parameters": (
            {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
                "additionalProperties": False,
            }
            if custom
            else copy.deepcopy(_tool_parameters(tool))
        ),
    }
    if not custom and tool.get("strict") is not None:
        converted["strict"] = bool(tool["strict"])
    return converted


def _remove_additional_tools_input(body: dict[str, Any]) -> None:
    if not isinstance(body.get("input"), list):
        return
    body["input"] = [
        item
        for item in body["input"]
        if not isinstance(item, dict) or str(item.get("type") or "").strip().lower() != "additional_tools"
    ]


def _convert_input_history(body: dict[str, Any], state: _RequestState) -> None:
    if not isinstance(body.get("input"), list):
        return
    for item in body["input"]:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type not in {"function_call", "custom_tool_call", "custom_tool_call_output"}:
            continue
        if item_type == "custom_tool_call_output":
            item["type"] = "function_call_output"
            state.converted_count += 1
            continue
        namespace = str(item.get("namespace") or "").strip()
        if item_type == "function_call" and not namespace:
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        identity = _ToolIdentity(
            name=name,
            namespace=namespace,
            tool_type="custom" if item_type == "custom_tool_call" else "function",
        )
        item["name"] = _alias_for(identity, state)
        item.pop("namespace", None)
        if item_type == "custom_tool_call":
            item["type"] = "function_call"
            item["arguments"] = json.dumps(
                {"input": str(item.pop("input", ""))},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        state.converted_count += 1


def _convert_tool_choice(body: dict[str, Any], state: _RequestState) -> set[str] | None:
    tool_choice = body.get("tool_choice")
    if not isinstance(tool_choice, dict):
        return None
    choice_type = str(tool_choice.get("type") or "").strip().lower()
    if choice_type == "allowed_tools":
        allowed_names: set[str] = set()
        for reference in tool_choice.get("tools") or []:
            converted = _convert_tool_reference(reference, state)
            if converted is not None and converted.get("name"):
                allowed_names.add(str(converted["name"]))
        mode = str(tool_choice.get("mode") or "auto").strip().lower()
        body["tool_choice"] = "required" if mode == "required" and allowed_names else "auto"
        state.converted_count += 1
        return allowed_names
    converted = _convert_tool_reference(tool_choice, state)
    if converted is None:
        body["tool_choice"] = "auto"
        state.converted_count += 1
    elif converted != tool_choice:
        body["tool_choice"] = converted
        state.converted_count += 1
    return None


def _convert_tool_reference(reference: Any, state: _RequestState) -> dict[str, Any] | None:
    if not isinstance(reference, dict):
        return None
    tool_type = str(reference.get("type") or "").strip().lower()
    if tool_type in {"function", "custom"}:
        namespace = str(reference.get("namespace") or "").strip()
        name = _tool_name(reference)
        if not name:
            return None
        if tool_type == "function" and not namespace:
            return {"type": "function", "name": name}
        identity = _ToolIdentity(name=name, namespace=namespace, tool_type=tool_type)
        return {"type": "function", "name": _alias_for(identity, state)}
    if tool_type in _SUPPORTED_TOOL_TYPES:
        return copy.deepcopy(reference)
    return None


def _alias_for(identity: _ToolIdentity, state: _RequestState) -> str:
    existing = state.identity_aliases.get(identity)
    if existing is not None:
        return existing

    qualified_name = f"{identity.namespace}__{identity.name}" if identity.namespace else identity.name
    sanitized = _FUNCTION_NAME_PATTERN.sub("_", qualified_name).strip("_") or "tool"
    alias = sanitized[:_MAX_FUNCTION_NAME_LENGTH]
    if alias in state.used_names:
        digest = hashlib.blake2s(
            f"{identity.namespace}\0{identity.name}\0{identity.tool_type}".encode("utf-8"),
            digest_size=5,
        ).hexdigest()
        suffix = f"__{digest}"
        alias = f"{sanitized[: _MAX_FUNCTION_NAME_LENGTH - len(suffix)]}{suffix}"
    state.used_names.add(alias)
    state.aliases[alias] = identity
    state.identity_aliases[identity] = alias
    return alias


def _restore_response_payload(body: dict[str, Any], state: _RequestState) -> tuple[dict[str, Any], bool]:
    restored = copy.deepcopy(body)
    event_type_changed = False

    item = restored.get("item")
    if isinstance(item, dict):
        _restore_tool_call_item(item, state)

    response = restored.get("response")
    if isinstance(response, dict):
        _restore_output_items(response.get("output"), state)
    _restore_output_items(restored.get("output"), state)

    event_type = str(restored.get("type") or "").strip().lower()
    if event_type == "response.function_call_arguments.done":
        item_id = str(restored.get("item_id") or "")
        identity = state.item_identities.get(item_id)
        if identity is not None and identity.tool_type == "custom":
            restored["type"] = "response.custom_tool_call_input.done"
            restored["input"] = _unwrap_custom_arguments(str(restored.pop("arguments", "")))
            event_type_changed = True
    return restored, event_type_changed


def _restore_output_items(value: Any, state: _RequestState) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, dict):
            _restore_tool_call_item(item, state)


def _restore_tool_call_item(item: dict[str, Any], state: _RequestState) -> None:
    if str(item.get("type") or "").strip().lower() != "function_call":
        return
    identity = state.aliases.get(str(item.get("name") or ""))
    if identity is None:
        return
    item_id = str(item.get("id") or "")
    if item_id:
        state.item_identities[item_id] = identity
    item["name"] = identity.name
    if identity.namespace:
        item["namespace"] = identity.namespace
    else:
        item.pop("namespace", None)
    if identity.tool_type == "custom":
        item["type"] = "custom_tool_call"
        item["input"] = _unwrap_custom_arguments(str(item.pop("arguments", "")))


def _unwrap_custom_arguments(arguments: str) -> str:
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError):
        return arguments
    if isinstance(parsed, dict) and isinstance(parsed.get("input"), str):
        return parsed["input"]
    return arguments


def _tool_name(tool: dict[str, Any]) -> str:
    name = str(tool.get("name") or "").strip()
    if name:
        return name
    nested = tool.get("function") or tool.get("custom")
    return str(nested.get("name") or "").strip() if isinstance(nested, dict) else ""


def _tool_description(tool: dict[str, Any]) -> str:
    if tool.get("description") is not None:
        return str(tool["description"])
    nested = tool.get("function") or tool.get("custom")
    return str(nested.get("description") or "") if isinstance(nested, dict) else ""


def _tool_parameters(tool: dict[str, Any]) -> dict[str, Any]:
    for key in ("parameters", "parametersJsonSchema", "input_schema"):
        if isinstance(tool.get(key), dict):
            return tool[key]
    function = tool.get("function")
    if isinstance(function, dict):
        for key in ("parameters", "parametersJsonSchema"):
            if isinstance(function.get(key), dict):
                return function[key]
    return {"type": "object", "properties": {}}
