#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一规范化上游 usage，并提供协议之间的 usage 映射。"""

from __future__ import annotations

import copy
from typing import Any

USAGE_STATUS_UNKNOWN = "unknown"
USAGE_STATUS_PARTIAL = "partial"
USAGE_STATUS_KNOWN = "known"


def safe_int(value: Any) -> int:
    """把上游数值安全转换为非负整数。"""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def create_usage_meta() -> dict[str, Any]:
    """创建可增量合并的 usage 元数据。"""
    return {
        "response_model": None,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "usage_status": USAGE_STATUS_UNKNOWN,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 0,
        "_usage_fields": set(),
        "_total_explicit": False,
        "_usage_seen": False,
    }


def extract_canonical_usage(payload: Any, source_format: str | None = None) -> dict[str, Any] | None:
    """从原始协议 payload 提取统一的输入、输出和缓存 token。"""
    usage, usage_metadata = _find_usage(payload)
    if usage is None and usage_metadata is None:
        return None

    normalized_source = str(source_format or "").strip().lower()
    if usage_metadata is not None:
        prompt = _optional_int(usage_metadata.get("promptTokenCount"))
        completion = _optional_int(usage_metadata.get("candidatesTokenCount"))
        total = _optional_int(usage_metadata.get("totalTokenCount"))
        fields = {
            key
            for key, value in (
                ("prompt_tokens", prompt),
                ("completion_tokens", completion),
            )
            if value is not None
        }
        return _build_usage(prompt, completion, total, fields=fields)

    assert usage is not None
    if normalized_source == "claude_chat" and _contains_claude_usage_fields(usage):
        input_tokens = _optional_int(usage.get("input_tokens"))
        cache_read = _optional_int(usage.get("cache_read_input_tokens")) or 0
        cache_creation = _optional_int(usage.get("cache_creation_input_tokens")) or 0
        output_tokens = _optional_int(usage.get("output_tokens"))
        if isinstance(payload, dict) and payload.get("type") == "message_start" and output_tokens == 0:
            output_tokens = None
        prompt = None
        if input_tokens is not None or cache_read or cache_creation:
            prompt = (input_tokens or 0) + cache_read + cache_creation
        total = _optional_int(usage.get("total_tokens"))
        reasoning = _optional_int(usage.get("thinking_tokens"))
        fields = {
            key
            for key, value in (
                ("prompt_tokens", prompt),
                ("completion_tokens", output_tokens),
            )
            if value is not None
        }
        return _build_usage(
            prompt,
            output_tokens,
            total,
            fields=fields,
            cache_read=cache_read,
            cache_creation=cache_creation,
            reasoning=reasoning or 0,
            usage_seen=True,
        )

    prompt = _first_optional_int(usage, "prompt_tokens", "input_tokens")
    completion = _first_optional_int(usage, "completion_tokens", "output_tokens")
    total = _optional_int(usage.get("total_tokens"))
    details = usage.get("prompt_tokens_details")
    cached = _optional_int(details.get("cached_tokens")) if isinstance(details, dict) else None
    cache_creation = _first_optional_int(
        usage,
        "cache_creation_input_tokens",
        "cache_creation_tokens",
    )
    if cache_creation is None and isinstance(details, dict):
        cache_creation = _first_optional_int(details, "cache_creation_tokens", "cache_creation_input_tokens")
    completion_details = usage.get("completion_tokens_details")
    reasoning = (
        _optional_int(completion_details.get("reasoning_tokens")) if isinstance(completion_details, dict) else None
    )
    if reasoning is None:
        reasoning = _optional_int(usage.get("thinking_tokens"))
    fields = {
        key
        for key, value in (
            ("prompt_tokens", prompt),
            ("completion_tokens", completion),
        )
        if value is not None
    }
    return _build_usage(
        prompt,
        completion,
        total,
        fields=fields,
        cache_read=cached or 0,
        cache_creation=cache_creation or 0,
        reasoning=reasoning or 0,
        usage_seen=True,
    )


