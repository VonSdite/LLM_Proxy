#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Executor contracts for upstream provider access."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..external import LLMProvider
from ..utils.compat import Protocol, dataclass


@dataclass(frozen=True, slots=True)
class OpenedUpstreamResponse:
    """Opened upstream response with normalized runtime metadata."""

    response: Any
    status_code: int
    content_type: str
    is_stream: bool
    stream_format: str


class Executor(Protocol):
    """Transport-specific executor that opens an upstream response."""

    transport: str

    def execute(
        self,
        provider: LLMProvider,
        headers: Dict[str, str],
        body: Dict[str, Any],
        requested_stream: bool,
        timeout_seconds: int,
        verify_ssl: bool,
        request_proxies: Optional[Dict[str, str]],
    ) -> OpenedUpstreamResponse:
        ...
