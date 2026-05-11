#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Downstream encoders for normalized proxy output."""

from __future__ import annotations

import json
from typing import Any

from .contracts import DownstreamChunk


def encode_downstream_chunk(chunk: DownstreamChunk, target_format: str) -> bytes:
    normalized_target = str(target_format or "").strip().lower()
    if normalized_target == "claude_chat":
        return _encode_claude_chunk(chunk)
    if normalized_target == "openai_responses":
        return _encode_openai_responses_chunk(chunk)
    return _encode_openai_chat_chunk(chunk)


def encode_openai_chunk(chunk: DownstreamChunk) -> bytes:
    return encode_downstream_chunk(chunk, "openai_chat")


def encode_downstream_response_body(payload: Any, target_format: str) -> bytes:
    del target_format
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def encode_openai_response_body(payload: Any) -> bytes:
    return encode_downstream_response_body(payload, "openai_chat")


def should_emit_terminal_chunk(target_format: str) -> bool:
    return str(target_format or "").strip().lower() == "openai_chat"


def is_terminal_chunk(chunk: DownstreamChunk, target_format: str) -> bool:
    normalized_target = str(target_format or "").strip().lower()
    if normalized_target == "claude_chat":
        if chunk.kind != "json" or not isinstance(chunk.payload, dict):
            return False
        payload_type = str(chunk.payload.get("type") or chunk.event or "").strip().lower()
        return payload_type in {"message_stop", "error"}
    if normalized_target == "openai_responses":
        if chunk.kind != "json" or not isinstance(chunk.payload, dict):
            return False
        payload_type = str(chunk.payload.get("type") or chunk.event or "").strip().lower()
        return payload_type in {"response.completed", "response.done", "response.failed", "response.cancelled"}
    return chunk.kind == "done"


def _encode_openai_chat_chunk(chunk: DownstreamChunk) -> bytes:
    if chunk.kind == "done":
        return b"data: [DONE]\n\n"

    if chunk.kind == "json":
        data = json.dumps(chunk.payload, ensure_ascii=False)
    elif isinstance(chunk.payload, bytes):
        data = chunk.payload.decode("utf-8", errors="ignore")
    else:
        data = str(chunk.payload or "")

    lines = []
    if chunk.event:
        lines.append(f"event: {chunk.event}")
    if chunk.kind == "text" and data.startswith(("data:", "event:", ":")):
        lines.append(data)
    else:
        lines.append(f"data: {data}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _encode_openai_responses_chunk(chunk: DownstreamChunk) -> bytes:
    if chunk.kind == "done":
        return b""

    if chunk.kind == "json":
        data = json.dumps(chunk.payload, ensure_ascii=False)
        event_name = chunk.event
        if not event_name and isinstance(chunk.payload, dict):
            event_name = str(chunk.payload.get("type") or "").strip() or None
    elif isinstance(chunk.payload, bytes):
        data = chunk.payload.decode("utf-8", errors="ignore")
        event_name = chunk.event
    else:
        data = str(chunk.payload or "")
        event_name = chunk.event

    lines = []
    if event_name:
        lines.append(f"event: {event_name}")
    if chunk.kind == "text" and data.startswith(("data:", "event:", ":")):
        lines.append(data)
    else:
        lines.append(f"data: {data}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _encode_claude_chunk(chunk: DownstreamChunk) -> bytes:
    if chunk.kind == "done":
        return b""

    if chunk.kind == "json":
        data = json.dumps(chunk.payload, ensure_ascii=False)
        event_name = chunk.event
        if not event_name and isinstance(chunk.payload, dict):
            event_name = str(chunk.payload.get("type") or "").strip() or None
    elif isinstance(chunk.payload, bytes):
        data = chunk.payload.decode("utf-8", errors="ignore")
        event_name = chunk.event
    else:
        data = str(chunk.payload or "")
        event_name = chunk.event

    lines = []
    if event_name:
        lines.append(f"event: {event_name}")
    lines.append(f"data: {data}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")
