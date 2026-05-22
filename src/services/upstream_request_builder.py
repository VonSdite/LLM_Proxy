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
    guarded_body: dict[str, Any]
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
    guarded_upstream_model = _resolve_guarded_upstream_model(
        provider.name,
        guarded_body,
        upstream_model,
    )
    if guarded_upstream_model != request_ctx.upstream_model:
        request_ctx = replace(request_ctx, upstream_model=guarded_upstream_model)
    guarded_stream = bool(guarded_body.get("stream", False))
    if guarded_stream != request_ctx.stream:
        request_ctx = replace(request_ctx, stream=guarded_stream)

    translated_body = translator.translate_request(
        guarded_upstream_model,
        guarded_body,
        guarded_stream,
    )
    if str(provider.source_format or "").strip().lower() == "claude_chat":
        resign_anthropic_messages_body_cch(translated_body)
    ensure_upstream_usage_capture(provider.source_format, translated_body, guarded_stream)
    return BuiltUpstreamRequest(
        headers=headers,
        guarded_body=guarded_body,
        translated_body=translated_body,
        request_ctx=request_ctx,
    )


def _resolve_guarded_upstream_model(
    provider_name: str,
    guarded_body: dict[str, Any],
    fallback_model: str,
) -> str:
    """解析 request_guard 修改后的实际上游模型名。"""
    guarded_model_value = guarded_body.get("model")
    if not isinstance(guarded_model_value, str):
        return fallback_model

    normalized_guarded_model = guarded_model_value.strip()
    if not normalized_guarded_model:
        return fallback_model

    provider_prefix = f"{provider_name}/"
    if normalized_guarded_model.startswith(provider_prefix):
        return normalized_guarded_model[len(provider_prefix) :]
    return normalized_guarded_model
