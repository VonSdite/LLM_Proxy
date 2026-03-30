#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stream decoders for multiple upstream formats."""

from __future__ import annotations

import codecs
import json
import re
from typing import Iterable, Iterator, Optional

from .contracts import StreamEvent

KNOWN_STREAM_FORMATS = {
    "auto",
    "sse_json",
    "sse_text",
    "ndjson",
    "raw_text",
    "ws_json",
    "ws_text",
    "nonstream",
}

_SSE_SEPARATOR = re.compile(r"\r?\n\r?\n")


def resolve_stream_format(
    preferred_format: Optional[str],
    content_type: str,
    transport: str,
) -> str:
    normalized = str(preferred_format or "").strip().lower() or "auto"
    if normalized not in KNOWN_STREAM_FORMATS:
        raise ValueError(f"Unsupported stream format: {preferred_format}")
    if normalized != "auto":
        return normalized

    lowered_content_type = (content_type or "").lower()
    if transport == "websocket":
        return "ws_json"
    if "text/event-stream" in lowered_content_type:
        return "sse_json"
    if "x-ndjson" in lowered_content_type or "ndjson" in lowered_content_type or "jsonl" in lowered_content_type:
        return "ndjson"
    if lowered_content_type.startswith("text/"):
        return "raw_text"
    return "nonstream"


def decode_stream_events(chunks: Iterable[bytes], stream_format: str) -> Iterator[StreamEvent]:
    normalized = str(stream_format).strip().lower()
    if normalized in {"sse_json", "sse_text"}:
        yield from _decode_sse_events(chunks, parse_json=(normalized == "sse_json"))
        return
    if normalized == "ndjson":
        yield from _decode_ndjson_events(chunks)
        return
    if normalized in {"raw_text", "ws_text"}:
        yield from _decode_raw_text_events(chunks)
        return
    if normalized == "ws_json":
        yield from _decode_websocket_json_events(chunks)
        return
    raise ValueError(f"Unsupported stream decoder format: {stream_format}")


def _decode_sse_events(chunks: Iterable[bytes], *, parse_json: bool) -> Iterator[StreamEvent]:
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    for chunk in chunks:
        if not chunk:
            continue
        buffer += decoder.decode(chunk)
        buffer, events = _split_sse_buffer(buffer)
        for event_text in events:
            yield from _parse_sse_event(event_text, parse_json=parse_json)

    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        yield from _parse_sse_event(buffer, parse_json=parse_json)


def _split_sse_buffer(buffer: str) -> tuple[str, list[str]]:
    events: list[str] = []
    while True:
        match = _SSE_SEPARATOR.search(buffer)
        if not match:
            break
        events.append(buffer[: match.start()])
        buffer = buffer[match.end() :]
    return buffer, events


def _parse_sse_event(event_text: str, *, parse_json: bool) -> Iterator[StreamEvent]:
    normalized = event_text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return

    event_name: Optional[str] = None
    data_lines: list[str] = []
    passthrough_lines: list[str] = []
    for raw_line in normalized.split("\n"):
        if raw_line.startswith("event:"):
            event_name = raw_line[6:].strip() or None
            passthrough_lines.append(raw_line)
            continue
        if raw_line.startswith("data:"):
            data_lines.append(raw_line[5:].strip())
            continue
        if raw_line != "":
            passthrough_lines.append(raw_line)

    if not data_lines:
        yield StreamEvent(kind="text", payload=normalized, raw=normalized, event=event_name)
        return

    data_text = "\n".join(data_lines).strip()
    if not data_text:
        return
    if data_text == "[DONE]":
        yield StreamEvent(kind="done", payload="[DONE]", raw=data_text, event=event_name)
        return

    if parse_json:
        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError:
            yield StreamEvent(kind="text", payload=data_text, raw=normalized, event=event_name)
            return
        yield StreamEvent(kind="json", payload=payload, raw=data_text, event=event_name)
        return

    yield StreamEvent(kind="text", payload=data_text, raw=normalized, event=event_name)


def _decode_ndjson_events(chunks: Iterable[bytes]) -> Iterator[StreamEvent]:
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    for chunk in chunks:
        if not chunk:
            continue
        buffer += decoder.decode(chunk)
        while True:
            newline_index = buffer.find("\n")
            if newline_index == -1:
                break
            line = buffer[:newline_index].strip()
            buffer = buffer[newline_index + 1 :]
            if not line:
                continue
            yield from _parse_json_line(line)

    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        yield from _parse_json_line(buffer.strip())


def _parse_json_line(line: str) -> Iterator[StreamEvent]:
    if line == "[DONE]":
        yield StreamEvent(kind="done", payload="[DONE]", raw=line)
        return
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        yield StreamEvent(kind="text", payload=line, raw=line)
        return
    yield StreamEvent(kind="json", payload=payload, raw=line)


def _decode_raw_text_events(chunks: Iterable[bytes]) -> Iterator[StreamEvent]:
    decoder = codecs.getincrementaldecoder("utf-8")()
    for chunk in chunks:
        if not chunk:
            continue
        text = decoder.decode(chunk)
        if text:
            yield StreamEvent(kind="text", payload=text, raw=text)

    tail = decoder.decode(b"", final=True)
    if tail:
        yield StreamEvent(kind="text", payload=tail, raw=tail)


def _decode_websocket_json_events(chunks: Iterable[bytes]) -> Iterator[StreamEvent]:
    for chunk in chunks:
        if not chunk:
            continue
        text = chunk.decode("utf-8", errors="ignore").strip()
        if not text:
            continue
        if text == "[DONE]":
            yield StreamEvent(kind="done", payload="[DONE]", raw=text)
            continue
        if text.startswith("data:") or text.startswith("event:") or text.startswith(":"):
            yield from _parse_sse_event(text, parse_json=True)
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            yield StreamEvent(kind="text", payload=text, raw=text)
            continue
        yield StreamEvent(kind="json", payload=payload, raw=text)
