#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 请求头处理辅助。"""

from __future__ import annotations

from typing import Any, Mapping


def normalize_http_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """规范化请求头映射，忽略空键名。"""
    if not isinstance(headers, Mapping):
        return {}

    normalized_headers: dict[str, str] = {}
    for raw_key, raw_value in headers.items():
        header_name = str(raw_key or "").strip()
        if not header_name:
            continue
        normalized_headers[header_name] = "" if raw_value is None else str(raw_value).strip()
    return normalized_headers


def merge_http_headers(
    base_headers: Mapping[str, Any] | None,
    extra_headers: Mapping[str, Any] | None,
) -> dict[str, str]:
    """按大小写不敏感规则合并请求头，后者覆盖前者。"""
    merged_headers = normalize_http_headers(base_headers)
    for header_name, header_value in normalize_http_headers(extra_headers).items():
        duplicated_keys = [
            existing_name
            for existing_name in merged_headers
            if existing_name.lower() == header_name.lower() and existing_name != header_name
        ]
        for duplicated_key in duplicated_keys:
            merged_headers.pop(duplicated_key, None)
        merged_headers[header_name] = header_value
    return merged_headers
