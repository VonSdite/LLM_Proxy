#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core proxy pipeline contracts and helpers."""

from .contracts import DownstreamChunk, NormalizedRequest, StreamEvent
from .decoders import decode_stream_events, resolve_stream_format
from .encoder import (
    encode_downstream_chunk,
    encode_downstream_response_body,
    encode_openai_chunk,
    encode_openai_response_body,
    is_terminal_chunk,
    should_emit_terminal_chunk,
)

__all__ = [
    "DownstreamChunk",
    "NormalizedRequest",
    "StreamEvent",
    "decode_stream_events",
    "encode_downstream_chunk",
    "encode_downstream_response_body",
    "encode_openai_chunk",
    "encode_openai_response_body",
    "is_terminal_chunk",
    "resolve_stream_format",
    "should_emit_terminal_chunk",
]
