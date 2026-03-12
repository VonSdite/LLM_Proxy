#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM Provider 配置与请求阶段钩子封装。"""

from typing import Any, Dict, Optional

from ..hooks import HookContext, HookModule


class LLMProvider:
    """封装 provider 配置与请求阶段钩子调用。"""

    def __init__(
        self,
        name: str,
        api: str,
        api_key: Optional[str] = None,
        model_list: Optional[list] = None,
        proxy: Optional[str] = None,
        timeout_seconds: int = 300,
        max_retries: int = 3,
        verify_ssl: bool = False,
        hook: Optional[HookModule] = None,
    ):
        self.name = name
        self.api = api
        self.api_key = api_key
        self.model_list = model_list or []
        self.proxy = proxy
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.verify_ssl = verify_ssl
        self.hook = hook

    def apply_header_hook(self, ctx: HookContext, headers: Dict[str, str]) -> Dict[str, str]:
        if self.hook and hasattr(self.hook, "header_hook"):
            return self.hook.header_hook(ctx, headers)
        return headers

    def apply_input_body_hook(self, ctx: HookContext, body: Dict[str, Any]) -> Dict[str, Any]:
        if self.hook and hasattr(self.hook, "input_body_hook"):
            return self.hook.input_body_hook(ctx, body)
        return body
