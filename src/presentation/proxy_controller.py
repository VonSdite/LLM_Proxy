#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Proxy request controller for OpenAI and Claude compatible routes."""

from __future__ import annotations

from uuid import uuid4
from typing import Any, Dict, Iterable, Optional, Sequence

from flask import Response, jsonify, request
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..hooks import HookAbortError
from ..services.proxy_service import ProxyErrorInfo
from ..utils import normalize_ip
from ..utils.local_time import now_local_datetime
from ..utils.compat import Protocol


class ConfigManagerLike(Protocol):
    def is_chat_whitelist_enabled(self) -> bool: ...


class ProxyServiceLike(Protocol):
    def proxy_request(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Optional[Response], int, Optional[ProxyErrorInfo]]: ...


class UserServiceLike(Protocol):
    def get_user_by_ip(
        self,
        ip_address: str,
        require_whitelist_access: bool = True,
    ) -> Optional[Dict[str, Any]]: ...

    def can_user_access_model(
        self,
        user: Optional[Dict[str, Any]],
        model_name: str,
        available_models: Optional[Sequence[str]] = None,
    ) -> bool: ...

    def get_accessible_models_for_user(
        self,
        user: Optional[Dict[str, Any]],
        available_models: Optional[Sequence[str]] = None,
    ) -> list[str]: ...


class LogServiceLike(Protocol):
    def log_request(
        self,
        request_model: str,
        response_model: Optional[str],
        total_tokens: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        start_time: Any = None,
        end_time: Any = None,
        ip_address: Optional[str] = None,
    ) -> Optional[int]: ...


class ProviderManagerLike(Protocol):
    def get_provider_for_model(self, model_name: str) -> Any: ...

    def list_model_names(self) -> Iterable[str]: ...

    def get_provider_view(self, provider_name: str) -> Any: ...


