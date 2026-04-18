#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Translator 层共享的 Tool Result 规范化辅助。"""

from __future__ import annotations

import json
from typing import Any


def normalize_tool_result_content(content: Any) -> Any:
    """把 tool result 内容规整为下游可稳定消费的文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False)
    return str(content or "")
