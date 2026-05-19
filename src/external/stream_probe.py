#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游流式响应探测工具。"""

from collections.abc import Callable, Iterator
from typing import Any


class StaticUpstreamResponse:
    """为非 requests 响应提供统一元数据与关闭能力。"""

    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        on_close: Callable[[], None] | None = None,
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


class PrefetchedStreamResponse:
    def __init__(self, response: Any, first_chunk: bytes):
        self._response = response
        self._first_chunk = first_chunk
        self.status_code = response.status_code
        self.headers = response.headers

    def iter_content(self, chunk_size: int | None = None) -> Iterator[bytes]:
        if self._first_chunk:
            yield self._first_chunk
            self._first_chunk = b""
        yield from self._response.iter_content(chunk_size=chunk_size)

    def close(self) -> None:
        self._response.close()


class BufferedUpstreamResponse:
    def __init__(self, response: Any, body: bytes):
        self._response = response
        self.content = body
        self.status_code = response.status_code
        self.headers = response.headers

    def close(self) -> None:
        self._response.close()


def looks_like_sse_chunk(chunk: bytes) -> bool:
    if not chunk:
        return False
    text = chunk.decode("utf-8", errors="ignore").lstrip()
    return text.startswith("data:") or text.startswith("event:") or text.startswith(":")


def probe_stream_response(upstream_response: Any) -> tuple[Any, bool]:
    chunk_iter = upstream_response.iter_content(chunk_size=None)
    first_chunk = b""
    for chunk in chunk_iter:
        if chunk:
            first_chunk = chunk
            break

    if not first_chunk:
        return BufferedUpstreamResponse(upstream_response, b""), False

    if looks_like_sse_chunk(first_chunk):
        return PrefetchedStreamResponse(upstream_response, first_chunk), True

    remaining = b"".join(chunk_iter)
    return BufferedUpstreamResponse(upstream_response, first_chunk + remaining), False
