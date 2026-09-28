#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Translator 层共享的 Tool Result 规范化辅助。"""

from __future__ import annotations

import json
from typing import Any

# 字符串字段容纳不下的块类型：OpenAI 的 role:"tool" 消息内容只接受字符串。
BINARY_PART_TYPES = frozenset({"image", "document"})


def _part_type(part: Any) -> str:
    if not isinstance(part, dict):
        return ""
    return str(part.get("type") or "").strip().lower()


def _is_binary_part(part: Any) -> bool:
    return _part_type(part) in BINARY_PART_TYPES


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "")


def split_tool_result_content(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """把 tool result 拆成 (tool 消息文本, 需要提升到 user 消息的块)。

    图片和文档块作为第二个返回值交给调用方放进后续的 user 消息；content 不含
    这些块时，文本部分与 :func:`normalize_tool_result_content` 的输出一致。
    """
    if not isinstance(content, list):
        return _stringify(content), []

    binary_parts = [part for part in content if _is_binary_part(part)]
    if not binary_parts:
        return _stringify(content), []

    texts: list[str] = []
    for part in content:
        if _is_binary_part(part):
            continue
        if _part_type(part) == "text":
            texts.append(str(part.get("text") or ""))
        else:
            texts.append(_stringify(part))

    text = "\n".join(item for item in texts if item)
    if not text:
        text = f"[{len(binary_parts)} 个图片/文档块已移至后续消息]"
    return text, binary_parts


def normalize_tool_result_content(content: Any) -> str:
    """把 tool result 内容规整为下游可稳定消费的文本。

    图片和文档块以占位说明代替，调用方通过 :func:`split_tool_result_content`
    取到这些块，再放进后续的 user 消息。
    """
    if isinstance(content, list):
        binary_parts = [part for part in content if _is_binary_part(part)]
        if binary_parts:
            kept = [part for part in content if not _is_binary_part(part)]
            text = _stringify(kept) if kept else ""
            note = f"[{len(binary_parts)} 个图片/文档块未内联]"
            return f"{text}\n{note}" if text else note
    return _stringify(content)
