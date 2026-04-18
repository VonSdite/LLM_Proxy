#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM provider 运行时对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, cast

from ..config.provider_config import resolve_provider_target_formats
from ..hooks import HookContext, HookModule


@dataclass(frozen=True)
class LLMProvider:
    """封装 provider 配置与请求阶段 hook 调用。"""

    name: str
    api: str
    transport: str = "http"
    source_format: str = "openai_chat"
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
        normalized_target_formats = resolve_provider_target_formats(self.target_formats)
        object.__setattr__(self, "target_formats", normalized_target_formats)

    @property
    def primary_target_format(self) -> str:
        return self.target_formats[0]

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

        guard = cast(
            Optional[Callable[[HookContext, Dict[str, Any]], Optional[Dict[str, Any]]]],
            getattr(self.hook, "request_guard", None),
        )
        if callable(guard):
            guarded = guard(ctx, body)
            return body if guarded is None else guarded
        return body

    def apply_response_guard(self, ctx: HookContext, body: Any) -> Any:
        if not self.hook:
            return body

        guard = cast(Optional[Callable[[HookContext, Any], Any]], getattr(self.hook, "response_guard", None))
        if callable(guard):
            guarded = guard(ctx, body)
            return body if guarded is None else guarded
        return body
