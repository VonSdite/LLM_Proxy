#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游请求构建辅助。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Optional

from ..application.app_context import Logger
from ..external import LLMProvider
from ..hooks import HookContext, HookErrorType
from ..translators import Translator
from .upstream_usage import ensure_upstream_usage_capture


@dataclass(frozen=True)
class BuiltUpstreamRequest:
    """标准化后的上游请求构建结果。"""

    headers: Dict[str, str]
    guarded_body: Dict[str, Any]
    translated_body: Dict[str, Any]
    request_ctx: HookContext


def build_upstream_request(
    *,
    root_path: Path,
    logger: Logger,
    provider: LLMProvider,
    request_model: str,
    upstream_model: str,
    provider_target_format: str,
    request_data: Dict[str, Any],
    request_headers: Dict[str, str],
    translator: Translator,
    attempt: int,
    previous_status_code: Optional[int],
    previous_error_type: Optional[HookErrorType],
    auth_group_name: Optional[str],
    auth_entry_id: Optional[str],
) -> BuiltUpstreamRequest:
    """构建经过 hook 和翻译后的上游请求。"""
    initial_stream = bool(request_data.get("stream", False))
    request_ctx = HookContext(
        retry=attempt,
        root_path=root_path,
        logger=logger,
        provider_name=provider.name,
        request_model=request_model,
        upstream_model=upstream_model,
        provider_source_format=provider.source_format,
        provider_target_format=provider_target_format,
        transport=provider.transport,
        stream=initial_stream,
        auth_group_name=auth_group_name,
        auth_entry_id=auth_entry_id,
        last_status_code=previous_status_code,
        last_error_type=previous_error_type,
    )

    headers = provider.apply_header_hook(request_ctx, dict(request_headers))
    guarded_body = provider.apply_request_guard(request_ctx, dict(request_data))
    guarded_stream = bool(guarded_body.get("stream", False))
    if guarded_stream != request_ctx.stream:
        request_ctx = replace(request_ctx, stream=guarded_stream)

    translated_body = translator.translate_request(upstream_model, guarded_body, guarded_stream)
    ensure_upstream_usage_capture(provider.source_format, translated_body, guarded_stream)
    return BuiltUpstreamRequest(
        headers=headers,
        guarded_body=guarded_body,
        translated_body=translated_body,
        request_ctx=request_ctx,
    )
