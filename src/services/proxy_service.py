#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游 LLM 代理服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

import requests
import websocket
from flask import Response, stream_with_context

from ..application.app_context import AppContext
from ..config.auth_group_manager import AuthGroupManager, AuthGroupSelectionError, SelectedAuthEntry
from ..executors import OpenedUpstreamResponse, build_default_executor_registry
from ..external import LLMProvider
from ..hooks import HookAbortError, HookContext, HookErrorType
from ..proxy_core import (
    DownstreamChunk,
    decode_stream_events,
    encode_downstream_chunk,
    encode_downstream_response_body,
    is_terminal_chunk,
    should_emit_terminal_chunk,
)
from ..translators import Translator, build_default_translator_registry
from ..utils.net import build_requests_proxies
from .proxy_response_builder import ProxyResponseBuilder
from .proxy_trace_logger import ProxyTraceLogger
from .proxy_transport_gateway import ProxyTransportGateway
from .upstream_request_builder import build_upstream_request


@dataclass(frozen=True)
class ProxyErrorInfo:
    """Structured failure information for locally generated proxy errors."""

    message: str
    status_code: int = 502
    error_type: str = "upstream_error"
    error_code: Optional[str] = None


class ProxyService:
    """处理上游 LLM 代理请求。"""

    def __init__(self, ctx: AppContext, auth_group_manager: Optional[AuthGroupManager] = None):
        self._config_manager = ctx.config_manager
        self._logger = ctx.logger
        self._trace_logger = logging.getLogger("llm_request_trace")
        self._root_path = ctx.root_path
        self._auth_group_manager = auth_group_manager
        self._executor_registry = build_default_executor_registry(self._logger)
        self._translator_registry = build_default_translator_registry()
        self._trace = ProxyTraceLogger(self._config_manager, self._trace_logger)
        self._transport = ProxyTransportGateway(self._executor_registry)
        self._response_builder = ProxyResponseBuilder(
            logger=self._logger,
            trace=self._trace,
            filter_response_headers=self._filter_response_headers,
            extend_trace_buffer=self._extend_trace_buffer,
        )

    def log_downstream_request_trace(
        self,
        *,
        trace_id: Optional[str],
        start_line: str,
        headers: Dict[str, Any],
        payload: Any,
        route_name: Optional[str] = None,
        client_ip: Optional[str] = None,
        provider_name: Optional[str] = None,
        request_model: Optional[str] = None,
        target_format: Optional[str] = None,
    ) -> None:
        self._trace.log_entry(
            stage="downstream_request",
            trace_id=trace_id,
            start_line=start_line,
            headers=headers,
            payload=payload,
            route_name=route_name,
            client_ip=client_ip,
            provider_name=provider_name,
            request_model=request_model,
            target_format=target_format,
        )

    def log_downstream_response_trace(
        self,
        *,
        trace_id: Optional[str],
        status_code: int,
        headers: Dict[str, Any],
        payload: Any,
        route_name: Optional[str] = None,
        client_ip: Optional[str] = None,
        provider_name: Optional[str] = None,
        request_model: Optional[str] = None,
        target_format: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> None:
        self._trace.log_entry(
            stage="downstream_response",
            trace_id=trace_id,
            start_line=self._trace.build_response_start_line(status_code),
            headers=headers,
            payload=payload,
            route_name=route_name,
            client_ip=client_ip,
            provider_name=provider_name,
            request_model=request_model,
            target_format=target_format,
            status_code=status_code,
            error_type=error_type,
        )

    def proxy_request(
        self,
        provider: LLMProvider,
        request_data: Dict[str, Any],
        request_headers: Dict[str, str],
        on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
        forward_stream_usage: bool = False,
        resolved_target_format: Optional[str] = None,
        trace_id: Optional[str] = None,
        route_name: Optional[str] = None,
        client_ip: Optional[str] = None,
    ) -> Tuple[Optional[Response], int, Optional[ProxyErrorInfo]]:
        """代理请求到目标 provider，并处理重试、格式转换与 guard。"""
        target_url = provider.api
        requested_model = request_data["model"]
        upstream_model = self._get_upstream_model_name(provider.name, requested_model)
        timeout_seconds = provider.timeout_seconds
        max_retries = provider.max_retries
        verify_ssl = provider.verify_ssl
        request_proxies = build_requests_proxies(provider.proxy)
        downstream_target_format = self._resolve_downstream_target_format(
            provider,
            resolved_target_format,
        )
        translator = self._translator_registry.get(provider.source_format, downstream_target_format)
        last_error: Optional[ProxyErrorInfo] = None
        previous_status_code: Optional[int] = None
        previous_error_type: Optional[HookErrorType] = None
        self._ensure_supported_target_format(downstream_target_format)

        def build_request(
            attempt: int,
            selected_auth: Optional[SelectedAuthEntry],
        ) -> Tuple[Dict[str, str], Dict[str, Any], Dict[str, Any], HookContext]:
            headers = dict(request_headers)
            headers["content-type"] = "application/json"
            if selected_auth is not None:
                headers.update(selected_auth.headers_mapping())
            elif provider.api_key:
                headers["authorization"] = f"Bearer {provider.api_key}"
            built_request = build_upstream_request(
                root_path=self._root_path,
                logger=self._logger,
                provider=provider,
                request_model=requested_model,
                upstream_model=upstream_model,
                provider_target_format=downstream_target_format,
                request_data=request_data,
                request_headers=headers,
                translator=translator,
                attempt=attempt,
                previous_status_code=previous_status_code,
                previous_error_type=previous_error_type,
                auth_group_name=(
                    selected_auth.auth_group_name
                    if selected_auth is not None
                    else provider.auth_group
                ),
                auth_entry_id=(selected_auth.entry_id if selected_auth is not None else None),
            )
            return (
                built_request.headers,
                built_request.guarded_body,
                built_request.translated_body,
                built_request.request_ctx,
            )

        for attempt in range(max_retries):
            selected_auth: Optional[SelectedAuthEntry] = None
            attempt_finalized = False

            def finalize_attempt(
                *,
                status_code: Optional[int] = None,
                error_type: Optional[HookErrorType] = None,
                error_message: Optional[str] = None,
                response_headers: Optional[Dict[str, Any]] = None,
                usage: Optional[Dict[str, Any]] = None,
            ) -> None:
                nonlocal attempt_finalized
                if attempt_finalized:
                    return
                attempt_finalized = True
                if self._auth_group_manager is None:
                    return
                self._auth_group_manager.finish(
                    selected_auth,
                    status_code=status_code,
                    error_type=error_type,
                    error_message=error_message,
                    response_headers=response_headers,
                    usage=usage,
                )

            try:
                if self._auth_group_manager is not None and provider.auth_group:
                    selected_auth = self._auth_group_manager.acquire(provider.auth_group)

                headers, guarded_body, translated_body, request_ctx = build_request(attempt, selected_auth)
                requested_stream = request_ctx.stream
                self._logger.info(
                    "Proxying upstream request: provider=%s transport=%s source=%s target=%s model=%s attempt=%s/%s stream=%s auth_group=%s auth_entry=%s",
                    provider.name,
                    provider.transport,
                    provider.source_format,
                    downstream_target_format,
                    translated_body.get("model"),
                    attempt + 1,
                    max_retries,
                    requested_stream,
                    request_ctx.auth_group_name or "<none>",
                    request_ctx.auth_entry_id or "<none>",
                )
                self._trace.log_entry(
                    stage="upstream_request",
                    trace_id=trace_id,
                    start_line=self._transport.build_upstream_request_start_line(
                        provider.transport,
                        target_url,
                    ),
                    headers=headers,
                    payload=translated_body,
                    route_name=route_name,
                    client_ip=client_ip,
                    provider_name=provider.name,
                    request_model=requested_model,
                    upstream_model=str(translated_body.get("model") or upstream_model),
                    target_format=downstream_target_format,
                    stream=requested_stream,
                    attempt=attempt + 1,
                )
                if self._auth_group_manager is not None:
                    self._auth_group_manager.mark_request_dispatched(selected_auth)

                opened = self._coerce_opened_response(
                    self._open_upstream_response(
                        provider,
                        headers,
                        translated_body,
                        requested_stream,
                        target_url,
                        request_proxies,
                        timeout_seconds,
                        verify_ssl,
                    )
                )

                if self._transport.should_retry_status_code(opened.status_code) and attempt < max_retries - 1:
                    previous_status_code = opened.status_code
                    previous_error_type = None
                    raw_response_headers = dict(getattr(opened.response, "headers", {}) or {})
                    _, _, retry_summary = self._response_builder.consume_upstream_error(
                        provider=provider,
                        opened=opened,
                        downstream_target_format=downstream_target_format,
                        trace_id=trace_id,
                        route_name=route_name,
                        client_ip=client_ip,
                        request_model=requested_model,
                        upstream_model=upstream_model,
                    )
                    finalize_attempt(
                        status_code=opened.status_code,
                        error_message=retry_summary,
                        response_headers=raw_response_headers,
                    )
                    self._logger.warning(
                        "Retryable upstream status (attempt %s/%s): provider=%s status=%s stream=%s, retrying",
                        attempt + 1,
                        max_retries,
                        provider.name,
                        opened.status_code,
                        opened.is_stream,
                    )
                    continue

                if requested_stream != opened.is_stream:
                    self._logger.warning(
                        "Stream mode mismatch: provider=%s requested_stream=%s upstream_stream=%s decoder=%s content_type=%s",
                        provider.name,
                        requested_stream,
                        opened.is_stream,
                        opened.stream_format,
                        opened.content_type,
                    )

                if opened.status_code >= 400:
                    raw_response_headers = dict(getattr(opened.response, "headers", {}) or {})
                    response, error_summary = self._response_builder.build_error_response(
                        provider=provider,
                        opened=opened,
                        downstream_target_format=downstream_target_format,
                        trace_id=trace_id,
                        route_name=route_name,
                        client_ip=client_ip,
                        request_model=requested_model,
                        upstream_model=upstream_model,
                    )
                    finalize_attempt(
                        status_code=opened.status_code,
                        error_message=error_summary,
                        response_headers=raw_response_headers,
                    )
                    return response, opened.status_code, None

                if opened.is_stream:
                    response = self._response_builder.build_stream_response(
                        provider=provider,
                        translator=translator,
                        request_ctx=request_ctx,
                        downstream_target_format=downstream_target_format,
                        original_request=guarded_body,
                        translated_request=translated_body,
                        opened=opened,
                        on_complete=on_complete,
                        forward_stream_usage=forward_stream_usage,
                        finalize_attempt=finalize_attempt,
                        trace_id=trace_id,
                        route_name=route_name,
                        client_ip=client_ip,
                    )
                else:
                    response = self._response_builder.build_nonstream_response(
                        provider=provider,
                        translator=translator,
                        request_ctx=request_ctx,
                        downstream_target_format=downstream_target_format,
                        original_request=guarded_body,
                        translated_request=translated_body,
                        opened=opened,
                        on_complete=on_complete,
                        finalize_attempt=finalize_attempt,
                        trace_id=trace_id,
                        route_name=route_name,
                        client_ip=client_ip,
                    )

                self._logger.info(
                    "Upstream request completed: provider=%s transport=%s source=%s target=%s status=%s stream=%s",
                    provider.name,
                    provider.transport,
                    provider.source_format,
                    downstream_target_format,
                    opened.status_code,
                    opened.is_stream,
                )
                return response, opened.status_code, None
            except AuthGroupSelectionError as exc:
                last_error = ProxyErrorInfo(
                    message=exc.message,
                    status_code=exc.status_code,
                    error_type=exc.error_type,
                    error_code=exc.error_code,
                )
                self._logger.warning(
                    "Auth group unavailable: provider=%s auth_group=%s status=%s error=%s",
                    provider.name,
                    provider.auth_group or "<none>",
                    exc.status_code,
                    exc.message,
                )
                return None, exc.status_code, last_error
            except requests.exceptions.RequestException as exc:
                previous_status_code = None
                previous_error_type = self._transport.classify_request_error(exc)
                last_error = self._build_transport_error_info("HTTP", exc, max_retries)
                self._logger.error(
                    "HTTP upstream request error (attempt %s/%s): provider=%s error=%s",
                    attempt + 1,
                    max_retries,
                    provider.name,
                    exc,
                )
                finalize_attempt(
                    error_type=previous_error_type,
                    error_message=str(exc),
                )
                if attempt < max_retries - 1:
                    continue
            except (websocket.WebSocketException, OSError) as exc:
                previous_status_code = None
                previous_error_type = self._transport.classify_websocket_error(exc)
                last_error = self._build_transport_error_info("WebSocket", exc, max_retries)
                self._logger.error(
                    "WebSocket upstream request error (attempt %s/%s): provider=%s error=%s",
                    attempt + 1,
                    max_retries,
                    provider.name,
                    exc,
                )
                finalize_attempt(
                    error_type=previous_error_type,
                    error_message=str(exc),
                )
                if attempt < max_retries - 1:
                    continue
            except Exception:
                finalize_attempt()
                raise

        if last_error is None:
            last_error = ProxyErrorInfo(
                message="Upstream request failed after retries",
                status_code=502,
                error_type="upstream_error",
                error_code="upstream_request_failed",
            )
        return None, last_error.status_code, last_error

    def _open_upstream_response(
        self,
        provider: LLMProvider,
        headers: Dict[str, str],
        body: Dict[str, Any],
        requested_stream: bool,
        target_url: str,
        request_proxies: Optional[Dict[str, str]],
        timeout_seconds: int,
        verify_ssl: bool,
    ) -> OpenedUpstreamResponse:
        del target_url
        return self._transport.open_upstream_response(
            provider=provider,
            headers=headers,
            body=body,
            requested_stream=requested_stream,
            timeout_seconds=timeout_seconds,
            verify_ssl=verify_ssl,
            request_proxies=request_proxies,
        )

    @staticmethod
    def _coerce_opened_response(result: Any) -> OpenedUpstreamResponse:
        return ProxyTransportGateway.coerce_opened_response(result)

    @staticmethod
    def _build_transport_error_info(
        transport: str,
        exc: Exception,
        max_retries: int,
    ) -> ProxyErrorInfo:
        _, message = ProxyTransportGateway.build_transport_error_info(
            transport,
            exc,
            max_retries,
        )
        return ProxyErrorInfo(
            message=message,
            status_code=502,
            error_type="upstream_error",
            error_code="upstream_request_failed",
        )

    @staticmethod
    def _get_upstream_model_name(provider_name: str, requested_model_name: str) -> str:
        prefix = f"{provider_name}/"
        if requested_model_name.startswith(prefix):
            return requested_model_name[len(prefix):]
        return requested_model_name

    @staticmethod
    def _ensure_supported_target_format(target_format: str) -> None:
        if str(target_format or "").strip().lower() not in {
            "openai_chat",
            "openai_responses",
            "claude_chat",
            "codex",
        }:
            raise ValueError(f"Unsupported downstream target_format: {target_format}")

    @staticmethod
    def _resolve_downstream_target_format(
        provider: LLMProvider,
        resolved_target_format: Optional[str] = None,
    ) -> str:
        normalized_target_format = str(resolved_target_format or "").strip().lower()
        if normalized_target_format:
            return normalized_target_format

        provider_target_formats = tuple(
            str(item or "").strip().lower()
            for item in getattr(provider, "target_formats", ())
            if str(item or "").strip()
        )
        if provider_target_formats:
            return provider_target_formats[0]
        return ""

    @staticmethod
    def _should_retry_status_code(status_code: int) -> bool:
        return ProxyTransportGateway.should_retry_status_code(status_code)

    @staticmethod
    def _classify_request_error(exc: requests.exceptions.RequestException) -> HookErrorType:
        return ProxyTransportGateway.classify_request_error(exc)

    @staticmethod
    def _classify_websocket_error(exc: Exception) -> HookErrorType:
        return ProxyTransportGateway.classify_websocket_error(exc)

    @staticmethod
    def _iter_stream_chunks_with_trace(
        upstream_chunks: Iterator[bytes],
        payload_buffer: Optional[bytearray],
    ) -> Iterator[bytes]:
        for chunk in upstream_chunks:
            if not chunk:
                continue
            ProxyService._extend_trace_buffer(payload_buffer, chunk)
            yield chunk

    def _is_trace_enabled(self, trace_id: Optional[str]) -> bool:
        return self._trace.is_enabled(trace_id)

    @staticmethod
    def _extend_trace_buffer(payload_buffer: Optional[bytearray], payload: Any) -> None:
        if payload_buffer is None:
            return
        payload_buffer.extend(ProxyService._coerce_trace_bytes(payload))

    @staticmethod
    def _filter_response_headers(headers: Any) -> Dict[str, str]:
        excluded = {
            "transfer-encoding",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "upgrade",
            "set-cookie",
            "content-length",
            "content-encoding",
        }
        return {key: value for key, value in headers.items() if key.lower() not in excluded}

    @staticmethod
    def _coerce_trace_bytes(payload: Any) -> bytes:
        return ProxyTraceLogger.coerce_trace_bytes(payload)
