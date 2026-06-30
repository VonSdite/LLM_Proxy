#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游请求构建辅助。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..application.app_context import Logger
from ..external import LLMProvider
from ..hooks import HookContext, HookErrorType
from ..translators import Translator
from .anthropic_billing import resign_anthropic_messages_body_cch
from .upstream_usage import ensure_upstream_usage_capture


@dataclass(frozen=True)
class BuiltUpstreamRequest:
    """标准化后的上游请求构建结果。"""

    headers: dict[str, str]
    original_body: dict[str, Any]
    translated_body: dict[str, Any]
    request_ctx: HookContext


def build_upstream_request(
    *,
    root_path: Path,
    logger: Logger,
    provider: LLMProvider,
    request_model: str,
    upstream_model: str,
    provider_target_format: str,
    request_data: dict[str, Any],
    request_headers: dict[str, str],
    translator: Translator,
    attempt: int,
    previous_status_code: int | None,
    previous_error_type: HookErrorType | None,
    auth_group_name: str | None,
    auth_entry_id: str | None,
) -> BuiltUpstreamRequest:
    """构建经过翻译和 hook 后的上游请求。"""
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
    translated_body = translator.translate_request(
        upstream_model,
        dict(request_data),
        initial_stream,
    )

    upstream_body = provider.apply_request_guard(request_ctx, dict(translated_body))
    final_upstream_model = _resolve_final_upstream_model(upstream_body, upstream_model)
    if final_upstream_model != request_ctx.upstream_model:
        request_ctx = replace(request_ctx, upstream_model=final_upstream_model)
    final_stream = bool(upstream_body.get("stream", False))
    if final_stream != request_ctx.stream:
        request_ctx = replace(request_ctx, stream=final_stream)

    if str(provider.source_format or "").strip().lower() == "claude_chat":
        resign_anthropic_messages_body_cch(upstream_body)
    ensure_upstream_usage_capture(provider.source_format, upstream_body, final_stream)
    return BuiltUpstreamRequest(
        headers=headers,
        original_body=dict(request_data),
        translated_body=upstream_body,
        request_ctx=request_ctx,
    )


def _resolve_final_upstream_model(
    upstream_body: dict[str, Any],
    fallback_model: str,
) -> str:
    """解析最终上游请求体中的实际上游模型名。"""
    upstream_model_value = upstream_body.get("model")
    if not isinstance(upstream_model_value, str):
        return fallback_model

    normalized_upstream_model = upstream_model_value.strip()
    if not normalized_upstream_model:
        return fallback_model

    return normalized_upstream_model
