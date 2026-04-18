#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 下游格式解析辅助。"""

from __future__ import annotations

from typing import Any


def resolve_runtime_target_formats(provider: Any) -> tuple[str, ...]:
    """兼容读取运行时对象上的下游格式集合。"""
    candidate_formats = getattr(provider, "target_formats", None)
    if candidate_formats:
        normalized_formats = tuple(
            str(item or "").strip().lower()
            for item in candidate_formats
            if str(item or "").strip()
        )
        if normalized_formats:
            return normalized_formats

    legacy_target_format = str(getattr(provider, "target_format", "") or "").strip().lower()
    if legacy_target_format:
        return (legacy_target_format,)
    return ()


def resolve_runtime_primary_target_format(
    provider: Any,
    *,
    preferred_target_format: str | None = None,
) -> str:
    """读取运行时对象上的首选下游格式。"""
    normalized_preferred = str(preferred_target_format or "").strip().lower()
    if normalized_preferred:
        return normalized_preferred

    provider_target_formats = resolve_runtime_target_formats(provider)
    if provider_target_formats:
        return provider_target_formats[0]
    return ""
