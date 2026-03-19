#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WebSocket 上游响应桥接。"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterator, Optional

from websocket import ABNF


class StaticUpstreamResponse:
    """为非 requests 响应提供统一元数据与关闭能力。"""

    def __init__(
        self,
        status_code: int = 200,
        headers: Optional[Dict[str, str]] = None,
        on_close: Optional[Callable[[], None]] = None,
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self._on_close = on_close

    def close(self) -> None:
        if self._on_close is None:
            return
        on_close = self._on_close
        self._on_close = None
        on_close()


class WebSocketUpstreamResponse:
    """把 websocket 消息流包装成 SSE 风格字节流。"""

    def __init__(
        self,
        connection: Any,
        *,
        status_code: int = 200,
        headers: Optional[Dict[str, str]] = None,
    ):
        self._connection = connection
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/event-stream"}

    def iter_content(self, chunk_size: Optional[int] = None) -> Iterator[bytes]:
        del chunk_size
        while True:
            opcode, payload = self._connection.recv_data(control_frame=True)
            if opcode == ABNF.OPCODE_CLOSE:
                return
            if opcode == ABNF.OPCODE_PING:
                self._connection.pong(_coerce_bytes(payload))
                continue
            if opcode not in {ABNF.OPCODE_TEXT, ABNF.OPCODE_BINARY}:
                continue

            normalized = normalize_websocket_message(payload)
            if normalized is not None:
                yield normalized

    def close(self) -> None:
        self._connection.close()


def collect_websocket_response_body(connection: Any, logger: Any = None) -> bytes:
    """读取非流式 websocket 响应体。"""
    payloads: list[bytes] = []

    while True:
        opcode, payload = connection.recv_data(control_frame=True)
        if opcode == ABNF.OPCODE_CLOSE:
            break
        if opcode == ABNF.OPCODE_PING:
            connection.pong(_coerce_bytes(payload))
            continue
        if opcode not in {ABNF.OPCODE_TEXT, ABNF.OPCODE_BINARY}:
            continue

        normalized = extract_websocket_payload(payload)
        if normalized is None:
            continue

        payloads.append(normalized)
        if is_terminal_websocket_payload(normalized):
            break

    if not payloads:
        return b""

    if len(payloads) > 1 and logger is not None:
        logger.warning(
            "WebSocket upstream returned %s messages for non-stream request, using the last payload",
            len(payloads),
        )
    return payloads[-1]


def normalize_websocket_message(payload: Any) -> Optional[bytes]:
    """把单个 websocket 消息归一化成 SSE 事件。"""
    text = _coerce_text(payload)
    if text is None:
        return None

    stripped = text.strip()
    if not stripped:
        return None

    if stripped.startswith(("data:", "event:", ":")):
        if stripped.endswith("\n\n"):
            return stripped.encode("utf-8")
        return f"{stripped}\n\n".encode("utf-8")

    return f"data: {stripped}\n\n".encode("utf-8")


def extract_websocket_payload(payload: Any) -> Optional[bytes]:
    """从 websocket 消息中提取最终 JSON/文本载荷。"""
    text = _coerce_text(payload)
    if text is None:
        return None

    stripped = text.strip()
    if not stripped:
        return None

    sse_payload = _extract_last_sse_data(stripped)
    if sse_payload is not None:
        return sse_payload

    if stripped == "[DONE]":
        return None
    return stripped.encode("utf-8")


def is_terminal_websocket_payload(payload: bytes) -> bool:
    """判断 websocket 载荷是否已到终止帧。"""
    text = payload.decode("utf-8", errors="ignore").strip()
    if not text:
        return False
    if text == "[DONE]":
        return True

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False

    if not isinstance(data, dict):
        return False
    if data.get("done") is True:
        return True

    event_type = str(data.get("type") or "").strip().lower()
    if event_type in {"response.completed", "response.done"}:
        return True

    usage = data.get("usage")
    choices = data.get("choices")
    if isinstance(usage, dict) and (choices is None or choices == []):
        return True

    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            finish_reason = choice.get("finish_reason")
            if finish_reason not in (None, ""):
                return True
    return False


def _extract_last_sse_data(text: str) -> Optional[bytes]:
    data_lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw_line.startswith("data:"):
            continue
        data = raw_line[5:].strip()
        if not data or data == "[DONE]":
            continue
        data_lines.append(data)

    if not data_lines:
        return None
    return data_lines[-1].encode("utf-8")


def _coerce_text(payload: Any) -> Optional[str]:
    if payload is None:
        return None
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="ignore")
    return str(payload)


def _coerce_bytes(payload: Any) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, bytes):
        return payload
    return str(payload).encode("utf-8")
