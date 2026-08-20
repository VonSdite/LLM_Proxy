#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex OAuth 模型代理服务。"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import requests
from flask import Response

from ..application.app_context import AppContext
from ..proxy_core import (
    DownstreamChunk,
    decode_stream_events,
    encode_downstream_chunk,
    encode_downstream_response_body,
    is_terminal_chunk,
    should_emit_terminal_chunk,
)
from ..proxy_core.usage import public_usage_meta
from ..utils.net import build_module_request_proxies, build_requests_proxy_settings
from ..utils.proxy_warning import (
    PROXY_WARNING_ERROR_CODE,
    PROXY_WARNING_STATUS_CODE,
    ProxyWarningRequired,
    request_with_proxy_warning_retry,
)
from .codex_oauth_service import (
    CODEX_USER_AGENT,
    CodexAuthCandidate,
    CodexOAuthService,
)
from .proxy_response_builder import ProxyResponseBuilder
from .proxy_service import ProxyErrorInfo

CODEX_BACKEND_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_CLIENT_VERSION = ""
CODEX_ORIGINATOR = "codex-tui"
CODEX_PROVIDER_NAME = "codex"
CODEX_PROXY_WARNING_ERROR_CODE = PROXY_WARNING_ERROR_CODE
CODEX_PROXY_WARNING_STATUS_CODE = PROXY_WARNING_STATUS_CODE
CODEX_RESPONSES_LITE_HEADER = "X-OpenAI-Internal-Codex-Responses-Lite"
CODEX_UPSTREAM_REDIRECT_ERROR_CODE = "codex_upstream_redirect"
CODEX_TOOL_IDENTIFIER_MAX_LENGTH = 64


