#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Translator 层共享的事件 Chunk 构造辅助。"""

from __future__ import annotations

from typing import Any

from ..proxy_core.contracts import DownstreamChunk


def build_json_event_chunk(event_name: str, payload: dict[str, Any]) -> DownstreamChunk:
    """构造带事件名的 JSON Stream Chunk。"""
    return DownstreamChunk(kind="json", payload=payload, event=event_name)
