#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared proxy pipeline contracts."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..utils.compat import Literal, dataclass


@dataclass(frozen=True, slots=True)
class NormalizedRequest:
    """Downstream request shape before provider-specific translation."""

    model: str
    body: Dict[str, Any]
    headers: Dict[str, str]
    stream: bool


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """A decoded upstream stream event."""

    kind: Literal["json", "text", "done"]
    payload: Any = None
    raw: str = ""
    event: Optional[str] = None


@dataclass(frozen=True, slots=True)
class DownstreamChunk:
    """A normalized downstream stream chunk before SSE encoding."""

    kind: Literal["json", "text", "done"]
    payload: Any = None
    event: Optional[str] = None
