#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Executor contracts for upstream provider access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..external import LLMProvider


@dataclass(frozen=True)
class OpenedUpstreamResponse:
    """Opened upstream response with normalized runtime metadata."""

    response: Any
    status_code: int
    content_type: str
    is_stream: bool
    stream_format: str


class Executor(Protocol):
    """Transport-specific executor that opens an upstream response."""

    @property
    def transport(self) -> str: ...

    def execute(
        self,
        provider: LLMProvider,
        headers: dict[str, str],
        body: dict[str, Any],
        requested_stream: bool,
        timeout_seconds: int,
        verify_ssl: bool,
        request_proxies: dict[str, str] | None,
    ) -> OpenedUpstreamResponse: ...
