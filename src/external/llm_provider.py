#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM provider 运行时对象。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..config.provider_config import (
    DEFAULT_PROVIDER_TARGET_FORMAT,
    resolve_provider_target_formats,
)
from ..hooks import HookContext, HookModule
from ..utils.compat import dataclass


@dataclass(frozen=True, slots=True)
class LLMProvider:
    """封装 provider 配置与请求阶段 hook 调用。"""

    name: str
    api: str
    transport: str = "http"
    source_format: str = "openai_chat"
    target_format: str = "openai_chat"
    target_formats: tuple[str, ...] = ()
    api_key: Optional[str] = None
    auth_group: Optional[str] = None
    model_list: tuple[str, ...] = ()
    proxy: Optional[str] = None
    timeout_seconds: int = 1200
    max_retries: int = 3
    verify_ssl: bool = False
    hook: Optional[HookModule] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_list", tuple(self.model_list))
        explicit_target_formats = tuple(self.target_formats)
        # DEPRECATED compatibility path for legacy callers that still
        # construct LLMProvider with a single `target_format` value.
        normalized_target_formats = resolve_provider_target_formats(
            explicit_target_formats or (self.target_format,)
        )
        normalized_target_format = str(self.target_format or "").strip().lower()
        if explicit_target_formats and normalized_target_format and normalized_target_format != DEFAULT_PROVIDER_TARGET_FORMAT:
            if normalized_target_format not in normalized_target_formats:
                raise ValueError(
                    "Provider target_format must also appear in target_formats when both are provided"
                )
            normalized_target_formats = (normalized_target_format,) + tuple(
                item for item in normalized_target_formats if item != normalized_target_format
            )
        object.__setattr__(self, "target_formats", normalized_target_formats)
        object.__setattr__(self, "target_format", normalized_target_formats[0])

    def supports_target_format(self, target_format: str) -> bool:
        normalized_target_format = str(target_format or "").strip().lower()
        return normalized_target_format in self.target_formats

    def apply_header_hook(self, ctx: HookContext, headers: Dict[str, str]) -> Dict[str, str]:
        if self.hook and hasattr(self.hook, "header_hook"):
            return self.hook.header_hook(ctx, headers)
        return headers

    def apply_request_guard(self, ctx: HookContext, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.hook:
            return body

        guard = getattr(self.hook, "request_guard", None)
        if callable(guard):
            guarded = guard(ctx, body)
            return body if guarded is None else guarded
        return body

    def apply_response_guard(self, ctx: HookContext, body: Any) -> Any:
        if not self.hook:
            return body

        guard = getattr(self.hook, "response_guard", None)
        if callable(guard):
            guarded = guard(ctx, body)
            return body if guarded is None else guarded
        return body
