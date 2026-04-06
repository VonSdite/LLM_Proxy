#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Proxy request controller for OpenAI and Claude compatible routes."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from flask import Response, jsonify, request

from ..application.app_context import AppContext
from ..config import ConfigManager, ProviderManager
from ..hooks import HookAbortError
from ..services import LogService, ProxyService, UserService
from ..services.proxy_service import ProxyErrorInfo
from ..utils import normalize_ip
from ..utils.local_time import now_local_datetime


class ProxyController:
    """Expose downstream OpenAI-compatible proxy routes."""

    def __init__(
        self,
        ctx: AppContext,
        proxy_service: ProxyService,
        user_service: UserService,
        log_service: LogService,
        provider_manager: ProviderManager,
    ):
        self._ctx = ctx
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._config_manager: ConfigManager = ctx.config_manager
        self._proxy_service = proxy_service
        self._user_service = user_service
        self._log_service = log_service
        self._provider_manager = provider_manager
        self._register_routes()

    def _register_routes(self) -> None:
        self._app.route("/v1/chat/completions", methods=["POST"])(self.chat_completions)
        self._app.route("/v1/responses", methods=["POST"])(self.responses)
        self._app.route("/v1/messages", methods=["POST"])(self.messages)
        self._app.route("/v1/models", methods=["GET"])(self.list_models)

    def _get_user_by_ip(self, ip_address: str) -> Optional[Dict[str, Any]]:
        return self._user_service.get_user_by_ip(ip_address, require_whitelist_access=True)

    def _is_whitelist_required(self) -> bool:
        return self._config_manager.is_chat_whitelist_enabled()

    def _get_authorized_user_for_request(
        self,
        client_ip: str,
        *,
        error_format: str,
    ) -> tuple[Optional[Dict[str, Any]], Optional[tuple[Response, int]]]:
        """在开启白名单时解析当前请求对应用户。"""
        if not self._is_whitelist_required():
            return None, None

        user = self._get_user_by_ip(client_ip)
        if user:
            return user, None

        self._logger.warning("Proxy denied: ip=%s is not in whitelist", client_ip)
        return None, self._error_response(
            f"IP address {client_ip} is not in whitelist",
            403,
            error_type="permission_error",
            code="ip_not_whitelisted",
            error_format=error_format,
        )

    @staticmethod
    def _build_error_payload(
        message: str,
        *,
        error_type: str,
        status_code: int = 400,
        code: Optional[str] = None,
        error_format: str = "openai_chat",
    ) -> Dict[str, Any]:
        normalized_format = str(error_format or "").strip().lower()
        if normalized_format == "claude_chat":
            return {
                "type": "error",
                "error": {
                    "type": error_type,
                    "message": message,
                },
            }
        return {
            "error": {
                "message": message,
                "type": error_type,
                "param": None,
                "code": code,
            }
        }

    def _error_response(
        self,
        message: str,
        status_code: int,
        *,
        error_type: str,
        code: Optional[str] = None,
        error_format: str = "openai_chat",
    ) -> tuple[Response, int]:
        return (
            jsonify(
                self._build_error_payload(
                    message,
                    error_type=error_type,
                    status_code=status_code,
                    code=code,
                    error_format=error_format,
                )
            ),
            status_code,
        )

    @staticmethod
    def _get_provider_target_formats(provider: Any) -> tuple[str, ...]:
        candidate_formats = getattr(provider, "target_formats", None)
        if candidate_formats:
            normalized = tuple(
                str(item or "").strip().lower()
                for item in candidate_formats
                if str(item or "").strip()
            )
            if normalized:
                return normalized

        # DEPRECATED compatibility path for legacy objects that still expose a
        # single `target_format` field. New runtime/provider APIs should only
        # provide `target_formats`, and this fallback can be removed later.
        target_format = str(getattr(provider, "target_format", "") or "").strip().lower()
        if target_format:
            return (target_format,)
        return ()

    @staticmethod
    def _format_provider_target_formats(target_formats: Iterable[str]) -> str:
        normalized = [str(item or "").strip().lower() for item in target_formats if str(item or "").strip()]
        if not normalized:
            return "<empty>"
        if len(normalized) == 1:
            return normalized[0]
        return ", ".join(normalized)

    def chat_completions(self) -> Response:
        return self._proxy_completion_request(
            route_name="chat_completions",
            expected_target_formats=("openai_chat",),
            inspect_stream_usage=True,
        )

    def responses(self) -> Response:
        return self._proxy_completion_request(
            route_name="responses",
            expected_target_formats=("openai_responses", "codex"),
            inspect_stream_usage=False,
        )

    def messages(self) -> Response:
        return self._proxy_completion_request(
            route_name="messages",
            expected_target_formats=("claude_chat",),
            inspect_stream_usage=False,
        )

    def _proxy_completion_request(
        self,
        *,
        route_name: str,
        expected_target_formats: Iterable[str],
        inspect_stream_usage: bool,
        error_format: Optional[str] = None,
    ) -> Response:
        normalized_expected_target_formats = tuple(
            str(item or "").strip().lower() for item in expected_target_formats if str(item or "").strip()
        )
        if not normalized_expected_target_formats:
            raise ValueError("expected_target_formats must not be empty")
        resolved_error_format = error_format or normalized_expected_target_formats[0]
        try:
            client_ip = normalize_ip(request.remote_addr)
            self._logger.info("Proxy request received: route=%s ip=%s", route_name, client_ip)
            user, denial_response = self._get_authorized_user_for_request(
                client_ip,
                error_format=resolved_error_format,
            )
            if denial_response is not None:
                return denial_response

            request_data = request.get_json(silent=True)
            if request_data is None:
                request_data = {}
            if not isinstance(request_data, dict):
                self._logger.warning("Proxy rejected: request body is not a JSON object route=%s", route_name)
                return self._error_response(
                    "Request body must be a JSON object",
                    400,
                    error_type="invalid_request_error",
                    code="invalid_request_body",
                    error_format=resolved_error_format,
                )

            request_data = dict(request_data)
            if "model" not in request_data:
                self._logger.warning("Proxy rejected: missing model in request body route=%s", route_name)
                return self._error_response(
                    "Missing 'model' in request body",
                    400,
                    error_type="invalid_request_error",
                    code="missing_model",
                    error_format=resolved_error_format,
                )

            model_name = request_data["model"]
            provider = self._provider_manager.get_provider_for_model(model_name)
            if not provider:
                self._logger.warning("Proxy rejected: unknown model=%r route=%s", model_name, route_name)
                return self._error_response(
                    f"Unknown model: {model_name}",
                    400,
                    error_type="invalid_request_error",
                    code="unknown_model",
                    error_format=resolved_error_format,
                )

            if self._is_whitelist_required() and not self._user_service.can_user_access_model(
                user,
                model_name,
                available_models=self._provider_manager.list_model_names(),
            ):
                self._logger.warning(
                    "Proxy denied: ip=%s is not allowed to access model=%s route=%s",
                    client_ip,
                    model_name,
                    route_name,
                )
                return self._error_response(
                    f"IP address {client_ip} is not allowed to access model {model_name}",
                    403,
                    error_type="permission_error",
                    code="model_not_allowed",
                    error_format=resolved_error_format,
                )

            provider_target_formats = self._get_provider_target_formats(provider)
            matched_target_formats = tuple(
                item
                for item in provider_target_formats
                if item in normalized_expected_target_formats
            )
            if not matched_target_formats:
                self._logger.warning(
                    "Proxy rejected: model=%s configured for target_formats=%s route=%s",
                    model_name,
                    self._format_provider_target_formats(provider_target_formats),
                    route_name,
                )
                if len(normalized_expected_target_formats) == 1:
                    expected_hint = normalized_expected_target_formats[0]
                    mismatch_message = (
                        f"Model {model_name} is configured for downstream formats "
                        f"{self._format_provider_target_formats(provider_target_formats)}, not {expected_hint}"
                    )
                else:
                    expected_hint = ", ".join(normalized_expected_target_formats)
                    mismatch_message = (
                        f"Model {model_name} is configured for downstream formats "
                        f"{self._format_provider_target_formats(provider_target_formats)}, not one of {expected_hint}"
                    )
                return self._error_response(
                    mismatch_message,
                    400,
                    error_type="invalid_request_error",
                    code="target_format_mismatch",
                    error_format=resolved_error_format,
                )
            if len(matched_target_formats) > 1:
                self._logger.warning(
                    "Proxy rejected: model=%s matched multiple target_formats=%s route=%s",
                    model_name,
                    self._format_provider_target_formats(matched_target_formats),
                    route_name,
                )
                return self._error_response(
                    (
                        f"Model {model_name} matches multiple downstream formats on route {route_name}: "
                        f"{self._format_provider_target_formats(matched_target_formats)}"
                    ),
                    400,
                    error_type="invalid_request_error",
                    code="ambiguous_target_formats",
                    error_format=resolved_error_format,
                )
            resolved_target_format = matched_target_formats[0]

            client_requested_usage_chunk = False
            if inspect_stream_usage:
                stream_options = request_data.get("stream_options")
                client_requested_usage_chunk = isinstance(stream_options, dict) and (
                    stream_options.get("include_usage") is True
                )

            headers = self._filter_request_headers(request.headers)
            start_time = now_local_datetime()

            def on_proxy_complete(response_meta: Dict[str, Any]) -> None:
                self._logger.info(
                    "Proxy completed: route=%s model=%s response_model=%s total_tokens=%s ip=%s",
                    route_name,
                    model_name,
                    response_meta.get("response_model"),
                    response_meta.get("total_tokens", 0),
                    client_ip,
                )
                self._log_service.log_request(
                    request_model=model_name,
                    response_model=response_meta.get("response_model"),
                    total_tokens=response_meta.get("total_tokens", 0),
                    prompt_tokens=response_meta.get("prompt_tokens", 0),
                    completion_tokens=response_meta.get("completion_tokens", 0),
                    start_time=start_time,
                    end_time=now_local_datetime(),
                    ip_address=client_ip,
                )

            result, status_code, failure_info = self._proxy_service.proxy_request(
                provider,
                request_data,
                headers,
                on_complete=on_proxy_complete,
                forward_stream_usage=client_requested_usage_chunk,
                resolved_target_format=resolved_target_format,
            )
            if result is None:
                failure_info = failure_info or ProxyErrorInfo(
                    message="Upstream request failed after retries",
                    status_code=status_code,
                    error_type="upstream_error",
                    error_code="upstream_request_failed",
                )
                self._logger.error(
                    "Proxy failed after retries: route=%s model=%s ip=%s status=%s upstream_error=%s",
                    route_name,
                    model_name,
                    client_ip,
                    status_code,
                    failure_info.message,
                )
                return self._error_response(
                    failure_info.message,
                    status_code,
                    error_type=failure_info.error_type,
                    code=failure_info.error_code,
                    error_format=resolved_error_format,
                )

            return result
        except HookAbortError as exc:
            self._logger.warning(
                "Proxy blocked by hook: route=%s ip=%s status=%s type=%s message=%s",
                route_name,
                normalize_ip(request.remote_addr),
                exc.status_code,
                exc.error_type,
                exc.message,
            )
            return self._error_response(
                exc.message,
                exc.status_code,
                error_type=exc.error_type,
                code=exc.error_type,
                error_format=resolved_error_format,
            )
        except Exception as exc:
            self._logger.error("Error in %s: %s", route_name, exc)
            return self._error_response(
                str(exc),
                500,
                error_type="server_error",
                code="internal_error",
                error_format=resolved_error_format,
            )

    def list_models(self) -> Response:
        try:
            client_ip = normalize_ip(request.remote_addr)
            user, denial_response = self._get_authorized_user_for_request(
                client_ip,
                error_format="openai_chat",
            )
            if denial_response is not None:
                return denial_response

            model_names = list(self._provider_manager.list_model_names())
            if self._is_whitelist_required():
                allowed_models = set(
                    self._user_service.get_accessible_models_for_user(
                        user,
                        available_models=model_names,
                    )
                )
                model_names = [model_name for model_name in model_names if model_name in allowed_models]

            data = []
            for model_key in model_names:
                provider_name, _, _ = str(model_key).partition("/")
                provider_view = self._provider_manager.get_provider_view(provider_name)
                source_format = getattr(provider_view, "source_format", None)
                target_formats = self._get_provider_target_formats(provider_view)
                transport = getattr(provider_view, "transport", None)
                data.append(
                    {
                        "id": model_key,
                        "object": "model",
                        "owned_by": provider_name or "proxy",
                        "provider_name": provider_name or "proxy",
                        "source_format": source_format,
                        "target_formats": list(target_formats),
                        "transport": transport,
                    }
                )
            return jsonify({"object": "list", "data": data})
        except Exception as exc:
            self._logger.error("Error listing models: %s", exc)
            return self._error_response(
                str(exc),
                500,
                error_type="server_error",
                code="internal_error",
            )

    @staticmethod
    def _filter_request_headers(headers: Any) -> Dict[str, str]:
        excluded = {
            "host",
            "content-length",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
        }
        return {k: v for k, v in headers.items() if k.lower() not in excluded}
