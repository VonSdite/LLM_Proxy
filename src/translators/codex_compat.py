"""Codex 请求工具兼容及响应标识还原。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from decimal import Decimal
from typing import Any

from ..proxy_core import DownstreamChunk, StreamEvent
from .codex_reasoning import decode_codex_reasoning

_SCHEMA_MAPS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
_SCHEMA_VALUES = {
    "items",
    "prefixItems",
    "additionalItems",
    "additionalProperties",
    "contains",
    "propertyNames",
    "unevaluatedItems",
    "unevaluatedProperties",
    "if",
    "then",
    "else",
    "not",
    "allOf",
    "anyOf",
    "oneOf",
}
_UNSUPPORTED_PATTERN = re.compile(r"(?<!\\)(?:\\\\)*\\(?:[pP]\{|0(?![0-9]))")


def _constant_key(value: Any) -> str:
    """JSON Schema 常量比较包含嵌套数值等价，且保留大整数精度。"""

    def canonical(item: Any) -> Any:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            number = Decimal(str(item))
            if number.is_zero():
                return ["number", "0"]
            if number.is_finite():
                sign, digits, exponent = number.as_tuple()
                digits_text = "".join(map(str, digits))
                trimmed = digits_text.rstrip("0")
                return ["number", sign, trimmed, exponent + len(digits_text) - len(trimmed)]
            return ["number", str(number)]
        if isinstance(item, dict):
            return ["object", [[key, canonical(val)] for key, val in sorted(item.items())]]
        if isinstance(item, list):
            return ["array", [canonical(val) for val in item]]
        return ["literal", item]

    return json.dumps(canonical(value), ensure_ascii=False, separators=(",", ":"))


def normalize_codex_schema(schema: Any) -> None:
    """只遍历 JSON Schema 节点，保留默认值、枚举值和说明中的业务数据。"""
    if isinstance(schema, list):
        for item in schema:
            normalize_codex_schema(item)
        return
    if not isinstance(schema, dict):
        return
    schema.pop("$schema", None)
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and _UNSUPPORTED_PATTERN.search(pattern):
        schema.pop("pattern")
    for key in _SCHEMA_MAPS:
        sub_map = schema.get(key)
        if isinstance(sub_map, dict):
            for name, item in list(sub_map.items()):
                if key == "patternProperties" and _UNSUPPORTED_PATTERN.search(name):
                    sub_map.pop(name)
                else:
                    normalize_codex_schema(item)
    for key in _SCHEMA_VALUES:
        if key in schema:
            normalize_codex_schema(schema[key])
    if "oneOf" in schema and "anyOf" in schema:
        return
    union_name = "oneOf" if "oneOf" in schema else "anyOf"
    branches = schema.get(union_name)
    if not isinstance(branches, list) or len(branches) < 8:
        return
    if any(
        not isinstance(branch, dict) or "const" not in branch or set(branch) - {"const", "title", "description"}
        for branch in branches
    ):
        return
    values = [branch["const"] for branch in branches]
    keys = [_constant_key(value) for value in values]
    if len(set(keys)) != len(keys):
        return
    existing_enum = schema.get("enum")
    if "enum" in schema and (
        not isinstance(existing_enum, list) or {_constant_key(value) for value in existing_enum} != set(keys)
    ):
        return
    schema.setdefault("enum", values)
    schema.pop(union_name)


def _identifier_map(values: list[str]) -> dict[str, str]:
    """短标识保持原值，长标识使用确定性摘要并避开已有短标识。"""
    unique = list(dict.fromkeys(values))
    occupied = {value for value in unique if len(value) <= 64}
    mapping: dict[str, str] = {}
    for value in unique:
        candidate = value
        attempt = 0
        while len(candidate) > 64 or (value != candidate and candidate in occupied):
            seed = value if attempt == 0 else f"{value}\0{attempt}"
            suffix = "_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
            candidate = value[: 64 - len(suffix)] + suffix
            attempt += 1
        mapping[value] = candidate
        occupied.add(candidate)
    return mapping


class CodexRequestCompatibility:
    """在请求内共享工具名、调用 ID 和输入项 ID 的双向映射。"""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.call_ids: dict[str, str] = {}
        self.item_ids: dict[str, str] = {}

    def prepare(self, body: dict[str, Any], target_format: str) -> None:
        """规范化转换后的请求，不接触原始下游消息。"""
        tools: list[dict[str, Any]] = []

        def collect_tools(items: Any) -> None:
            if not isinstance(items, list):
                return
            for tool in items:
                if isinstance(tool, dict):
                    tools.append(tool)
                    collect_tools(tool.get("tools"))

        collect_tools(body.get("tools"))
        input_items = body.get("input")
        input_items = input_items if isinstance(input_items, list) else []
        if target_format == "claude_chat":
            normalized_items: list[Any] = []
            for item in input_items:
                if isinstance(item, dict) and item.get("type") == "reasoning":
                    decoded = decode_codex_reasoning(item.get("encrypted_content"))
                    if decoded is not None:
                        normalized_items.append(decoded)
                else:
                    normalized_items.append(item)
            input_items = normalized_items
            system = body.pop("instructions", None)
            if system is not None:
                body["instructions"] = ""
            if isinstance(system, str) and system:
                input_items.insert(
                    0, {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": system}]}
                )
        names = [str(tool["name"]) for tool in tools if isinstance(tool.get("name"), str)]
        names.extend(
            str(item["name"]) for item in input_items if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
        names.extend(
            item["namespace"]
            for item in input_items
            if isinstance(item, dict) and isinstance(item.get("namespace"), str)
        )
        self.names = _identifier_map(names)
        self.call_ids = _identifier_map(
            [item["call_id"] for item in input_items if isinstance(item, dict) and isinstance(item.get("call_id"), str)]
        )
        for tool in tools:
            if "name" in tool:
                tool["name"] = self.names.get(tool["name"], tool["name"])
            for field in ("cache_control", "input_schema", "defer_loading"):
                tool.pop(field, None)
            if tool.get("type") == "function" and target_format in {"openai_chat", "claude_chat"}:
                tool.setdefault("strict", False)
            normalize_codex_schema(tool.get("parameters"))
        self._rewrite_choice(body.get("tool_choice"))
        prefixes = {
            "message": "msg",
            "reasoning": "rs",
            "function_call": "fc",
            "custom_tool_call": "ctc",
            "custom_tool_call_output": "ctco",
        }
        kept_items: list[Any] = []
        candidate_ids: dict[str, str] = {}
        for item in input_items:
            if not isinstance(item, dict):
                kept_items.append(item)
                continue
            item_id = item.get("id")
            if isinstance(item_id, str):
                if item.get("type") == "reasoning" and len(item_id) > 64 and item.get("encrypted_content"):
                    continue
                prefix = prefixes.get(item.get("type"))
                candidate_ids[item_id] = (
                    f"{prefix}_{item_id}" if prefix and item_id and not item_id.startswith(prefix) else item_id
                )
            if "name" in item:
                item["name"] = self.names.get(item["name"], item["name"])
            if "namespace" in item:
                item["namespace"] = self.names.get(item["namespace"], item["namespace"])
            if "call_id" in item:
                item["call_id"] = self.call_ids.get(item["call_id"], item["call_id"])
            kept_items.append(item)
        occupied = {
            candidate for original, candidate in candidate_ids.items() if original == candidate and len(candidate) <= 64
        }
        for original, candidate in candidate_ids.items():
            attempt = 0
            while len(candidate) > 64 or (original != candidate and candidate in occupied):
                suffix = "_" + hashlib.sha256(f"{original}\0{attempt}".encode("utf-8")).hexdigest()[:16]
                candidate = candidate_ids[original][:47] + suffix
                attempt += 1
            self.item_ids[original] = candidate
            occupied.add(candidate)
        for item in kept_items:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                item["id"] = self.item_ids.get(item["id"], item["id"])
        body["input"] = kept_items
        if target_format in {"openai_chat", "claude_chat"}:
            body.setdefault("reasoning", {"effort": "medium"})

    def _rewrite_choice(self, choice: Any) -> None:
        if not isinstance(choice, dict):
            return
        if isinstance(choice.get("name"), str):
            choice["name"] = self.names.get(choice["name"], choice["name"])
        for key, value in choice.items():
            if key == "tools" and isinstance(value, list):
                for item in value:
                    self._rewrite_choice(item)
            elif isinstance(value, dict):
                self._rewrite_choice(value)

    def restore_payload(self, payload: Any) -> Any:
        """还原响应结构中的标识，函数参数和工具结果作为业务数据保留。"""
        if not isinstance(payload, dict):
            return payload
        result = copy.deepcopy(payload)
        reverse_names = {value: key for key, value in self.names.items()}
        reverse_calls = {value: key for key, value in self.call_ids.items()}
        reverse_items = {value: key for key, value in self.item_ids.items()}

        def restore(item: Any) -> None:
            if not isinstance(item, dict):
                return
            for field, mapping in (
                ("name", reverse_names),
                ("namespace", reverse_names),
                ("call_id", reverse_calls),
                ("id", reverse_items),
                ("item_id", reverse_items),
            ):
                if isinstance(item.get(field), str):
                    item[field] = mapping.get(item[field], item[field])
            for field in ("item", "response"):
                restore(item.get(field))
            if isinstance(item.get("output"), list):
                for child in item["output"]:
                    restore(child)

        restore(result)
        return result


class CodexResponseAdapter:
    """在通用响应转换之前还原工具标识并记录成功的推理历史。"""

    def __init__(
        self, delegate: Any, compatibility: CodexRequestCompatibility, cache: Any, scope: str, body: dict[str, Any]
    ):
        self.delegate = delegate
        self.compatibility = compatibility
        self.cache = cache
        self.scope = scope
        self.body = body
        self.items: dict[int, dict[str, Any]] = {}
        self.done_indexes: set[int] = set()
        self.reasoning_emitted: set[str] = set()
        self.reasoning_text: dict[str, str] = {}

    def observe(self, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        result = copy.deepcopy(payload)
        if result.get("type") == "response.output_item.done" and isinstance(result.get("item"), dict):
            self.items[int(result.get("output_index", len(self.items)))] = result["item"]
        if result.get("type") in {"response.completed", "response.done", "response.incomplete"}:
            response = result.get("response")
            if isinstance(response, dict):
                if not response.get("output") and self.items:
                    response["output"] = [self.items[index] for index in sorted(self.items)]
                if result.get("type") != "response.incomplete" and response.get("status") not in {
                    "failed",
                    "cancelled",
                    "incomplete",
                }:
                    self.cache.record(self.scope, self.body, response.get("output") or [])
        return result

    def translate_nonstream_response(
        self, model_name: str, original_request: dict[str, Any], translated_request: dict[str, Any], payload: Any
    ) -> Any:
        payload = self.observe(payload)
        restored = self.compatibility.restore_payload(payload)
        result = self.delegate.translate_nonstream_response(model_name, original_request, translated_request, restored)
        if self.delegate.target_format == "openai_chat" and isinstance(result, dict):
            response = restored.get("response", restored) if isinstance(restored, dict) else {}
            details = [
                item
                for item in response.get("output", [])
                if isinstance(item, dict) and item.get("type") == "reasoning" and item.get("encrypted_content")
            ]
            if details:
                for choice in result.get("choices", []):
                    if isinstance(choice.get("message"), dict):
                        choice["message"]["reasoning_details"] = copy.deepcopy(details)
        return result

    def translate_stream_event(
        self,
        model_name: str,
        original_request: dict[str, Any],
        translated_request: dict[str, Any],
        event: StreamEvent,
        state: dict[str, Any],
    ) -> list[DownstreamChunk]:
        payload = self.observe(event.payload)
        restored = self.compatibility.restore_payload(payload)
        chunks: list[DownstreamChunk] = []
        if (
            isinstance(restored, dict)
            and restored.get("type") in {"response.completed", "response.done", "response.incomplete"}
            and self.delegate.target_format in {"openai_chat", "claude_chat"}
        ):
            response = restored.get("response") or {}
            if not state.get("codex_created"):
                created = StreamEvent(kind="json", payload={"type": "response.created", "response": response})
                chunks.extend(
                    self.delegate.translate_stream_event(
                        model_name, original_request, translated_request, created, state
                    )
                )
            for index, item in enumerate(response.get("output") or []):
                if not isinstance(item, dict):
                    continue
                fields = {"output_index": index, "item_id": item.get("id") or str(index)}
                if index not in self.done_indexes:
                    synthetic = StreamEvent(
                        kind="json", payload={"type": "response.output_item.done", "item": item, **fields}
                    )
                    chunks.extend(self._translate(model_name, original_request, translated_request, synthetic, state))
                if item.get("type") == "reasoning" and self.delegate.target_format == "openai_chat":
                    text = "".join(part.get("text", "") for part in item.get("summary") or [] if isinstance(part, dict))
                    current = self.reasoning_text.get(str(fields["item_id"]), "")
                    suffix = text[len(current) :] if text.startswith(current) else ""
                    if suffix:
                        synthetic = StreamEvent(
                            kind="json",
                            payload={"type": "response.reasoning_summary_text.delta", "delta": suffix, **fields},
                        )
                        chunks.extend(
                            self._translate(model_name, original_request, translated_request, synthetic, state)
                        )
                if item.get("type") == "message" and self.delegate.target_format == "openai_chat":
                    text = "".join(
                        part.get("text", "")
                        for part in item.get("content") or []
                        if isinstance(part, dict) and part.get("type") == "output_text"
                    )
                    synthetic = StreamEvent(
                        kind="json", payload={"type": "response.output_text.done", "text": text, **fields}
                    )
                    chunks.extend(self._translate(model_name, original_request, translated_request, synthetic, state))
        restored_event = StreamEvent(kind=event.kind, payload=restored, raw=event.raw, event=event.event)
        chunks.extend(self._translate(model_name, original_request, translated_request, restored_event, state))
        return chunks

    def _translate(
        self,
        model_name: str,
        original_request: dict[str, Any],
        translated_request: dict[str, Any],
        event: StreamEvent,
        state: dict[str, Any],
    ) -> list[DownstreamChunk]:
        restored = event.payload
        if isinstance(restored, dict):
            if restored.get("type") == "response.created":
                state["codex_created"] = True
            if restored.get("type") == "response.output_item.done":
                self.done_indexes.add(int(restored.get("output_index", len(self.done_indexes))))
            if restored.get("type") == "response.reasoning_summary_text.delta" and isinstance(
                restored.get("delta"), str
            ):
                key = str(restored.get("item_id") or restored.get("output_index") or "0")
                self.reasoning_text[key] = self.reasoning_text.get(key, "") + restored["delta"]
        chunks = self.delegate.translate_stream_event(model_name, original_request, translated_request, event, state)
        if self.delegate.target_format == "openai_chat" and isinstance(restored, dict):
            item = restored.get("item")
            if (
                restored.get("type") == "response.output_item.done"
                and isinstance(item, dict)
                and item.get("type") == "reasoning"
                and item.get("encrypted_content")
            ):
                encrypted = item["encrypted_content"]
                if encrypted not in self.reasoning_emitted:
                    self.reasoning_emitted.add(encrypted)
                    chunks.append(
                        DownstreamChunk(
                            kind="json",
                            payload={
                                "id": state.get("response_id", ""),
                                "created": state.get("created", 0),
                                "object": "chat.completion.chunk",
                                "model": state.get("response_model") or model_name,
                                "choices": [
                                    {"index": 0, "delta": {"reasoning_details": [item]}, "finish_reason": None}
                                ],
                            },
                        )
                    )
        return chunks
