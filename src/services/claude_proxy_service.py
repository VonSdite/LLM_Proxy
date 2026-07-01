#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude OAuth 模型代理服务。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import requests
from flask import Response, stream_with_context

from ..application.app_context import AppContext
from ..proxy_core import (
    DownstreamChunk,
    decode_stream_events,
    encode_downstream_chunk,
    encode_downstream_response_body,
    is_terminal_chunk,
    should_emit_terminal_chunk,
)
from ..utils.http_headers import merge_http_headers
from ..utils.net import build_module_request_proxies, build_requests_proxy_settings
from ..utils.proxy_warning import (
    PROXY_WARNING_ERROR_CODE,
    PROXY_WARNING_STATUS_CODE,
    ProxyWarningRequired,
    request_with_proxy_warning_retry,
)
from .anthropic_billing import resign_anthropic_messages_body_cch
from .claude_oauth_service import ClaudeAuthCandidate, ClaudeOAuthService
from .proxy_response_builder import ProxyResponseBuilder
from .proxy_service import ProxyErrorInfo

CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages?beta=true"
CLAUDE_PROVIDER_NAME = "claude"
CLAUDE_USER_AGENT = "claude-cli/2.1.70 (external, cli)"
CLAUDE_PACKAGE_VERSION = "0.80.0"
CLAUDE_RUNTIME_VERSION = "v24.5.0"
CLAUDE_BETA_HEADER = (
    "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,"
    "context-management-2025-06-27,prompt-caching-scope-2026-01-05,"
    "structured-outputs-2025-12-15,fast-mode-2026-02-01,redact-thinking-2026-02-12,"
    "token-efficient-tools-2026-03-28"
)
CLAUDE_PROXY_WARNING_ERROR_CODE = PROXY_WARNING_ERROR_CODE
CLAUDE_PROXY_WARNING_STATUS_CODE = PROXY_WARNING_STATUS_CODE
CLAUDE_UPSTREAM_REDIRECT_ERROR_CODE = "claude_upstream_redirect"


