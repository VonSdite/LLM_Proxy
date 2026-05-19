#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared proxy pipeline contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class NormalizedRequest:
    """Downstream request shape before provider-specific translation."""

    model: str
    body: dict[str, Any]
    headers: dict[str, str]
    stream: bool


@dataclass(frozen=True)
class StreamEvent:
    """A decoded upstream stream event."""

    kind: Literal["json", "text", "done"]
    payload: Any = None
    raw: str = ""
    event: str | None = None


@dataclass(frozen=True)
class DownstreamChunk:
    """A normalized downstream stream chunk before SSE encoding."""

    kind: Literal["json", "text", "done"]
    payload: Any = None
    event: str | None = None