class ProxyController:
    """Expose downstream OpenAI-compatible proxy routes."""

    def __init__(
        self,
        ctx: AppContext,
        proxy_service: ProxyServiceLike,
        user_service: UserServiceLike,
        log_service: LogServiceLike,
        provider_manager: ProviderManagerLike,
    ):
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._config_manager: ConfigManagerLike = ctx.config_manager
        self._proxy_service = proxy_service
        self._user_service = user_service
        self._log_service = log_service
        self._provider_manager = provider_manager
        self._register_routes()

    def _log_downstream_request_trace_safe(
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
        try:
            trace_method = getattr(self._proxy_service, "log_downstream_request_trace")
            trace_method(
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
        except AttributeError:
            return
        except Exception as exc:
            self._logger.warning(
                "Proxy trace logging skipped: method=log_downstream_request_trace error=%s",
                exc,
            )

    def _log_downstream_response_trace_safe(
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
        try:
            trace_method = getattr(self._proxy_service, "log_downstream_response_trace")
            trace_method(
                trace_id=trace_id,
                status_code=status_code,
                headers=headers,
                payload=payload,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider_name,
                request_model=request_model,
                target_format=target_format,
                error_type=error_type,
            )
        except AttributeError:
            return
        except Exception as exc:
            self._logger.warning(
                "Proxy trace logging skipped: method=log_downstream_response_trace error=%s",
                exc,
            )

    def _register_routes(self) -> None:
        self._app.route("/v1/chat/completions", methods=["POST"])(self.chat_completions)
        self._app.route("/v1/responses", methods=["POST"])(self.responses)
        self._app.route("/v1/messages", methods=["POST"])(self.messages)
        self._app.route("/v1/models", methods=["GET"])(self.list_models)

    def _get_user_by_ip(self, ip_address: str) -> Optional[Dict[str, Any]]:
        return self._user_service.get_user_by_ip(
            ip_address, require_whitelist_access=True
        )

    def _is_whitelist_required(self) -> bool:
        return self._config_manager.is_chat_whitelist_enabled()

    def _get_authorized_user_for_request(
        self,
        client_ip: str,
        *,
        error_format: str,
    ) -> tuple[Optional[Dict[str, Any]], Optional[tuple[Response, int]]]:
        """在启用白名单时解析当前请求对应的用户。"""
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
        trace_id: Optional[str] = None,
        route_name: Optional[str] = None,
        client_ip: Optional[str] = None,
        provider_name: Optional[str] = None,
        request_model: Optional[str] = None,
        target_format: Optional[str] = None,
    ) -> tuple[Response, int]:
        payload = self._build_error_payload(
            message,
            error_type=error_type,
            status_code=status_code,
            code=code,
            error_format=error_format,
        )
        self._log_downstream_response_trace_safe(
            trace_id=trace_id,
            status_code=status_code,
            headers={"Content-Type": "application/json; charset=utf-8"},
            payload=payload,
            route_name=route_name,
            client_ip=client_ip,
            provider_name=provider_name,
            request_model=request_model,
            target_format=target_format,
            error_type=error_type,
        )
        return (
            jsonify(payload),
            status_code,
        )

    @staticmethod
    def _get_provider_target_formats(provider: Any) -> tuple[str, ...]:
        candidate_formats = getattr(provider, "target_formats", ())
        return tuple(
            str(item or "").strip().lower()
            for item in candidate_formats
            if str(item or "").strip()
        )

    @staticmethod
    def _format_provider_target_formats(target_formats: Iterable[str]) -> str:
        normalized = [
            str(item or "").strip().lower()
            for item in target_formats
            if str(item or "").strip()
        ]
        if not normalized:
            return "<empty>"
        if len(normalized) == 1:
            return normalized[0]
        return ", ".join(normalized)

    def chat_completions(self) -> ResponseReturnValue:
        return self._proxy_completion_request(
            route_name="chat_completions",
            expected_target_formats=("openai_chat",),
            inspect_stream_usage=True,
        )

    def responses(self) -> ResponseReturnValue:
        return self._proxy_completion_request(
            route_name="responses",
            expected_target_formats=("openai_responses",),
            inspect_stream_usage=False,
        )

    def messages(self) -> ResponseReturnValue:
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
    ) -> ResponseReturnValue:
        normalized_expected_target_formats = tuple(
            str(item or "").strip().lower()
            for item in expected_target_formats
            if str(item or "").strip()
        )
        if not normalized_expected_target_formats:
            raise ValueError("expected_target_formats must not be empty")
        resolved_error_format = error_format or normalized_expected_target_formats[0]
        client_ip = normalize_ip(request.remote_addr)
        trace_id: Optional[str] = None
        model_name: Optional[str] = None
        provider_name: Optional[str] = None
        resolved_target_format: Optional[str] = None
        try:
            self._logger.info(
                "Proxy request received: route=%s ip=%s", route_name, client_ip
            )
            user, denial_response = self._get_authorized_user_for_request(
                client_ip,
                error_format=resolved_error_format,
            )
            if denial_response is not None:
                return denial_response

            raw_request_data = request.get_json(silent=True)
            if raw_request_data is None:
                request_data: Dict[str, Any] = {}
            elif not isinstance(raw_request_data, dict):
                self._logger.warning(
                    "Proxy rejected: request body is not a JSON object route=%s",
                    route_name,
                )
                return self._error_response(
                    "Request body must be a JSON object",
                    400,
                    error_type="invalid_request_error",
                    code="invalid_request_body",
                    error_format=resolved_error_format,
                )
            else:
                request_data = dict(raw_request_data)

            model_name_value = request_data.get("model")
            if not isinstance(model_name_value, str) or not model_name_value.strip():
                self._logger.warning(
                    "Proxy rejected: missing model in request body route=%s", route_name
                )
                return self._error_response(
                    "Missing 'model' in request body",
                    400,
                    error_type="invalid_request_error",
                    code="missing_model",
                    error_format=resolved_error_format,
                )

            model_name = model_name_value.strip()
            provider = self._provider_manager.get_provider_for_model(model_name)
            if not provider:
                self._logger.warning(
                    "Proxy rejected: unknown model=%r route=%s", model_name, route_name
                )
                return self._error_response(
                    f"Unknown model: {model_name}",
                    400,
                    error_type="invalid_request_error",
                    code="unknown_model",
                    error_format=resolved_error_format,
                )

            if (
                self._is_whitelist_required()
                and not self._user_service.can_user_access_model(
                    user,
                    model_name,
                    available_models=tuple(self._provider_manager.list_model_names()),
                )
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
            resolved_target_format = next(iter(matched_target_formats), None)
            if resolved_target_format is None:
                raise RuntimeError("resolved_target_format must not be empty")
            provider_name = getattr(provider, "name", None)

            client_requested_usage_chunk = False
            if inspect_stream_usage:
                stream_options = request_data.get("stream_options")
                client_requested_usage_chunk = isinstance(stream_options, dict) and (
                    stream_options.get("include_usage") is True
                )

            trace_id = uuid4().hex
            self._log_downstream_request_trace_safe(
                trace_id=trace_id,
                start_line=self._build_request_start_line(request.method, request.full_path),
                headers=self._copy_headers(request.headers),
                payload=request_data,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider_name,
                request_model=model_name,
                target_format=resolved_target_format,
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
                trace_id=trace_id,
                route_name=route_name,
                client_ip=client_ip,
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
                    trace_id=trace_id,
                    route_name=route_name,
                    client_ip=client_ip,
                    provider_name=provider_name,
                    request_model=model_name,
                    target_format=resolved_target_format,
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
                trace_id=trace_id,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider_name,
                request_model=model_name,
                target_format=resolved_target_format,
            )
        except Exception as exc:
            self._logger.error("Error in %s: %s", route_name, exc)
            return self._error_response(
                str(exc),
                500,
                error_type="server_error",
                code="internal_error",
                error_format=resolved_error_format,
                trace_id=trace_id,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider_name,
                request_model=model_name,
                target_format=resolved_target_format,
            )

    def list_models(self) -> ResponseReturnValue:
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
                model_names = [
                    model_name
                    for model_name in model_names
                    if model_name in allowed_models
                ]

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

    @staticmethod
    def _copy_headers(headers: Any) -> Dict[str, str]:
        return {key: value for key, value in headers.items()}

    @staticmethod
    def _build_request_start_line(method: str, full_path: str) -> str:
        normalized_path = str(full_path or "").rstrip("?") or "/"
        return f"{str(method or 'POST').upper()} {normalized_path} HTTP/1.1"