class CodexProxyService:
    """使用本地 Codex OAuth 认证文件代理 Responses 风格模型。"""

    def __init__(self, ctx: AppContext, codex_oauth_service: CodexOAuthService):
        self._logger = ctx.logger
        self._config_manager = ctx.config_manager
        self._codex_oauth_service = codex_oauth_service
        from ..translators import build_default_translator_registry

        self._translator_registry = build_default_translator_registry()

    def has_model(self, model_name: str) -> bool:
        """判断 Codex OAuth 是否支持指定模型。"""
        return self._codex_oauth_service.has_model(model_name)

    def list_model_names(self) -> tuple[str, ...]:
        """返回 Codex OAuth 当前模型名。"""
        return self._codex_oauth_service.list_model_names()

    def has_image_model(self, model_name: str) -> bool:
        """判断 Codex OAuth 是否支持指定图片模型。"""
        return self._codex_oauth_service.has_image_model(model_name)

    def list_image_model_names(self) -> tuple[str, ...]:
        """返回 Codex OAuth 当前图片模型名。"""
        return self._codex_oauth_service.list_image_model_names()

    def get_default_image_model(self) -> str:
        """返回 Codex OAuth 图片生成默认模型。"""
        return self._codex_oauth_service.get_default_image_model()

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
        on_stream_failure: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        """按账号配额顺序代理 Codex 请求。"""
        del trace_id
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

        candidates = self._codex_oauth_service.iter_auth_candidates_for_model(model_name)
        if not candidates:
            return (
                None,
                503,
                ProxyErrorInfo(
                    message=f"No available Codex OAuth account for model: {model_name}",
                    status_code=503,
                    error_type="upstream_error",
                    error_code="codex_auth_unavailable",
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
                forward_stream_usage=forward_stream_usage,
                target_format=target_format,
                route_name=route_name,
                client_ip=client_ip,
                on_stream_failure=on_stream_failure,
            )
            if failure is not None:
                if failure.error_code in {
                    CODEX_PROXY_WARNING_ERROR_CODE,
                    CODEX_UPSTREAM_REDIRECT_ERROR_CODE,
                }:
                    return response, status_code, failure
                last_failure = failure
                continue
            return response, status_code, failure

        if last_failure is None:
            last_failure = ProxyErrorInfo(
                message="All Codex OAuth accounts are unavailable",
                status_code=503,
                error_type="upstream_error",
                error_code="codex_auth_unavailable",
            )
        return None, last_failure.status_code, last_failure

    def proxy_image_request(
        self,
        request_data: dict[str, Any],
        request_headers: dict[str, str],
        *,
        action: str,
        on_complete: Callable[[dict[str, Any]], None] | None = None,
        trace_id: str | None = None,
        route_name: str | None = None,
        client_ip: str | None = None,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        """把 OpenAI Images 请求包装成 Codex image_generation 工具调用。"""
        del trace_id
        normalized_action = self._normalize_image_action(action)
        if not normalized_action:
            return (
                None,
                400,
                ProxyErrorInfo(
                    message="Unsupported image action",
                    status_code=400,
                    error_type="invalid_request_error",
                    error_code="unsupported_image_action",
                ),
            )

        image_model = (
            str(request_data.get("model") or "").strip() or self._codex_oauth_service.get_default_image_model()
        )
        if not image_model:
            return (
                None,
                400,
                ProxyErrorInfo(
                    message="Missing image model",
                    status_code=400,
                    error_type="invalid_request_error",
                    error_code="missing_model",
                ),
            )
        if not self._codex_oauth_service.has_image_model(image_model):
            return (
                None,
                400,
                ProxyErrorInfo(
                    message=f"Unknown image model: {image_model}",
                    status_code=400,
                    error_type="invalid_request_error",
                    error_code="unknown_model",
                ),
            )

        prompt = str(request_data.get("prompt") or "").strip()
        if not prompt:
            return (
                None,
                400,
                ProxyErrorInfo(
                    message="Missing 'prompt' in request body",
                    status_code=400,
                    error_type="invalid_request_error",
                    error_code="missing_prompt",
                ),
            )

        if normalized_action == "edit" and not self._extract_image_input_urls(request_data):
            return (
                None,
                400,
                ProxyErrorInfo(
                    message="Image edit requests require at least one input image",
                    status_code=400,
                    error_type="invalid_request_error",
                    error_code="missing_image",
                ),
            )

        main_model = self._codex_oauth_service.get_image_generation_main_model()
        if not main_model:
            return (
                None,
                503,
                ProxyErrorInfo(
                    message="No Codex OAuth text model is configured for image generation",
                    status_code=503,
                    error_type="upstream_error",
                    error_code="codex_model_unavailable",
                ),
            )

        candidates = [
            candidate
            for candidate in self._codex_oauth_service.iter_auth_candidates_for_model(main_model)
            if not self._is_free_plan_candidate(candidate)
        ]
        if not candidates:
            return (
                None,
                503,
                ProxyErrorInfo(
                    message="No available Codex OAuth account for image generation",
                    status_code=503,
                    error_type="upstream_error",
                    error_code="codex_auth_unavailable",
                ),
            )

        image_request_data = dict(request_data)
        image_request_data["model"] = image_model
        last_failure: ProxyErrorInfo | None = None
        for candidate in candidates:
            response, status_code, failure = self._proxy_image_with_candidate(
                candidate=candidate,
                request_data=image_request_data,
                request_headers=request_headers,
                action=normalized_action,
                image_model=image_model,
                main_model=main_model,
                on_complete=on_complete,
                route_name=route_name,
                client_ip=client_ip,
            )
            if failure is not None:
                if failure.error_code in {
                    CODEX_PROXY_WARNING_ERROR_CODE,
                    CODEX_UPSTREAM_REDIRECT_ERROR_CODE,
                }:
                    return response, status_code, failure
                last_failure = failure
                continue
            return response, status_code, failure

        if last_failure is None:
            last_failure = ProxyErrorInfo(
                message="All Codex OAuth accounts are unavailable for image generation",
                status_code=503,
                error_type="upstream_error",
                error_code="codex_auth_unavailable",
            )
        return None, last_failure.status_code, last_failure

    def _proxy_with_candidate(
        self,
        *,
        candidate: CodexAuthCandidate,
        model_name: str,
        request_data: dict[str, Any],
        request_headers: dict[str, str],
        on_complete: Callable[[dict[str, Any]], None] | None,
        forward_stream_usage: bool,
        target_format: str,
        route_name: str | None,
        client_ip: str | None,
        on_stream_failure: Callable[[dict[str, Any]], None] | None,
        allow_auth_refresh_retry: bool = True,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        translator = self._translator_registry.get("openai_responses", target_format)
        upstream_body = translator.translate_request(
            model_name,
            dict(request_data),
            True,
        )
        if target_format == "claude_chat":
            self._sanitize_codex_claude_compat_body(upstream_body)
        responses_lite = self._is_codex_responses_lite_request(upstream_body, request_headers)
        self._apply_codex_body_defaults(
            upstream_body,
            model_name,
            image_generation_model=self._codex_oauth_service.get_default_image_model(),
            allow_image_generation=self._should_enable_image_generation_tool(
                upstream_body,
                request_headers,
                model_name=model_name,
                candidate=candidate,
            ),
            responses_lite=responses_lite,
        )
        upstream_headers = self._build_codex_headers(
            request_headers,
            candidate,
            stream=True,
        )
        request_options = self._build_request_options()

        try:
            upstream_response = request_with_proxy_warning_retry(
                lambda: requests.post(
                    CODEX_BACKEND_RESPONSES_URL,
                    headers=upstream_headers,
                    json=upstream_body,
                    stream=True,
                    timeout=1200,
                    allow_redirects=False,
                    **request_options,
                ),
                request_options=request_options,
                logger=self._logger,
                log_context=f"provider=codex model={model_name} auth_file={candidate.name}",
            )
        except ProxyWarningRequired as exc:
            self._logger.warning(
                "Codex upstream blocked by network proxy warning: model=%s auth_file=%s "
                "status=%s confirmation_url=%s auto_confirm_error=%s",
                model_name,
                candidate.name,
                exc.upstream_status,
                exc.confirmation_url,
                exc.auto_confirm_error or "",
            )
            return (
                None,
                CODEX_PROXY_WARNING_STATUS_CODE,
                self._build_proxy_warning_error(
                    exc,
                ),
            )
        except (requests.exceptions.RequestException, OSError) as exc:
            return self._build_candidate_transport_failure(
                candidate_name=candidate.name,
                model_name=model_name,
                exc=exc,
                response_started=False,
            )

        if 300 <= upstream_response.status_code < 400:
            location = str(upstream_response.headers.get("Location") or "").strip()
            upstream_response.close()
            message = f"Codex upstream returned redirect {upstream_response.status_code}"
            if location:
                message = f"{message}: {location}"
            return (
                None,
                502,
                ProxyErrorInfo(
                    message=message,
                    status_code=502,
                    error_type="upstream_error",
                    error_code=CODEX_UPSTREAM_REDIRECT_ERROR_CODE,
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
                fallback=f"Codex upstream returned {upstream_response.status_code}",
            )
            self._log_codex_upstream_error(
                response=upstream_response,
                error_message=error_message,
                error_type=error_type,
                auth_file_name=candidate.name,
                route_name=route_name,
                client_ip=client_ip,
                model_name=model_name,
                target_format=target_format,
            )
            if (
                allow_auth_refresh_retry
                and str(candidate.payload.get("refresh_token") or "").strip()
                and self._is_authentication_error_response(upstream_response.status_code, error_type, error_message)
            ):
                refreshed_candidate, refresh_failure = self._refresh_candidate_after_auth_error(
                    candidate,
                    model_name=model_name,
                )
                if refreshed_candidate is not None:
                    return self._proxy_with_candidate(
                        candidate=refreshed_candidate,
                        model_name=model_name,
                        request_data=request_data,
                        request_headers=request_headers,
                        on_complete=on_complete,
                        forward_stream_usage=forward_stream_usage,
                        target_format=target_format,
                        route_name=route_name,
                        client_ip=client_ip,
                        on_stream_failure=on_stream_failure,
                        allow_auth_refresh_retry=False,
                    )
                if refresh_failure is not None:
                    return None, refresh_failure.status_code, refresh_failure
            if self._is_quota_exhausted_response(upstream_response.status_code, body):
                retry_after = self._extract_retry_after_seconds(upstream_response, body)
                self._record_quota_exhausted_response(
                    candidate.name,
                    error_message=error_message,
                    error_type=error_type,
                    retry_after_seconds=retry_after,
                )
                self._logger.warning(
                    "Codex OAuth account quota exhausted: model=%s auth_file=%s",
                    model_name,
                    candidate.name,
                )
                return (
                    None,
                    429,
                    ProxyErrorInfo(
                        message="Codex OAuth account quota exhausted",
                        status_code=429,
                        error_type="upstream_error",
                        error_code="codex_quota_exhausted",
                        response_headers={"Retry-After": retry_after} if retry_after is not None else None,
                    ),
                )
            if self._is_model_capacity_response(upstream_response.status_code, body):
                self._codex_oauth_service.record_auth_file_failure(
                    candidate.name,
                    error_message,
                    status_code=429,
                    error_type=error_type or "model_capacity",
                )
                return (
                    None,
                    429,
                    ProxyErrorInfo(
                        message=error_message,
                        status_code=429,
                        error_type="upstream_error",
                        error_code="codex_model_capacity",
                        details=self._build_codex_upstream_error_details(
                            upstream_response.status_code,
                            error_type=error_type,
                        ),
                        response_headers=dict(upstream_response.headers),
                    ),
                )
            self._codex_oauth_service.record_auth_file_failure(
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
                    error_code=error_type or "codex_upstream_error",
                    details=self._build_codex_upstream_error_details(
                        upstream_response.status_code,
                        error_type=error_type,
                    ),
                    response_headers=dict(upstream_response.headers),
                ),
            )

        if bool(request_data.get("stream", False)):
            try:
                stream_response = self._build_stream_response(
                    response=upstream_response,
                    translator=translator,
                    model_name=model_name,
                    original_request=request_data,
                    translated_request=upstream_body,
                    target_format=target_format,
                    on_complete=on_complete,
                    forward_stream_usage=forward_stream_usage,
                    route_name=route_name,
                    client_ip=client_ip,
                    auth_file_name=candidate.name,
                    on_stream_failure=on_stream_failure,
                )
            except (requests.exceptions.RequestException, OSError) as exc:
                return self._build_candidate_transport_failure(
                    candidate_name=candidate.name,
                    model_name=model_name,
                    exc=exc,
                    response_started=True,
                )
            return (
                stream_response,
                upstream_response.status_code,
                None,
            )

        try:
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
        except (requests.exceptions.RequestException, OSError) as exc:
            return self._build_candidate_transport_failure(
                candidate_name=candidate.name,
                model_name=model_name,
                exc=exc,
                response_started=True,
            )

    def _proxy_image_with_candidate(
        self,
        *,
        candidate: CodexAuthCandidate,
        request_data: dict[str, Any],
        request_headers: dict[str, str],
        action: str,
        image_model: str,
        main_model: str,
        on_complete: Callable[[dict[str, Any]], None] | None,
        route_name: str | None,
        client_ip: str | None,
        allow_auth_refresh_retry: bool = True,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        upstream_body = self._build_image_responses_body(
            request_data,
            action=action,
            image_model=image_model,
            main_model=main_model,
        )
        upstream_headers = self._build_codex_headers(
            request_headers,
            candidate,
            stream=True,
        )
        request_options = self._build_request_options()

        try:
            upstream_response = request_with_proxy_warning_retry(
                lambda: requests.post(
                    CODEX_BACKEND_RESPONSES_URL,
                    headers=upstream_headers,
                    json=upstream_body,
                    stream=True,
                    timeout=1200,
                    allow_redirects=False,
                    **request_options,
                ),
                request_options=request_options,
                logger=self._logger,
                log_context=f"provider=codex image_model={image_model} auth_file={candidate.name}",
            )
        except ProxyWarningRequired as exc:
            self._logger.warning(
                "Codex image upstream blocked by network proxy warning: image_model=%s auth_file=%s "
                "status=%s confirmation_url=%s auto_confirm_error=%s",
                image_model,
                candidate.name,
                exc.upstream_status,
                exc.confirmation_url,
                exc.auto_confirm_error or "",
            )
            return (
                None,
                CODEX_PROXY_WARNING_STATUS_CODE,
                self._build_proxy_warning_error(exc),
            )
        except (requests.exceptions.RequestException, OSError) as exc:
            return self._build_candidate_transport_failure(
                candidate_name=candidate.name,
                model_name=image_model,
                exc=exc,
                response_started=False,
            )

        if 300 <= upstream_response.status_code < 400:
            location = str(upstream_response.headers.get("Location") or "").strip()
            upstream_response.close()
            message = f"Codex upstream returned redirect {upstream_response.status_code}"
            if location:
                message = f"{message}: {location}"
            return (
                None,
                502,
                ProxyErrorInfo(
                    message=message,
                    status_code=502,
                    error_type="upstream_error",
                    error_code=CODEX_UPSTREAM_REDIRECT_ERROR_CODE,
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
                fallback=f"Codex upstream returned {upstream_response.status_code}",
            )
            self._log_codex_upstream_error(
                response=upstream_response,
                error_message=error_message,
                error_type=error_type,
                auth_file_name=candidate.name,
                route_name=route_name,
                client_ip=client_ip,
                model_name=image_model,
                target_format="openai_images",
            )
            if (
                allow_auth_refresh_retry
                and str(candidate.payload.get("refresh_token") or "").strip()
                and self._is_authentication_error_response(upstream_response.status_code, error_type, error_message)
            ):
                refreshed_candidate, refresh_failure = self._refresh_candidate_after_auth_error(
                    candidate,
                    model_name=main_model,
                )
                if refreshed_candidate is not None:
                    return self._proxy_image_with_candidate(
                        candidate=refreshed_candidate,
                        request_data=request_data,
                        request_headers=request_headers,
                        action=action,
                        image_model=image_model,
                        main_model=main_model,
                        on_complete=on_complete,
                        route_name=route_name,
                        client_ip=client_ip,
                        allow_auth_refresh_retry=False,
                    )
                if refresh_failure is not None:
                    return None, refresh_failure.status_code, refresh_failure
            if self._is_quota_exhausted_response(upstream_response.status_code, body):
                self._record_quota_exhausted_response(
                    candidate.name,
                    error_message=error_message,
                    error_type=error_type,
                    retry_after_seconds=self._extract_retry_after_seconds(upstream_response, body),
                )
                return (
                    None,
                    429,
                    ProxyErrorInfo(
                        message="Codex OAuth account quota exhausted",
                        status_code=429,
                        error_type="upstream_error",
                        error_code="codex_quota_exhausted",
                    ),
                )
            if self._is_model_capacity_response(upstream_response.status_code, body):
                self._codex_oauth_service.record_auth_file_failure(
                    candidate.name,
                    error_message,
                    status_code=429,
                    error_type=error_type or "model_capacity",
                )
                return (
                    None,
                    429,
                    ProxyErrorInfo(
                        message=error_message,
                        status_code=429,
                        error_type="upstream_error",
                        error_code="codex_model_capacity",
                        details=self._build_codex_upstream_error_details(
                            upstream_response.status_code,
                            error_type=error_type,
                        ),
                    ),
                )
            self._codex_oauth_service.record_auth_file_failure(
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
                    error_code=error_type or "codex_upstream_error",
                    details=self._build_codex_upstream_error_details(
                        upstream_response.status_code,
                        error_type=error_type,
                    ),
                ),
            )

        if bool(request_data.get("stream", False)):
            return (
                self._build_image_stream_response(
                    response=upstream_response,
                    image_model=image_model,
                    action=action,
                    response_format=self._normalize_image_response_format(request_data.get("response_format")),
                    on_complete=on_complete,
                    auth_file_name=candidate.name,
                ),
                upstream_response.status_code,
                None,
            )

        return self._build_image_nonstream_response(
            response=upstream_response,
            image_model=image_model,
            response_format=self._normalize_image_response_format(request_data.get("response_format")),
            on_complete=on_complete,
            auth_file_name=candidate.name,
        )

    def _build_candidate_transport_failure(
        self,
        *,
        candidate_name: str,
        model_name: str,
        exc: BaseException,
        response_started: bool,
    ) -> tuple[None, int, ProxyErrorInfo]:
        if response_started:
            message = f"HTTP upstream response failed before downstream response started: {exc}"
            auth_error_type = "codex_stream_failed"
            log_context = "response"
        else:
            message = f"HTTP upstream request failed after 1 attempts: {exc}"
            auth_error_type = "upstream_request_failed"
            log_context = "request"
        self._logger.error(
            "Codex upstream %s error: model=%s auth_file=%s error=%s",
            log_context,
            model_name,
            candidate_name,
            exc,
        )
        self._codex_oauth_service.record_auth_file_failure(
            candidate_name,
            message,
            status_code=502,
            error_type=auth_error_type,
        )
        return (
            None,
            502,
            ProxyErrorInfo(
                message=message,
                status_code=502,
                error_type="upstream_error",
                error_code="upstream_request_failed",
            ),
        )

    @staticmethod
    def _apply_codex_body_defaults(
        body: dict[str, Any],
        model_name: str,
        *,
        image_generation_model: str | None = None,
        allow_image_generation: bool = True,
        responses_lite: bool = False,
    ) -> None:
        body["model"] = model_name
        body["stream"] = True
        body["store"] = False
        body["parallel_tool_calls"] = False if responses_lite else True
        body["include"] = ["reasoning.encrypted_content"]
        if isinstance(body.get("input"), str):
            body["input"] = [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": body["input"],
                        }
                    ],
                }
            ]
        for item in body.get("input") or []:
            role = str(item.get("role") or "").strip().lower() if isinstance(item, dict) else ""
            if role == "system":
                item["role"] = "developer"
        if str(body.get("service_tier") or "").strip() not in {"priority", "fast"}:
            body.pop("service_tier", None)
        body.pop("max_output_tokens", None)
        body.pop("max_completion_tokens", None)
        for field in (
            "temperature",
            "top_p",
            "truncation",
            "context_management",
            "user",
            "generate",
            "prompt_cache_retention",
            "safety_identifier",
            "stream_options",
        ):
            body.pop(field, None)
        body.pop("previous_response_id", None)
        body.pop("metadata", None)
        CodexProxyService._normalize_codex_builtin_tools(body)
        if allow_image_generation:
            CodexProxyService._ensure_image_generation_tool(body, image_generation_model)
        if not responses_lite and not CodexProxyService._has_codex_tools(body):
            body.pop("parallel_tool_calls", None)
        body.setdefault("instructions", "")

    @classmethod
    def _sanitize_codex_claude_compat_body(cls, body: dict[str, Any]) -> None:
        """清理 Claude 请求中 Codex Responses 不接受的历史上下文细节。"""
        cls._strip_codex_claude_cache_control(body)

        tool_name_map: dict[str, str] = {}
        tools = body.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict):
                    cls._sanitize_codex_claude_tool(tool, tool_name_map)

        call_id_map: dict[str, str] = {}
        input_items = body.get("input")
        if isinstance(input_items, list):
            sanitized_items: list[Any] = []
            for item in input_items:
                if not isinstance(item, dict):
                    sanitized_items.append(item)
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "reasoning":
                    continue
                if item_type in {"function_call", "custom_tool_call"}:
                    cls._apply_codex_tool_name_map(item, tool_name_map)
                    cls._apply_codex_call_id_map(item, call_id_map)
                elif item_type in {"function_call_output", "custom_tool_call_output"}:
                    cls._apply_codex_call_id_map(item, call_id_map)
                sanitized_items.append(item)
            body["input"] = sanitized_items

        cls._sanitize_codex_tool_choice(body.get("tool_choice"), tool_name_map)

    @classmethod
    def _sanitize_codex_claude_tool(cls, tool: dict[str, Any], tool_name_map: dict[str, str]) -> None:
        original_name = str(tool.get("name") or "").strip()
        if original_name:
            shortened_name = cls._shorten_codex_identifier(original_name, prefer_mcp_leaf=True)
            tool_name_map[original_name] = shortened_name
            tool["name"] = shortened_name
        tool.pop("input_schema", None)
        tool.pop("cache_control", None)
        tool.pop("defer_loading", None)
        parameters = tool.get("parameters")
        if isinstance(parameters, dict):
            parameters.pop("$schema", None)
        if str(tool.get("type") or "").strip().lower() == "function":
            tool["strict"] = False

    @classmethod
    def _sanitize_codex_tool_choice(cls, tool_choice: Any, tool_name_map: dict[str, str]) -> None:
        if not isinstance(tool_choice, dict):
            return
        cls._apply_codex_tool_name_map(tool_choice, tool_name_map)
        function_choice = tool_choice.get("function")
        if isinstance(function_choice, dict):
            cls._apply_codex_tool_name_map(function_choice, tool_name_map)
        choice_type = str(tool_choice.get("type") or "").strip()
        nested_choice = tool_choice.get(choice_type)
        if isinstance(nested_choice, dict):
            cls._apply_codex_tool_name_map(nested_choice, tool_name_map)
        nested_tools = tool_choice.get("tools")
        if isinstance(nested_tools, list):
            for nested_tool in nested_tools:
                if isinstance(nested_tool, dict):
                    cls._apply_codex_tool_name_map(nested_tool, tool_name_map)

    @classmethod
    def _apply_codex_tool_name_map(cls, payload: dict[str, Any], tool_name_map: dict[str, str]) -> None:
        if "name" not in payload:
            return
        original_name = str(payload.get("name") or "").strip()
        if not original_name:
            return
        shortened_name = tool_name_map.get(original_name)
        if shortened_name is None:
            shortened_name = cls._shorten_codex_identifier(original_name, prefer_mcp_leaf=True)
            tool_name_map[original_name] = shortened_name
        payload["name"] = shortened_name

    @classmethod
    def _apply_codex_call_id_map(cls, payload: dict[str, Any], call_id_map: dict[str, str]) -> None:
        if "call_id" not in payload:
            return
        original_call_id = str(payload.get("call_id") or "").strip()
        if not original_call_id:
            return
        shortened_call_id = call_id_map.get(original_call_id)
        if shortened_call_id is None:
            shortened_call_id = cls._shorten_codex_identifier(original_call_id)
            call_id_map[original_call_id] = shortened_call_id
        payload["call_id"] = shortened_call_id

    @staticmethod
    def _shorten_codex_identifier(value: str, *, prefer_mcp_leaf: bool = False) -> str:
        text = str(value or "").strip()
        if len(text) <= CODEX_TOOL_IDENTIFIER_MAX_LENGTH:
            return text
        if prefer_mcp_leaf and text.startswith("mcp__"):
            separator_index = text.rfind("__")
            if separator_index > 0:
                candidate = f"mcp__{text[separator_index + 2 :]}"
                if len(candidate) <= CODEX_TOOL_IDENTIFIER_MAX_LENGTH:
                    return candidate
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        suffix = f"_{digest}"
        prefix_length = max(CODEX_TOOL_IDENTIFIER_MAX_LENGTH - len(suffix), 0)
        return f"{text[:prefix_length]}{suffix}"[:CODEX_TOOL_IDENTIFIER_MAX_LENGTH]

    @staticmethod
    def _strip_codex_claude_cache_control(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("cache_control", None)
            for child in value.values():
                CodexProxyService._strip_codex_claude_cache_control(child)
            return
        if isinstance(value, list):
            for child in value:
                CodexProxyService._strip_codex_claude_cache_control(child)

    @staticmethod
    def _has_codex_tools(body: dict[str, Any]) -> bool:
        tools = body.get("tools")
        return isinstance(tools, list) and any(isinstance(tool, dict) for tool in tools)

    @staticmethod
    def _normalize_codex_builtin_tools(body: dict[str, Any]) -> None:
        """归一 Codex 上游当前接受的内置工具名称。"""

        def normalize_tool(tool: Any) -> None:
            if not isinstance(tool, dict):
                return
            if tool.get("type") in {
                "web_search_preview",
                "web_search_preview_2025_03_11",
            }:
                tool["type"] = "web_search"

        tools = body.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                normalize_tool(tool)

        tool_choice = body.get("tool_choice")
        if isinstance(tool_choice, dict):
            normalize_tool(tool_choice)
            choice_tools = tool_choice.get("tools")
            if isinstance(choice_tools, list):
                for tool in choice_tools:
                    normalize_tool(tool)

    @staticmethod
    def _ensure_image_generation_tool(body: dict[str, Any], image_generation_model: str | None) -> None:
        """确保 Codex 请求携带内置图片生成工具。"""
        normalized_model = str(image_generation_model or "").strip()
        default_tool: dict[str, Any] = {
            "type": "image_generation",
            "output_format": "png",
        }
        if normalized_model:
            default_tool["model"] = normalized_model

        tools = body.get("tools")
        if not isinstance(tools, list):
            body["tools"] = [default_tool]
            return

        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if str(tool.get("type") or "").strip() == "image_generation":
                tool.setdefault("output_format", "png")
                if normalized_model and not str(tool.get("model") or "").strip():
                    tool["model"] = normalized_model
                return
            if CodexProxyService._is_image_generation_function_tool(tool):
                return
        tools.append(default_tool)

    @staticmethod
    def _is_image_generation_function_tool(tool: dict[str, Any]) -> bool:
        tool_type = str(tool.get("type") or "").strip()
        if tool_type == "function":
            return str(tool.get("name") or "").strip() == "image_gen.imagegen"
        if tool_type != "namespace" or str(tool.get("name") or "").strip() != "image_gen":
            return False
        nested_tools = tool.get("tools")
        if not isinstance(nested_tools, list):
            return False
        for nested_tool in nested_tools:
            if not isinstance(nested_tool, dict):
                continue
            if (
                str(nested_tool.get("type") or "").strip() == "function"
                and str(nested_tool.get("name") or "").strip() == "imagegen"
            ):
                return True
        return False

    def _should_enable_image_generation_tool(
        self,
        body: dict[str, Any],
        request_headers: dict[str, str],
        *,
        model_name: str,
        candidate: CodexAuthCandidate,
    ) -> bool:
        if self._is_codex_responses_lite_request(body, request_headers):
            return False
        if str(model_name or "").strip().endswith("spark"):
            return False
        return not self._is_free_plan_candidate(candidate)

    @staticmethod
    def _is_codex_responses_lite_request(body: dict[str, Any], request_headers: dict[str, str]) -> bool:
        for key, value in (request_headers or {}).items():
            if key.lower() == CODEX_RESPONSES_LITE_HEADER.lower() and str(value or "").strip().lower() == "true":
                return True
        client_metadata = body.get("client_metadata")
        if not isinstance(client_metadata, dict):
            return False
        value = client_metadata.get("ws_request_header_x_openai_internal_codex_responses_lite")
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() == "true"

    @staticmethod
    def _is_free_plan_candidate(candidate: CodexAuthCandidate) -> bool:
        return str(candidate.plan_type or "").strip().lower() == "free"

    def _build_image_responses_body(
        self,
        request_data: dict[str, Any],
        *,
        action: str,
        image_model: str,
        main_model: str,
    ) -> dict[str, Any]:
        tool: dict[str, Any] = {
            "type": "image_generation",
            "action": action,
            "model": image_model,
        }
        string_fields = [
            "size",
            "quality",
            "background",
            "output_format",
            "moderation",
        ]
        if action == "edit":
            string_fields.append("input_fidelity")
        for field in string_fields:
            value = str(request_data.get(field) or "").strip()
            if value:
                tool[field] = value

        for field in ("output_compression", "partial_images"):
            value = request_data.get(field)
            if value in (None, ""):
                continue
            try:
                tool[field] = int(value)
            except (TypeError, ValueError):
                continue

        mask_url = self._extract_mask_image_url(request_data)
        if mask_url:
            tool["input_image_mask"] = {"image_url": mask_url}

        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": str(request_data.get("prompt") or "").strip(),
            }
        ]
        for image_url in self._extract_image_input_urls(request_data):
            content.append({"type": "input_image", "image_url": image_url})

        return {
            "instructions": "",
            "stream": True,
            "reasoning": {"effort": "medium", "summary": "auto"},
            "parallel_tool_calls": True,
            "include": ["reasoning.encrypted_content"],
            "model": main_model,
            "store": False,
            "tool_choice": {"type": "image_generation"},
            "tools": [tool],
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": content,
                }
            ],
        }

    @staticmethod
    def _normalize_image_action(action: str) -> str:
        normalized_action = str(action or "").strip().lower()
        return normalized_action if normalized_action in {"generate", "edit"} else ""

    @staticmethod
    def _normalize_image_response_format(response_format: Any) -> str:
        return "url" if str(response_format or "").strip().lower() == "url" else "b64_json"

    @classmethod
    def _extract_image_input_urls(cls, request_data: dict[str, Any]) -> list[str]:
        urls: list[str] = []

        def append_url(value: Any) -> None:
            if isinstance(value, str):
                image_url = value.strip()
            elif isinstance(value, dict):
                image_url = str(value.get("image_url") or value.get("url") or "").strip()
            else:
                image_url = ""
            if image_url:
                urls.append(image_url)

        images = request_data.get("images")
        if isinstance(images, list):
            for item in images:
                append_url(item)
        else:
            append_url(images)

        image = request_data.get("image")
        if isinstance(image, list):
            for item in image:
                append_url(item)
        else:
            append_url(image)
        return list(dict.fromkeys(urls))

    @staticmethod
    def _extract_mask_image_url(request_data: dict[str, Any]) -> str:
        mask = request_data.get("mask")
        if isinstance(mask, str):
            return mask.strip()
        if isinstance(mask, dict):
            return str(mask.get("image_url") or mask.get("url") or "").strip()
        return ""

    def _build_codex_headers(
        self,
        request_headers: dict[str, str],
        candidate: CodexAuthCandidate,
        *,
        stream: bool,
    ) -> dict[str, str]:
        source_headers = request_headers or {}
        originator = self._get_header(source_headers, "Originator") or CODEX_ORIGINATOR
        client_version = self._get_header(source_headers, "Version") or CODEX_CLIENT_VERSION
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {candidate.access_token}",
            "User-Agent": CODEX_USER_AGENT,
            "Accept": "text/event-stream" if stream else "application/json",
            "Connection": "Keep-Alive",
            "Originator": originator,
        }
        for header_name in (
            "X-Codex-Beta-Features",
            "X-Codex-Turn-Metadata",
            "X-Client-Request-Id",
        ):
            header_value = self._get_header(source_headers, header_name)
            if header_value:
                headers[header_name] = header_value
        if client_version:
            headers["Version"] = client_version
        if "Mac OS" in CODEX_USER_AGENT:
            headers["Session_id"] = self._get_header(source_headers, "Session_id") or str(uuid4())
        if candidate.account_id:
            headers["Chatgpt-Account-Id"] = candidate.account_id
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

    def _log_codex_upstream_error(
        self,
        *,
        response: requests.Response,
        error_message: str,
        error_type: str,
        auth_file_name: str,
        route_name: str | None,
        client_ip: str | None,
        model_name: str,
        target_format: str,
    ) -> None:
        status_code = int(getattr(response, "status_code", 0) or 0)
        self._logger.warning(
            "Codex upstream error: route=%s client_ip=%s target_format=%s model=%s auth_file=%s "
            "status=%s error_type=%s error=%s",
            route_name or "<none>",
            client_ip or "<none>",
            target_format or "<none>",
            model_name,
            auth_file_name,
            status_code,
            error_type or "<none>",
            error_message,
        )

    @staticmethod
    def _build_codex_upstream_error_details(
        status_code: int,
        *,
        error_type: str,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {"upstream_status": status_code}
        if error_type:
            details["upstream_error_type"] = error_type
        return details

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
        forward_stream_usage: bool,
        route_name: str | None,
        client_ip: str | None,
        auth_file_name: str,
        on_stream_failure: Callable[[dict[str, Any]], None] | None,
    ) -> Response:
        del route_name, client_ip
        downstream_headers = self._filter_response_headers(response.headers)
        downstream_headers["Content-Type"] = "text/event-stream; charset=utf-8"
        downstream_headers["Cache-Control"] = "no-cache"
        downstream_started = False
        downstream_cancelled = False

        def mark_downstream_started() -> None:
            nonlocal downstream_started
            downstream_started = True

        def mark_downstream_cancelled() -> None:
            nonlocal downstream_cancelled
            downstream_cancelled = True

        def generate() -> Iterator[bytes]:
            nonlocal downstream_cancelled
            state: dict[str, Any] = {}
            meta = ProxyResponseBuilder._create_empty_meta()
            terminal_sent = False
            completed = False
            failed_payload: dict[str, Any] | None = None
            stream_failure_message = ""
            transport_failed = False
            processing_failed = False

            def emit_stream_error(message: str, error_type: str) -> Iterator[bytes]:
                nonlocal terminal_sent
                response_model = str(meta.get("response_model") or model_name)
                for error_chunk in ProxyResponseBuilder.build_stream_error_chunks(
                    downstream_target_format=target_format,
                    message=message,
                    error_type=error_type,
                    response_id=f"stream_error_{CODEX_PROVIDER_NAME}",
                    response_model=response_model,
                ):
                    terminal_chunk = is_terminal_chunk(error_chunk, target_format)
                    encoded_chunk = encode_downstream_chunk(error_chunk, target_format)
                    if encoded_chunk:
                        if terminal_chunk:
                            terminal_sent = True
                        yield encoded_chunk

            def emit_chat_terminal_if_needed() -> Iterator[bytes]:
                nonlocal terminal_sent
                if not should_emit_terminal_chunk(target_format) or terminal_sent:
                    return
                encoded_chunk = encode_downstream_chunk(DownstreamChunk(kind="done"), target_format)
                if encoded_chunk:
                    terminal_sent = True
                    yield encoded_chunk

            try:
                for event in decode_stream_events(response.iter_content(chunk_size=None), "sse_json"):
                    if event.kind == "json" and isinstance(event.payload, dict):
                        ProxyResponseBuilder._update_meta_from_payload(
                            meta,
                            event.payload,
                            source_format="openai_responses",
                        )
                        event_type = str(event.payload.get("type") or event.event or "").strip()
                        if event_type in {"response.completed", "response.done", "response.incomplete"}:
                            completed = True
                        elif event_type in {"response.failed", "response.cancelled", "error"}:
                            failed_payload = event.payload
                    chunks = translator.translate_stream_event(
                        model_name,
                        original_request,
                        translated_request,
                        event,
                        state,
                    )
                    ProxyResponseBuilder._update_meta_from_stream_state(meta, state)
                    for chunk in chunks:
                        terminal_chunk = chunk.kind == "done" or is_terminal_chunk(chunk, target_format)
                        if chunk.kind == "done":
                            if terminal_sent:
                                continue
                        if terminal_chunk and not completed and failed_payload is None:
                            continue

                        if chunk.kind == "json" and isinstance(chunk.payload, dict):
                            if (
                                target_format == "openai_chat"
                                and not forward_stream_usage
                                and self._is_usage_only_stream_chunk(chunk.payload)
                            ):
                                continue
                        encoded = encode_downstream_chunk(chunk, target_format)
                        if encoded:
                            if terminal_chunk:
                                terminal_sent = True
                            yield encoded
            except GeneratorExit:
                downstream_cancelled = True
                raise
            except (requests.exceptions.RequestException, OSError) as exc:
                if terminal_sent or completed:
                    self._logger.warning(
                        "Codex upstream framing error ignored after terminal event: model=%s auth_file=%s error=%s",
                        model_name,
                        auth_file_name,
                        exc,
                    )
                    yield from emit_chat_terminal_if_needed()
                    return
                if failed_payload is not None:
                    self._logger.warning(
                        "Codex upstream framing error ignored after upstream error event: "
                        "model=%s auth_file=%s error=%s",
                        model_name,
                        auth_file_name,
                        exc,
                    )
                    yield from emit_chat_terminal_if_needed()
                    return
                transport_failed = True
                stream_failure_message = str(exc)
                if not downstream_started:
                    raise
                self._logger.error(
                    "Codex streamed upstream transport error: model=%s auth_file=%s error=%s",
                    model_name,
                    auth_file_name,
                    exc,
                )
                yield from emit_stream_error(
                    "Upstream stream interrupted",
                    "upstream_stream_error",
                )
            except Exception as exc:
                processing_failed = True
                stream_failure_message = str(exc)
                raise
            else:
                if not completed and failed_payload is None:
                    transport_failed = True
                    stream_failure_message = "Codex stream closed before response.completed"
                    if not downstream_started:
                        raise requests.exceptions.ChunkedEncodingError(stream_failure_message)
                    self._logger.error(
                        "Codex upstream stream ended before terminal event: model=%s auth_file=%s",
                        model_name,
                        auth_file_name,
                    )
                    yield from emit_stream_error(
                        "Upstream stream interrupted",
                        "upstream_stream_error",
                    )
                elif should_emit_terminal_chunk(target_format) and not terminal_sent:
                    yield from emit_chat_terminal_if_needed()
            finally:
                try:
                    response.close()
                except Exception as exc:
                    self._logger.error("Error closing Codex upstream stream response: %s", exc)
                if downstream_cancelled and not transport_failed and not processing_failed:
                    pass
                elif stream_failure_message and (downstream_started or processing_failed):
                    self._codex_oauth_service.record_auth_file_failure(
                        auth_file_name,
                        stream_failure_message,
                        status_code=502,
                        error_type="codex_stream_failed",
                    )
                elif failed_payload is not None:
                    self._codex_oauth_service.record_auth_file_failure(
                        auth_file_name,
                        self._extract_stream_failure_message(failed_payload),
                        status_code=502,
                        error_type="codex_stream_failed",
                    )
                elif completed:
                    self._codex_oauth_service.record_auth_file_success(auth_file_name)
                if on_stream_failure is not None and downstream_started and not downstream_cancelled:
                    failure_message = stream_failure_message
                    if not failure_message and failed_payload is not None:
                        failure_message = self._extract_stream_failure_message(failed_payload)
                    if failure_message:
                        try:
                            on_stream_failure(
                                {
                                    "status_code": 502,
                                    "error_type": "codex_stream_failed",
                                    "error_message": failure_message,
                                }
                            )
                        except Exception as exc:
                            self._logger.error("Error in Codex on_stream_failure callback: %s", exc)
                should_record_completion = completed or (
                    downstream_started
                    and (downstream_cancelled or transport_failed or processing_failed or failed_payload is not None)
                )
                if on_complete is not None and should_record_completion:
                    try:
                        on_complete(public_usage_meta(meta))
                    except Exception as exc:
                        self._logger.error("Error in Codex on_complete callback: %s", exc)

        return ProxyResponseBuilder.create_streaming_response(
            generate(),
            status_code=response.status_code,
            headers=downstream_headers,
            on_started=mark_downstream_started,
            on_cancelled=mark_downstream_cancelled,
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
        try:
            completed_payload: dict[str, Any] | None = None
            failed_payload: dict[str, Any] | None = None
            for event in decode_stream_events(response.iter_content(chunk_size=None), "sse_json"):
                if event.kind != "json" or not isinstance(event.payload, dict):
                    continue
                event_type = str(event.payload.get("type") or event.event or "").strip()
                if event_type in {"response.completed", "response.done", "response.incomplete"}:
                    completed_payload = event.payload
                elif event_type in {"response.failed", "response.cancelled", "error"}:
                    failed_payload = event.payload

            if completed_payload is None:
                error_message = self._extract_stream_failure_message(failed_payload)
                self._codex_oauth_service.record_auth_file_failure(
                    auth_file_name,
                    error_message,
                    status_code=502,
                    error_type="codex_stream_incomplete",
                )
                return (
                    None,
                    502,
                    ProxyErrorInfo(
                        message=error_message,
                        status_code=502,
                        error_type="upstream_error",
                        error_code="codex_stream_incomplete",
                    ),
                )

            payload_for_translation: Any = completed_payload
            if target_format == "openai_responses" and isinstance(completed_payload.get("response"), dict):
                payload_for_translation = completed_payload["response"]
            translated_payload = translator.translate_nonstream_response(
                model_name,
                original_request,
                translated_request,
                payload_for_translation,
            )
            meta = ProxyResponseBuilder._create_empty_meta()
            ProxyResponseBuilder._update_meta_from_payload(
                meta,
                completed_payload,
                source_format="openai_responses",
            )
            if isinstance(translated_payload, dict):
                ProxyResponseBuilder._update_meta_from_payload(
                    meta,
                    translated_payload,
                    source_format=target_format,
                )
            if on_complete is not None:
                try:
                    on_complete(public_usage_meta(meta))
                except Exception as exc:
                    self._logger.error("Error in Codex on_complete callback: %s", exc)

            self._codex_oauth_service.record_auth_file_success(auth_file_name)
            return (
                Response(
                    encode_downstream_response_body(translated_payload, target_format),
                    status=response.status_code,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                ),
                response.status_code,
                None,
            )
        finally:
            response.close()

    def _build_image_nonstream_response(
        self,
        *,
        response: requests.Response,
        image_model: str,
        response_format: str,
        on_complete: Callable[[dict[str, Any]], None] | None,
        auth_file_name: str,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        try:
            completed_payload, failed_payload, image_items = self._collect_image_response_events(response)
            if completed_payload is None:
                error_message = self._extract_stream_failure_message(failed_payload)
                self._codex_oauth_service.record_auth_file_failure(
                    auth_file_name,
                    error_message,
                    status_code=502,
                    error_type="codex_image_stream_incomplete",
                )
                return (
                    None,
                    502,
                    ProxyErrorInfo(
                        message=error_message,
                        status_code=502,
                        error_type="upstream_error",
                        error_code="codex_image_stream_incomplete",
                    ),
                )

            results, created_at, usage = self._extract_image_results(completed_payload, image_items)
            if not results:
                self._codex_oauth_service.record_auth_file_failure(
                    auth_file_name,
                    "Codex image generation completed without image output",
                    status_code=502,
                    error_type="codex_image_output_missing",
                )
                return (
                    None,
                    502,
                    ProxyErrorInfo(
                        message="Codex image generation completed without image output",
                        status_code=502,
                        error_type="upstream_error",
                        error_code="codex_image_output_missing",
                    ),
                )

            payload = self._build_images_api_response(results, created_at, usage, response_format)
            if on_complete is not None:
                try:
                    on_complete(self._build_image_response_meta(image_model, usage))
                except Exception as exc:
                    self._logger.error("Error in Codex image on_complete callback: %s", exc)
            self._codex_oauth_service.record_auth_file_success(auth_file_name)
            return (
                Response(
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    status=response.status_code,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                ),
                response.status_code,
                None,
            )
        finally:
            response.close()

    def _build_image_stream_response(
        self,
        *,
        response: requests.Response,
        image_model: str,
        action: str,
        response_format: str,
        on_complete: Callable[[dict[str, Any]], None] | None,
        auth_file_name: str,
    ) -> Response:
        downstream_headers = self._filter_response_headers(response.headers)
        downstream_headers["Content-Type"] = "text/event-stream; charset=utf-8"
        downstream_headers["Cache-Control"] = "no-cache"
        stream_prefix = "image_edit" if action == "edit" else "image_generation"

        def generate() -> Iterator[bytes]:
            completed = False
            failed_payload: dict[str, Any] | None = None
            image_items: list[tuple[int, dict[str, Any]]] = []
            usage: dict[str, Any] | None = None
            try:
                for event in decode_stream_events(response.iter_content(chunk_size=None), "sse_json"):
                    if event.kind != "json" or not isinstance(event.payload, dict):
                        continue
                    payload = event.payload
                    event_type = str(payload.get("type") or event.event or "").strip()
                    if event_type == "response.image_generation_call.partial_image":
                        frame = self._build_image_partial_frame(payload, response_format, stream_prefix)
                        if frame:
                            yield frame
                        continue
                    if event_type == "response.output_item.done":
                        item = payload.get("item")
                        if isinstance(item, dict) and str(item.get("type") or "") == "image_generation_call":
                            image_items.append((int(payload.get("output_index") or 0), item))
                        continue
                    if event_type in {"response.completed", "response.done"}:
                        completed = True
                        results, _, usage = self._extract_image_results(payload, image_items)
                        for result in results:
                            yield self._build_image_completed_frame(
                                result,
                                usage,
                                response_format,
                                stream_prefix,
                            )
                        continue
                    if event_type in {"response.failed", "response.cancelled", "error"}:
                        failed_payload = payload
                        yield self._build_image_error_frame(
                            self._extract_stream_failure_message(failed_payload),
                            stream_prefix,
                        )
            except GeneratorExit:
                raise
            except (requests.exceptions.RequestException, OSError) as exc:
                self._logger.error(
                    "Codex image upstream stream error: image_model=%s auth_file=%s error=%s",
                    image_model,
                    auth_file_name,
                    exc,
                )
                self._codex_oauth_service.record_auth_file_failure(
                    auth_file_name,
                    str(exc),
                    status_code=502,
                    error_type="codex_image_stream_failed",
                )
                yield self._build_image_error_frame("Upstream stream interrupted", stream_prefix)
            finally:
                try:
                    response.close()
                except Exception as exc:
                    self._logger.error("Error closing Codex image upstream stream response: %s", exc)
                if failed_payload is not None:
                    self._codex_oauth_service.record_auth_file_failure(
                        auth_file_name,
                        self._extract_stream_failure_message(failed_payload),
                        status_code=502,
                        error_type="codex_image_stream_failed",
                    )
                elif completed:
                    self._codex_oauth_service.record_auth_file_success(auth_file_name)
                    if on_complete is not None:
                        try:
                            on_complete(self._build_image_response_meta(image_model, usage))
                        except Exception as exc:
                            self._logger.error("Error in Codex image on_complete callback: %s", exc)

        return ProxyResponseBuilder.create_streaming_response(
            generate(),
            status_code=response.status_code,
            headers=downstream_headers,
            on_started=lambda: None,
        )

    def _collect_image_response_events(
        self,
        response: requests.Response,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[tuple[int, dict[str, Any]]]]:
        completed_payload: dict[str, Any] | None = None
        failed_payload: dict[str, Any] | None = None
        image_items: list[tuple[int, dict[str, Any]]] = []
        for event in decode_stream_events(response.iter_content(chunk_size=None), "sse_json"):
            if event.kind != "json" or not isinstance(event.payload, dict):
                continue
            event_type = str(event.payload.get("type") or event.event or "").strip()
            if event_type in {"response.completed", "response.done"}:
                completed_payload = event.payload
            elif event_type in {"response.failed", "response.cancelled", "error"}:
                failed_payload = event.payload
            elif event_type == "response.output_item.done":
                item = event.payload.get("item")
                if isinstance(item, dict) and str(item.get("type") or "").strip() == "image_generation_call":
                    image_items.append((int(event.payload.get("output_index") or 0), item))
        return completed_payload, failed_payload, image_items

    @classmethod
    def _extract_image_results(
        cls,
        completed_payload: dict[str, Any],
        image_items: list[tuple[int, dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
        response = completed_payload.get("response")
        if not isinstance(response, dict):
            response = completed_payload
        created_at = cls._safe_int(response.get("created_at"), int(time.time()))
        usage = cls._extract_image_usage(response)

        output_items = response.get("output")
        source_items: list[dict[str, Any]] = []
        if isinstance(output_items, list) and output_items:
            source_items = [item for item in output_items if isinstance(item, dict)]
        elif image_items:
            source_items = [item for _, item in sorted(image_items, key=lambda pair: pair[0])]

        results: list[dict[str, Any]] = []
        for item in source_items:
            if str(item.get("type") or "").strip() != "image_generation_call":
                continue
            result = str(item.get("result") or "").strip()
            if not result:
                continue
            results.append(
                {
                    "result": result,
                    "revised_prompt": str(item.get("revised_prompt") or "").strip(),
                    "output_format": str(item.get("output_format") or "").strip(),
                    "size": str(item.get("size") or "").strip(),
                    "background": str(item.get("background") or "").strip(),
                    "quality": str(item.get("quality") or "").strip(),
                }
            )
        return results, created_at, usage

    @staticmethod
    def _extract_image_usage(response: dict[str, Any]) -> dict[str, Any] | None:
        tool_usage = response.get("tool_usage")
        if isinstance(tool_usage, dict) and isinstance(tool_usage.get("image_gen"), dict):
            return dict(tool_usage["image_gen"])
        usage = response.get("usage")
        return dict(usage) if isinstance(usage, dict) else None

    @classmethod
    def _build_images_api_response(
        cls,
        results: list[dict[str, Any]],
        created_at: int,
        usage: dict[str, Any] | None,
        response_format: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "created": created_at,
            "data": [],
        }
        first = results[0] if results else {}
        for field in ("background", "output_format", "quality", "size"):
            if first.get(field):
                payload[field] = first[field]
        if usage:
            payload["usage"] = usage

        for result in results:
            item: dict[str, Any] = {}
            revised_prompt = str(result.get("revised_prompt") or "").strip()
            if revised_prompt:
                item["revised_prompt"] = revised_prompt
            b64_json = str(result.get("result") or "")
            if response_format == "url":
                item["url"] = f"data:{cls._image_mime_type(result.get('output_format'))};base64,{b64_json}"
            else:
                item["b64_json"] = b64_json
            payload["data"].append(item)
        return payload

    @classmethod
    def _build_image_partial_frame(
        cls,
        payload: dict[str, Any],
        response_format: str,
        stream_prefix: str,
    ) -> bytes:
        partial_image_b64 = str(payload.get("partial_image_b64") or "").strip()
        if not partial_image_b64:
            return b""
        event_name = f"{stream_prefix}.partial_image"
        data: dict[str, Any] = {
            "type": event_name,
            "partial_image_index": cls._safe_int(payload.get("partial_image_index"), 0),
        }
        if response_format == "url":
            data["url"] = f"data:{cls._image_mime_type(payload.get('output_format'))};base64,{partial_image_b64}"
        else:
            data["b64_json"] = partial_image_b64
        return cls._build_image_sse_frame(event_name, data)

    @classmethod
    def _build_image_completed_frame(
        cls,
        result: dict[str, Any],
        usage: dict[str, Any] | None,
        response_format: str,
        stream_prefix: str,
    ) -> bytes:
        event_name = f"{stream_prefix}.completed"
        data: dict[str, Any] = {"type": event_name}
        if usage:
            data["usage"] = usage
        b64_json = str(result.get("result") or "")
        if response_format == "url":
            data["url"] = f"data:{cls._image_mime_type(result.get('output_format'))};base64,{b64_json}"
        else:
            data["b64_json"] = b64_json
        return cls._build_image_sse_frame(event_name, data)

    @classmethod
    def _build_image_error_frame(cls, message: str, stream_prefix: str) -> bytes:
        event_name = f"{stream_prefix}.error"
        return cls._build_image_sse_frame(
            event_name,
            {
                "type": event_name,
                "error": {
                    "message": message,
                    "type": "upstream_error",
                },
            },
        )

    @staticmethod
    def _build_image_sse_frame(event_name: str, payload: dict[str, Any]) -> bytes:
        data = json.dumps(payload, ensure_ascii=False)
        return f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")

    @staticmethod
    def _image_mime_type(output_format: Any) -> str:
        normalized_format = str(output_format or "").strip().lower()
        if normalized_format in {"jpg", "jpeg"}:
            return "image/jpeg"
        if normalized_format == "webp":
            return "image/webp"
        return "image/png"

    @staticmethod
    def _safe_int(value: Any, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def _build_image_response_meta(cls, image_model: str, usage: dict[str, Any] | None) -> dict[str, Any]:
        usage = usage or {}
        input_tokens = cls._safe_int(usage.get("input_tokens") or usage.get("prompt_tokens"), 0)
        output_tokens = cls._safe_int(usage.get("output_tokens") or usage.get("completion_tokens"), 0)
        total_candidate = cls._safe_int(usage.get("total_tokens"), 0)
        total_tokens = total_candidate if total_candidate > 0 else input_tokens + output_tokens
        has_input = "input_tokens" in usage or "prompt_tokens" in usage
        has_output = "output_tokens" in usage or "completion_tokens" in usage
        usage_status = "known" if has_input and has_output else "partial" if has_input or has_output else "unknown"
        return {
            "response_model": image_model,
            "total_tokens": total_tokens,
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "usage_status": usage_status,
        }

    @staticmethod
    def _extract_stream_failure_message(payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return "Codex stream closed before response.completed"
        response = payload.get("response")
        error = response.get("error") if isinstance(response, dict) else payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error.get("message"))
        if isinstance(error, dict) and error.get("type"):
            return str(error.get("type"))
        message = str(payload.get("message") or "").strip()
        if message:
            return message
        code = str(payload.get("code") or "").strip()
        if code:
            return code
        return "Codex stream closed before response.completed"

    @classmethod
    def _extract_response_error_info(cls, body: bytes, *, fallback: str) -> tuple[str, str]:
        raw_text = body.decode("utf-8", errors="replace").strip()
        try:
            payload = json.loads(raw_text) if raw_text else {}
        except json.JSONDecodeError:
            return fallback, ""

        if not isinstance(payload, dict):
            return fallback, ""

        error = payload.get("error")
        if isinstance(error, dict):
            message = cls._first_text_field(
                error,
                ("message", "detail", "error_description", "code", "type"),
            )
            if not message:
                message = cls._first_text_field(payload, ("message", "detail", "error_description", "code"))
            error_type = str(error.get("type") or error.get("code") or "").strip()
            if not message:
                message = fallback
            return message, error_type
        if isinstance(error, str) and error.strip():
            return error.strip(), ""

        message = cls._first_text_field(payload, ("message", "detail", "error_description", "code", "type"))
        if not message:
            message = fallback
        return message, ""

    @staticmethod
    def _first_text_field(payload: dict[str, Any], field_names: tuple[str, ...]) -> str:
        for field_name in field_names:
            value = payload.get(field_name)
            if value in (None, ""):
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _refresh_candidate_after_auth_error(
        self,
        candidate: CodexAuthCandidate,
        *,
        model_name: str,
    ) -> tuple[CodexAuthCandidate | None, ProxyErrorInfo | None]:
        try:
            refreshed_candidate = self._codex_oauth_service.refresh_auth_candidate(candidate.name)
        except Exception as exc:
            message = f"Token refresh failed: {exc}"
            self._logger.warning(
                "Codex auth file refresh after unauthorized response failed: model=%s auth_file=%s error=%s",
                model_name,
                candidate.name,
                exc,
            )
            self._codex_oauth_service.record_auth_file_failure(
                candidate.name,
                message,
                status_code=401,
                error_type="token_refresh_failed",
            )
            return (
                None,
                ProxyErrorInfo(
                    message=message,
                    status_code=401,
                    error_type="upstream_error",
                    error_code="token_refresh_failed",
                ),
            )
        self._logger.info(
            "Codex auth file refreshed after unauthorized response: model=%s auth_file=%s",
            model_name,
            candidate.name,
        )
        return refreshed_candidate, None

    def _record_quota_exhausted_response(
        self,
        auth_file_name: str,
        *,
        error_message: str,
        error_type: str,
        retry_after_seconds: float | None,
    ) -> None:
        """记录额度耗尽，并立即刷新配额快照供前端展示。"""
        self._codex_oauth_service.mark_auth_file_quota_exhausted(
            auth_file_name,
            retry_after_seconds=retry_after_seconds,
        )
        self._codex_oauth_service.record_auth_file_failure(
            auth_file_name,
            error_message or "Codex OAuth account quota exhausted",
            status_code=429,
            error_type=error_type or "usage_limit_reached",
            retry_after_seconds=retry_after_seconds,
        )
        self._codex_oauth_service.refresh_auth_file_quota_snapshot(auth_file_name)

    @staticmethod
    def _is_quota_exhausted_response(status_code: int, body: bytes) -> bool:
        if status_code not in {400, 429}:
            return False
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return status_code == 429
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            if isinstance(payload, dict) and str(payload.get("type") or "").strip() == "usage_limit_reached":
                return True
            return status_code == 429
        error_type = str(error.get("type") or payload.get("type") or "").strip()
        if error_type == "usage_limit_reached":
            return True
        return status_code == 429 and error_type in {"rate_limit_exceeded", ""}

    @staticmethod
    def _is_model_capacity_response(status_code: int, body: bytes) -> bool:
        if status_code != 400:
            return False
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None

        candidates: list[str] = []
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                candidates.append(str(error.get("message") or ""))
            candidates.append(str(payload.get("message") or ""))
        candidates.append(body.decode("utf-8", errors="replace"))

        for candidate in candidates:
            normalized = str(candidate or "").strip().lower()
            if not normalized:
                continue
            if (
                "selected model is at capacity" in normalized
                or "model is at capacity. please try a different model" in normalized
            ):
                return True
        return False

    @staticmethod
    def _is_authentication_error_response(status_code: int, error_type: str, error_message: str) -> bool:
        if status_code == 401:
            return True
        normalized_type = str(error_type or "").strip().lower()
        if normalized_type in {
            "authentication_error",
            "invalid_api_key",
            "invalid_grant",
            "refresh_token_reused",
        }:
            return True
        normalized_message = str(error_message or "").strip().lower()
        return (
            "invalid or expired token" in normalized_message
            or "invalid_api_key" in normalized_message
            or "invalid_grant" in normalized_message
            or "refresh_token_reused" in normalized_message
        )

    @staticmethod
    def _extract_retry_after_seconds(response: requests.Response, body: bytes) -> float | None:
        retry_after = response.headers.get("Retry-After")
        try:
            if retry_after:
                return float(retry_after)
        except ValueError:
            pass

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            retry_source = error
        elif isinstance(payload, dict):
            retry_source = payload
        else:
            return None
        resets_in_seconds = retry_source.get("resets_in_seconds") or retry_source.get("resetsInSeconds")
        try:
            if resets_in_seconds is not None:
                return float(resets_in_seconds)
        except (TypeError, ValueError):
            return None
        resets_at = retry_source.get("resets_at") or retry_source.get("resetsAt")
        try:
            if resets_at is not None:
                return max(float(resets_at) - time.time(), 1.0)
        except (TypeError, ValueError):
            return None
        return None

    @staticmethod
    def _build_proxy_warning_error(exc: ProxyWarningRequired) -> ProxyErrorInfo:
        return ProxyErrorInfo(
            message=str(exc),
            status_code=CODEX_PROXY_WARNING_STATUS_CODE,
            error_type="upstream_error",
            error_code=CODEX_PROXY_WARNING_ERROR_CODE,
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
    def _get_header(headers: dict[str, str], name: str) -> str:
        lowered = name.lower()
        for key, value in headers.items():
            if key.lower() == lowered:
                return str(value or "").strip()
        return ""

    @staticmethod
    def _is_usage_only_stream_chunk(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        if not isinstance(payload.get("usage"), dict):
            return False
        choices = payload.get("choices")
        return choices is None or (isinstance(choices, list) and len(choices) == 0)
