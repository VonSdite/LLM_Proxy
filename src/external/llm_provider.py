#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM provider 运行时对象。"""

from __future__ import annotations

from typing import Any, Dict, Optional

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
