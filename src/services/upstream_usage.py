#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游请求 usage 参数补齐工具。"""

from __future__ import annotations

from typing import Any, Dict


def ensure_upstream_usage_capture(
    source_format: str,
    translated_body: Dict[str, Any],
    stream: bool,
) -> None:
    """在协议支持时显式请求 usage 返回。"""
    if not stream:
        return

    normalized_source_format = str(source_format or "").strip().lower()
    if normalized_source_format != "openai_chat":
        return

    stream_options = translated_body.get("stream_options")
    if not isinstance(stream_options, dict):
        stream_options = {}
    else:
        stream_options = dict(stream_options)

    if stream_options.get("include_usage") is not True:
        stream_options["include_usage"] = True
    translated_body["stream_options"] = stream_options