class ClaudeProxyService:
    """使用本地 Claude OAuth 认证文件代理 Anthropic Messages 模型。"""

    def __init__(self, ctx: AppContext, claude_oauth_service: ClaudeOAuthService):
        self._logger = ctx.logger
        self._config_manager = ctx.config_manager
        self._claude_oauth_service = claude_oauth_service
        from ..translators import build_default_translator_registry

        self._translator_registry = build_default_translator_registry()

    def has_model(self, model_name: str) -> bool:
        """判断 Claude OAuth 是否支持指定模型。"""
        return self._claude_oauth_service.has_model(model_name)

    def list_model_names(self) -> tuple[str, ...]:
        """返回 Claude OAuth 当前模型名。"""
        return self._claude_oauth_service.list_model_names()

    def proxy_request(
        self,
        request_data: dict[str, Any],
        request_headers: dict[str, str],
        on_complete: Callable[[dict[str, Any]], None] | None = None,
        forward_stream_usage: bool = False,
        resolved_target_format: str | None = None,
        trace_id: str | None = None,
        route_name: str | None = None,
        client_ip: str | None = None,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        """按认证文件顺序代理 Claude OAuth 请求。"""
        del trace_id, forward_stream_usage
        model_name = str(request_data.get("model") or "").strip()
        target_format = str(resolved_target_format or "").strip().lower()
        if not model_name:
            return (
                None,
                400,
                ProxyErrorInfo(
                    message="Missing 'model' in request body",
                    status_code=400,
                    error_type="invalid_request_error",
                    error_code="missing_model",
                ),
            )
        if not target_format:
            return (
                None,
                400,
                ProxyErrorInfo(
                    message="Missing downstream target format",
                    status_code=400,
                    error_type="invalid_request_error",
                    error_code="missing_target_format",
                ),
            )

        candidates = self._claude_oauth_service.iter_auth_candidates_for_model(model_name)
        if not candidates:
            return (
                None,
                503,
                ProxyErrorInfo(
                    message=f"No available Claude OAuth account for model: {model_name}",
                    status_code=503,
                    error_type="upstream_error",
                    error_code="claude_auth_unavailable",
                ),
            )

        last_failure: ProxyErrorInfo | None = None
        for candidate in candidates:
            response, status_code, failure = self._proxy_with_candidate(
                candidate=candidate,
                model_name=model_name,
                request_data=request_data,
                request_headers=request_headers,
                on_complete=on_complete,
                target_format=target_format,
                route_name=route_name,
                client_ip=client_ip,
            )
            if failure is not None:
                if failure.error_code in {
                    CLAUDE_PROXY_WARNING_ERROR_CODE,
                    CLAUDE_UPSTREAM_REDIRECT_ERROR_CODE,
                }:
                    return response, status_code, failure
                last_failure = failure
                continue
            return response, status_code, failure

        if last_failure is None:
            last_failure = ProxyErrorInfo(
                message="All Claude OAuth accounts are unavailable",
                status_code=503,
                error_type="upstream_error",
                error_code="claude_auth_unavailable",
            )
        return None, last_failure.status_code, last_failure

    def _proxy_with_candidate(
        self,
        *,
        candidate: ClaudeAuthCandidate,
        model_name: str,
        request_data: dict[str, Any],
        request_headers: dict[str, str],
        on_complete: Callable[[dict[str, Any]], None] | None,
        target_format: str,
        route_name: str | None,
        client_ip: str | None,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        translator = self._translator_registry.get("claude_chat", target_format)
        requested_stream = bool(request_data.get("stream", False))
        upstream_body = translator.translate_request(
            model_name,
            dict(request_data),
            requested_stream,
        )
        extra_betas, upstream_body = self._extract_betas(upstream_body)
        self._apply_claude_body_defaults(upstream_body, model_name, requested_stream)
        resign_anthropic_messages_body_cch(upstream_body)
        upstream_headers = self._build_claude_headers(
            request_headers,
            candidate,
            stream=requested_stream,
            extra_betas=extra_betas,
        )
        request_options = self._build_request_options()

        try:
            upstream_response = request_with_proxy_warning_retry(
                lambda: requests.post(
                    CLAUDE_MESSAGES_URL,
                    headers=upstream_headers,
                    json=upstream_body,
                    stream=requested_stream,
                    timeout=1200,
                    allow_redirects=False,
                    **request_options,
                ),
                request_options=request_options,
                logger=self._logger,
                log_context=f"provider=claude model={model_name} auth_file={candidate.name}",
            )
        except ProxyWarningRequired as exc:
            self._logger.warning(
                "Claude upstream blocked by network proxy warning: model=%s auth_file=%s "
                "status=%s confirmation_url=%s auto_confirm_error=%s",
                model_name,
                candidate.name,
                exc.upstream_status,
                exc.confirmation_url,
                exc.auto_confirm_error or "",
            )
            return None, CLAUDE_PROXY_WARNING_STATUS_CODE, self._build_proxy_warning_error(exc)
        except requests.exceptions.RequestException as exc:
            self._logger.error(
                "Claude upstream request error: model=%s auth_file=%s error=%s",
                model_name,
                candidate.name,
                exc,
            )
            self._claude_oauth_service.record_auth_file_failure(
                candidate.name,
                f"HTTP upstream request failed after 1 attempts: {exc}",
                status_code=502,
                error_type="upstream_request_failed",
            )
            return (
                None,
                502,
                ProxyErrorInfo(
                    message=f"HTTP upstream request failed after 1 attempts: {exc}",
                    status_code=502,
                    error_type="upstream_error",
                    error_code="upstream_request_failed",
                ),
            )

        if 300 <= upstream_response.status_code < 400:
            location = str(upstream_response.headers.get("Location") or "").strip()
            upstream_response.close()
            message = f"Claude upstream returned redirect {upstream_response.status_code}"
            if location:
                message = f"{message}: {location}"
            return (
                None,
                502,
                ProxyErrorInfo(
                    message=message,
                    status_code=502,
                    error_type="upstream_error",
                    error_code=CLAUDE_UPSTREAM_REDIRECT_ERROR_CODE,
                    details={
                        "redirect_url": location,
                        "upstream_status": upstream_response.status_code,
                    },
                ),
            )

        if upstream_response.status_code >= 400:
            body = self._read_response_body(upstream_response)
            error_message, error_type = self._extract_response_error_info(
                body,
                fallback=f"Claude upstream returned {upstream_response.status_code}",
            )
            self._claude_oauth_service.record_auth_file_failure(
                candidate.name,
                error_message,
                status_code=upstream_response.status_code,
                error_type=error_type,
            )
            return (
                None,
                upstream_response.status_code,
                ProxyErrorInfo(
                    message=error_message,
                    status_code=upstream_response.status_code,
                    error_type="upstream_error",
                    error_code=error_type or "claude_upstream_error",
                ),
            )

        if requested_stream:
            return (
                self._build_stream_response(
                    response=upstream_response,
                    translator=translator,
                    model_name=model_name,
                    original_request=request_data,
                    translated_request=upstream_body,
                    target_format=target_format,
                    on_complete=on_complete,
                    route_name=route_name,
                    client_ip=client_ip,
                    auth_file_name=candidate.name,
                ),
                upstream_response.status_code,
                None,
            )

        return self._build_nonstream_response(
            response=upstream_response,
            translator=translator,
            model_name=model_name,
            original_request=request_data,
            translated_request=upstream_body,
            target_format=target_format,
            on_complete=on_complete,
            route_name=route_name,
            client_ip=client_ip,
            auth_file_name=candidate.name,
        )

    @staticmethod
    def _apply_claude_body_defaults(body: dict[str, Any], model_name: str, stream: bool) -> None:
        body["model"] = model_name
        body["stream"] = bool(stream)
        try:
            max_tokens = int(body.get("max_tokens") or 0)
        except (TypeError, ValueError):
            max_tokens = 0
        if max_tokens <= 0:
            body["max_tokens"] = 4096

    def _build_claude_headers(
        self,
        request_headers: dict[str, str],
        candidate: ClaudeAuthCandidate,
        *,
        stream: bool,
        extra_betas: list[str],
    ) -> dict[str, str]:
        headers = merge_http_headers({}, request_headers)
        betas = self._merge_betas(self._get_header(headers, "Anthropic-Beta"), extra_betas)
        headers = self._drop_headers(headers, {"authorization", "x-api-key", "content-length", "host"})
        headers = merge_http_headers(
            headers,
            {
                "Authorization": f"Bearer {candidate.access_token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
                "Accept-Encoding": "identity" if stream else "gzip, deflate, br, zstd",
                "Anthropic-Version": "2023-06-01",
                "Anthropic-Beta": betas,
                "X-App": "cli",
                "X-Stainless-Retry-Count": "0",
                "X-Stainless-Runtime": "node",
                "X-Stainless-Lang": "js",
                "X-Stainless-Timeout": "600",
                "X-Stainless-Package-Version": CLAUDE_PACKAGE_VERSION,
                "X-Stainless-Runtime-Version": CLAUDE_RUNTIME_VERSION,
                "X-Stainless-Os": "MacOS",
                "X-Stainless-Arch": "arm64",
                "X-Claude-Code-Session-Id": self._session_id_for_candidate(candidate),
                "x-client-request-id": str(uuid4()),
                "User-Agent": CLAUDE_USER_AGENT,
                "Connection": "keep-alive",
            },
        )
        return headers

    def _build_request_options(self) -> dict[str, Any]:
        if self._config_manager is None:
            return {
                "proxies": {
                    "http": None,
                    "https": None,
                    "all": None,
                },
                "verify": False,
            }
        proxy_settings = build_requests_proxy_settings(
            self._get_oauth_proxy_mode(),
            self._config_manager.get_oauth_proxy(),
            proxy_mode_error_message="OAuth proxy_mode must be one of: direct, system, custom",
            proxy_url_error_message="OAuth proxy must be a valid absolute URL",
        )
        return {
            "proxies": build_module_request_proxies(proxy_settings),
            "verify": self._config_manager.is_oauth_verify_ssl_enabled(),
        }

    def _get_oauth_proxy_mode(self) -> str | None:
        getter = getattr(self._config_manager, "get_oauth_proxy_mode", None)
        if callable(getter):
            value = getter()
            if isinstance(value, str):
                return value
        return None

    def _build_stream_response(
        self,
        *,
        response: requests.Response,
        translator: Any,
        model_name: str,
        original_request: dict[str, Any],
        translated_request: dict[str, Any],
        target_format: str,
        on_complete: Callable[[dict[str, Any]], None] | None,
        route_name: str | None,
        client_ip: str | None,
        auth_file_name: str,
    ) -> Response:
        del route_name, client_ip
        downstream_headers = self._filter_response_headers(response.headers)
        downstream_headers["Content-Type"] = "text/event-stream; charset=utf-8"
        downstream_headers["Cache-Control"] = "no-cache"

        def generate() -> Iterator[bytes]:
            state: dict[str, Any] = {}
            meta = ProxyResponseBuilder._create_empty_meta()
            terminal_sent = False
            completed = False
            stream_error_message = ""
            try:
                for event in decode_stream_events(response.iter_content(chunk_size=None), "sse_json"):
                    if event.kind == "json" and isinstance(event.payload, dict):
                        event_type = str(event.payload.get("type") or event.event or "").strip()
                        if event_type == "message_stop":
                            completed = True
                        elif event_type == "error":
                            stream_error_message = self._extract_stream_error_message(event.payload)
                    chunks = translator.translate_stream_event(
                        model_name,
                        original_request,
                        translated_request,
                        event,
                        state,
                    )
                    ProxyResponseBuilder._update_meta_from_stream_state(meta, state)
                    for chunk in chunks:
                        if chunk.kind == "done":
                            if terminal_sent:
                                continue
                            terminal_sent = True
                        elif is_terminal_chunk(chunk, target_format):
                            terminal_sent = True

                        if chunk.kind == "json" and isinstance(chunk.payload, dict):
                            ProxyResponseBuilder._update_meta_from_payload(meta, chunk.payload)
                        encoded = encode_downstream_chunk(chunk, target_format)
                        if encoded:
                            yield encoded

                if should_emit_terminal_chunk(target_format) and not terminal_sent:
                    yield encode_downstream_chunk(DownstreamChunk(kind="done"), target_format)
            except Exception as exc:
                stream_error_message = str(exc)
                raise
            finally:
                response.close()
                if stream_error_message:
                    self._claude_oauth_service.record_auth_file_failure(
                        auth_file_name,
                        stream_error_message,
                        status_code=502,
                        error_type="claude_stream_failed",
                    )
                elif completed:
                    self._claude_oauth_service.record_auth_file_success(auth_file_name)
                if on_complete is not None:
                    try:
                        on_complete(meta)
                    except Exception as exc:
                        self._logger.error("Error in Claude on_complete callback: %s", exc)

        return Response(
            stream_with_context(generate()),
            status=response.status_code,
            headers=downstream_headers,
        )

    def _build_nonstream_response(
        self,
        *,
        response: requests.Response,
        translator: Any,
        model_name: str,
        original_request: dict[str, Any],
        translated_request: dict[str, Any],
        target_format: str,
        on_complete: Callable[[dict[str, Any]], None] | None,
        route_name: str | None,
        client_ip: str | None,
        auth_file_name: str,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        del route_name, client_ip
        body = self._read_response_body(response)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._claude_oauth_service.record_auth_file_failure(
                auth_file_name,
                f"Claude non-stream response parse failed: {exc}",
                status_code=502,
                error_type="claude_response_parse_failed",
            )
            return (
                None,
                502,
                ProxyErrorInfo(
                    message=f"Claude non-stream response parse failed: {exc}",
                    status_code=502,
                    error_type="upstream_error",
                    error_code="claude_response_parse_failed",
                ),
            )
        translated_payload = translator.translate_nonstream_response(
            model_name,
            original_request,
            translated_request,
            payload,
        )
        meta = ProxyResponseBuilder._create_empty_meta()
        if isinstance(translated_payload, dict):
            ProxyResponseBuilder._update_meta_from_payload(meta, translated_payload)
        if on_complete is not None:
            try:
                on_complete(meta)
            except Exception as exc:
                self._logger.error("Error in Claude on_complete callback: %s", exc)

        self._claude_oauth_service.record_auth_file_success(auth_file_name)
        return (
            Response(
                encode_downstream_response_body(translated_payload, target_format),
                status=response.status_code,
                headers={"Content-Type": "application/json; charset=utf-8"},
            ),
            response.status_code,
            None,
        )

    @staticmethod
    def _extract_response_error_info(body: bytes, *, fallback: str) -> tuple[str, str]:
        text = body.decode("utf-8", errors="replace").strip()
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            return (text[:1000] if text else fallback, "")

        if not isinstance(payload, dict):
            return (text[:1000] if text else fallback, "")

        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("type") or fallback).strip()
            error_type = str(error.get("type") or "").strip()
            return message, error_type
        if isinstance(error, str) and error.strip():
            return error.strip(), ""

        message = str(payload.get("message") or fallback).strip()
        return message, ""

    @staticmethod
    def _extract_stream_error_message(payload: dict[str, Any]) -> str:
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or "Claude stream error")
        return "Claude stream error"

    @staticmethod
    def _build_proxy_warning_error(exc: ProxyWarningRequired) -> ProxyErrorInfo:
        return ProxyErrorInfo(
            message=str(exc),
            status_code=CLAUDE_PROXY_WARNING_STATUS_CODE,
            error_type="upstream_error",
            error_code=CLAUDE_PROXY_WARNING_ERROR_CODE,
            details=exc.to_details(),
        )

    @staticmethod
    def _read_response_body(response: requests.Response) -> bytes:
        try:
            content = getattr(response, "content", None)
            if isinstance(content, bytes):
                return content
            if isinstance(content, str):
                return content.encode("utf-8")
            return b"".join(response.iter_content(chunk_size=None))
        finally:
            response.close()

    @staticmethod
    def _filter_response_headers(headers: Any) -> dict[str, str]:
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
    def _extract_betas(body: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        value = body.get("betas")
        if value is None:
            return [], body
        next_body = dict(body)
        next_body.pop("betas", None)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()], next_body
        normalized = str(value or "").strip()
        return ([normalized] if normalized else []), next_body

    @staticmethod
    def _merge_betas(existing_header: str, extra_betas: list[str]) -> str:
        beta_names: list[str] = []
        seen: set[str] = set()
        source = existing_header or CLAUDE_BETA_HEADER
        for value in [*source.split(","), *extra_betas]:
            beta_name = str(value or "").strip()
            if not beta_name or beta_name in seen:
                continue
            seen.add(beta_name)
            beta_names.append(beta_name)
        for required in ("oauth-2025-04-20", "interleaved-thinking-2025-05-14"):
            if required not in seen:
                beta_names.append(required)
                seen.add(required)
        return ",".join(beta_names)

    @staticmethod
    def _get_header(headers: dict[str, str], name: str) -> str:
        lowered = name.lower()
        for key, value in headers.items():
            if key.lower() == lowered:
                return str(value or "").strip()
        return ""

    @staticmethod
    def _drop_headers(headers: dict[str, str], lowered_names: set[str]) -> dict[str, str]:
        return {key: value for key, value in headers.items() if key.lower() not in lowered_names}

    @staticmethod
    def _session_id_for_candidate(candidate: ClaudeAuthCandidate) -> str:
        seed = candidate.access_token or candidate.email or candidate.name
        digest = uuid4().hex if not seed else hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        return f"session-{digest}"
