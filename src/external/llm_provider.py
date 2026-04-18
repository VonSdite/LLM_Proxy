#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM provider 运行时对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, TypeVar, cast

from ..config.provider_config import resolve_provider_target_formats
from ..hooks import HookContext, HookModule

T = TypeVar("T")


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
        return self._call_optional_hook(
            "header_hook",
            ctx,
            headers,
            default_value=headers,
        )

    def apply_request_guard(self, ctx: HookContext, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._call_optional_hook(
            "request_guard",
            ctx,
            body,
            default_value=body,
        )

    def apply_response_guard(self, ctx: HookContext, body: Any) -> Any:
        return self._call_optional_hook(
            "response_guard",
            ctx,
            body,
            default_value=body,
        )

    def _call_optional_hook(
        self,
        method_name: str,
        ctx: HookContext,
        payload: T,
        *,
        default_value: T,
    ) -> T:
        if not self.hook:
            return default_value

        hook_method = cast(
            Optional[Callable[[HookContext, T], Any]],
            getattr(self.hook, method_name, None),
        )
        if not callable(hook_method):
            return default_value

        guarded_payload = hook_method(ctx, payload)
        return default_value if guarded_payload is None else cast(T, guarded_payload)