def merge_usage_meta(meta: dict[str, Any], payload: Any, source_format: str | None = None) -> None:
    """将一个响应片段的 usage 合并到请求级元数据。"""
    incoming = extract_canonical_usage(payload, source_format)
    if incoming is None:
        return

    meta["_usage_seen"] = True
    fields = meta.setdefault("_usage_fields", set())
    if not isinstance(fields, set):
        fields = set(fields or ())
        meta["_usage_fields"] = fields

    for field in (
        "prompt_tokens",
        "completion_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "reasoning_tokens",
    ):
        if field not in incoming:
            continue
        value = safe_int(incoming[field])
        if field not in fields or (safe_int(meta.get(field)) == 0 and value > 0):
            meta[field] = value
        fields.add(field)

    if incoming.get("total_tokens") is not None:
        value = safe_int(incoming["total_tokens"])
        if not meta.get("_total_explicit") or (safe_int(meta.get("total_tokens")) == 0 and value > 0):
            meta["total_tokens"] = value
        meta["_total_explicit"] = bool(incoming.get("_total_explicit")) or meta.get("_total_explicit", False)

    if "prompt_tokens" in fields or "completion_tokens" in fields:
        if "prompt_tokens" in fields and "completion_tokens" in fields:
            meta["usage_status"] = USAGE_STATUS_KNOWN
        else:
            meta["usage_status"] = USAGE_STATUS_PARTIAL
        if not meta.get("_total_explicit"):
            meta["total_tokens"] = safe_int(meta.get("prompt_tokens")) + safe_int(meta.get("completion_tokens"))


def public_usage_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """移除合并内部状态，返回可用于日志和额度统计的 usage。"""
    return {key: copy.deepcopy(value) for key, value in meta.items() if not key.startswith("_")}


def openai_usage_to_claude(usage: Any) -> dict[str, Any]:
    """把 OpenAI usage 映射到 Claude Messages usage。"""
    canonical = extract_canonical_usage(usage, "openai_chat") or {}
    prompt = safe_int(canonical.get("prompt_tokens"))
    cached = safe_int(canonical.get("cache_read_input_tokens"))
    cache_creation = safe_int(canonical.get("cache_creation_input_tokens"))
    result: dict[str, Any] = {
        "input_tokens": max(prompt - cached - cache_creation, 0),
        "output_tokens": safe_int(canonical.get("completion_tokens")),
    }
    if cached > 0:
        result["cache_read_input_tokens"] = cached
    if cache_creation > 0:
        result["cache_creation_input_tokens"] = cache_creation
    reasoning = safe_int(canonical.get("reasoning_tokens"))
    if reasoning > 0:
        result["thinking_tokens"] = reasoning
    return result


def claude_usage_to_openai(usage: Any) -> dict[str, Any]:
    """把 Claude Messages usage 映射到 OpenAI Chat usage。"""
    canonical = extract_canonical_usage(usage, "claude_chat") or {}
    prompt = safe_int(canonical.get("prompt_tokens"))
    completion = safe_int(canonical.get("completion_tokens"))
    result: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    cached = safe_int(canonical.get("cache_read_input_tokens"))
    cache_creation = safe_int(canonical.get("cache_creation_input_tokens"))
    if cached or cache_creation:
        details: dict[str, Any] = {}
        if cached:
            details["cached_tokens"] = cached
        if cache_creation:
            details["cache_creation_tokens"] = cache_creation
        result["prompt_tokens_details"] = details
    reasoning = safe_int(canonical.get("reasoning_tokens"))
    if reasoning:
        result["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return result


def _find_usage(payload: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return None, None
    if any(
        key in payload
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    ):
        return payload, None
    if isinstance(payload.get("usageMetadata"), dict):
        return None, payload["usageMetadata"]
    if isinstance(payload.get("usage"), dict):
        return payload["usage"], None
    for key in ("message", "response"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            if isinstance(nested.get("usageMetadata"), dict):
                return None, nested["usageMetadata"]
            if isinstance(nested.get("usage"), dict):
                return nested["usage"], None
    return None, None


def _contains_claude_usage_fields(usage: dict[str, Any]) -> bool:
    return any(
        key in usage
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "thinking_tokens",
        )
    )


def _build_usage(
    prompt: int | None,
    completion: int | None,
    total: int | None,
    *,
    fields: set[str],
    cache_read: int = 0,
    cache_creation: int = 0,
    reasoning: int = 0,
    usage_seen: bool = True,
) -> dict[str, Any]:
    result: dict[str, Any] = {"_usage_seen": usage_seen, "_fields": fields}
    if prompt is not None:
        result["prompt_tokens"] = max(prompt, 0)
    if completion is not None:
        result["completion_tokens"] = max(completion, 0)
    if total is not None and (total > 0 or (prompt or 0) == 0 and (completion or 0) == 0):
        result["total_tokens"] = max(total, 0)
        result["_total_explicit"] = True
    elif prompt is not None or completion is not None:
        result["total_tokens"] = max(prompt or 0, 0) + max(completion or 0, 0)
        result["_total_explicit"] = False
    if cache_read:
        result["cache_read_input_tokens"] = cache_read
    if cache_creation:
        result["cache_creation_input_tokens"] = cache_creation
    if reasoning:
        result["reasoning_tokens"] = reasoning
    return result


def _first_optional_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _optional_int(payload.get(key))
        if value is not None:
            return value
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None
