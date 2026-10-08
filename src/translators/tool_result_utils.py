#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Translator 层共享的 Tool Result 规范化辅助。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

_MEDIA_TYPES = {"image", "document", "image_url", "input_image", "file", "input_file"}
TOOL_RESULT_MEDIA_PLACEHOLDER = "[Tool returned media content; see the following user message.]"


class UnsupportedToolResultContent(ValueError):
    """表示目标协议无法承载工具返回的媒体来源。"""


def normalize_tool_result_content(content: Any) -> Any:
    """把 tool result 内容规整为下游可稳定消费的文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False)
    return str(content or "")


def convert_tool_result_content(
    content: Any,
    convert_part: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    text_type: str,
    target_format: str,
) -> str | list[dict[str, Any]]:
    """按目标协议转换工具内容，保留未知文本结构并显式报告不可承载的媒体。"""
    if isinstance(content, str):
        try:
            structured = json.loads(content)
        except ValueError:
            return content
        parts = structured if isinstance(structured, list) else [structured]
        if not any(
            isinstance(part, dict) and str(part.get("type") or "").strip().lower() in _MEDIA_TYPES for part in parts
        ):
            return content
        content = structured
    if not isinstance(content, (dict, list)):
        return normalize_tool_result_content(content)

    parts = content if isinstance(content, list) else [content]
    converted_parts: list[dict[str, Any]] = []
    for part in parts:
        converted = convert_part(part) if isinstance(part, dict) else None
        if converted is None:
            if isinstance(part, dict) and str(part.get("type") or "").strip().lower() in _MEDIA_TYPES:
                raise UnsupportedToolResultContent(
                    f"Cannot convert tool result '{part.get('type')}' to {target_format}: "
                    "unsupported or missing media source"
                )
            converted = {"type": text_type, "text": normalize_tool_result_content(part)}
        converted_parts.append(converted)
    return converted_parts or ""


def split_tool_result_content(
    content: Any,
    convert_part: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> tuple[str, list[dict[str, Any]]]:
    """把 Chat 工具输出的文本与可补传到 user 消息的媒体分开。"""
    converted = convert_tool_result_content(content, convert_part, text_type="text", target_format="openai_chat")
    if isinstance(converted, str):
        return converted, []
    texts = [str(part.get("text") or "") for part in converted if part.get("type") == "text"]
    media = [part for part in converted if part.get("type") != "text"]
    text = "\n\n".join(texts)
    return text or (TOOL_RESULT_MEDIA_PLACEHOLDER if media else ""), media


def tool_result_media_relay(call_id: str, media: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为补传媒体标注其所属的工具调用。"""
    if not media:
        return []
    notice = f"Media returned by tool call {call_id}:" if call_id else "Media returned by the preceding tool call:"
    return [{"type": "text", "text": notice}, *media]
