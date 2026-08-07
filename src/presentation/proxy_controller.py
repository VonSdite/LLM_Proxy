#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Proxy request controller for OpenAI and Claude compatible routes."""

from __future__ import annotations

import base64
from collections.abc import Iterable, Sequence
from typing import Any, Protocol
from uuid import uuid4

from flask import Response, jsonify, request
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..hooks import HookAbortError
from ..services.claude_proxy_service import CLAUDE_PROVIDER_NAME
from ..services.codex_proxy_service import CODEX_PROVIDER_NAME
from ..services.proxy_service import ProxyErrorInfo
from ..utils import resolve_client_ip
from ..utils.local_time import now_local_datetime


class ConfigManagerLike(Protocol):
    def is_chat_whitelist_enabled(self) -> bool: ...

    def is_api_key_management_enabled(self) -> bool: ...

    def is_real_client_ip_enabled(self) -> bool: ...

    def get_real_client_ip_header(self) -> str: ...

    def is_model_mapping_enabled(self) -> bool: ...


class ProxyServiceLike(Protocol):
    def proxy_request(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]: ...


class CodexProxyServiceLike(Protocol):
    def has_model(self, model_name: str) -> bool: ...

    def list_model_names(self) -> Iterable[str]: ...

    def has_image_model(self, model_name: str) -> bool: ...

    def list_image_model_names(self) -> Iterable[str]: ...

    def get_default_image_model(self) -> str: ...

    def proxy_request(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]: ...

    def proxy_image_request(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]: ...


class ClaudeProxyServiceLike(Protocol):
    def has_model(self, model_name: str) -> bool: ...

    def list_model_names(self) -> Iterable[str]: ...

    def proxy_request(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]: ...


class UserServiceLike(Protocol):
    def get_user_by_ip(
        self,
        ip_address: str,
        require_whitelist_access: bool = True,
    ) -> dict[str, Any] | None: ...

    def can_user_access_model(
        self,
        user: dict[str, Any] | None,
        model_name: str,
        available_models: Sequence[str] | None = None,
    ) -> bool: ...

    def get_accessible_models_for_user(
        self,
        user: dict[str, Any] | None,
        available_models: Sequence[str] | None = None,
    ) -> list[str]: ...


class ApiKeyServiceLike(Protocol):
    def extract_api_key_from_headers(self, headers: Any) -> str: ...

    def authenticate_api_key(self, raw_api_key: str) -> dict[str, Any] | None: ...

    def can_api_key_access_model(
        self,
        api_key: dict[str, Any] | None,
        model_name: str,
        available_models: Sequence[str] | None = None,
    ) -> bool: ...

    def get_accessible_models_for_api_key(
        self,
        api_key: dict[str, Any] | None,
        available_models: Sequence[str] | None = None,
    ) -> list[str]: ...

    def is_token_limit_exceeded(self, api_key: dict[str, Any] | None) -> bool: ...


class LogServiceLike(Protocol):
    def log_request(
        self,
        request_model: str,
        response_model: str | None,
        total_tokens: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        start_time: Any = None,
        end_time: Any = None,
        ip_address: str | None = None,
        api_key_id: int | None = None,
    ) -> int | None: ...


class ProviderManagerLike(Protocol):
    def get_provider_for_model(self, model_name: str) -> Any: ...

    def list_model_names(self) -> Iterable[str]: ...

    def get_provider_view(self, provider_name: str) -> Any: ...


class ModelMappingServiceLike(Protocol):
    def list_mapping_ids(self) -> Iterable[str]: ...

    def list_image_mapping_ids(self) -> Iterable[str]: ...

    def has_mapping(self, mapping_id: str) -> bool: ...

    def acquire_target(self, mapping_id: str, excluded_target_ids: Iterable[str] = ()) -> Any: ...

    def record_success(self, selection: Any) -> None: ...

    def record_failure(
        self,
        selection: Any,
        *,
        status_code: int | None,
        error_type: str | None,
        error_message: str | None,
        response_headers: Any = None,
    ) -> None: ...


class ProxyController:
    """Expose downstream OpenAI-compatible proxy routes."""

    def __init__(
        self,
        ctx: AppContext,
        proxy_service: ProxyServiceLike,
        user_service: UserServiceLike,
        log_service: LogServiceLike,
        provider_manager: ProviderManagerLike,
        codex_proxy_service: CodexProxyServiceLike | None = None,
        claude_proxy_service: ClaudeProxyServiceLike | None = None,
        api_key_service: ApiKeyServiceLike | None = None,
        model_mapping_service: ModelMappingServiceLike | None = None,
    ):
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._config_manager: ConfigManagerLike = ctx.config_manager
        self._proxy_service = proxy_service
        self._codex_proxy_service = codex_proxy_service
        self._claude_proxy_service = claude_proxy_service
        self._user_service = user_service
        self._log_service = log_service
        self._provider_manager = provider_manager
        self._api_key_service = api_key_service
        self._model_mapping_service = model_mapping_service
        self._register_routes()

    def _log_downstream_request_trace_safe(
        self,
        *,
        trace_id: str | None,
        start_line: str,
        headers: dict[str, Any],
        payload: Any,
        route_name: str | None = None,
        client_ip: str | None = None,
        provider_name: str | None = None,
        request_model: str | None = None,
        target_format: str | None = None,
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
        trace_id: str | None,
        status_code: int,
        headers: dict[str, Any],
        payload: Any,
        route_name: str | None = None,
        client_ip: str | None = None,
        provider_name: str | None = None,
        request_model: str | None = None,
        target_format: str | None = None,
        error_type: str | None = None,
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
        self._app.route("/v1/images/generations", methods=["POST"])(self.images_generations)
        self._app.route("/v1/images/edits", methods=["POST"])(self.images_edits)
        self._app.route("/v1/models", methods=["GET"])(self.list_models)

    def _get_user_by_ip(self, ip_address: str) -> dict[str, Any] | None:
        return self._user_service.get_user_by_ip(ip_address, require_whitelist_access=True)

    def _is_whitelist_required(self) -> bool:
        return self._config_manager.is_chat_whitelist_enabled()

    def _is_api_key_required(self) -> bool:
        read_enabled = getattr(self._config_manager, "is_api_key_management_enabled", None)
        if read_enabled is None:
            return False
        return bool(read_enabled())

    def _get_request_client_ip(self) -> str:
        """按系统设置解析当前请求的客户端 IP。"""
        return resolve_client_ip(
            request.headers,
            request.remote_addr,
            real_ip_enabled=self._config_manager.is_real_client_ip_enabled(),
            real_ip_header=self._config_manager.get_real_client_ip_header(),
        )

    def _get_authorized_user_for_request(
        self,
        client_ip: str,
        *,
        error_format: str,
    ) -> tuple[dict[str, Any] | None, tuple[Response, int] | None]:
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

    def _get_authorized_api_key_for_request(
        self,
        *,
        error_format: str,
    ) -> tuple[dict[str, Any] | None, tuple[Response, int] | None]:
        """在启用 API Key 管理时解析当前请求携带的 key。"""
        if not self._is_api_key_required():
            return None, None

        if self._api_key_service is None:
            self._logger.error("API key management is enabled but API key service is not configured")
            return None, self._error_response(
                "API key service is not configured",
                500,
                error_type="server_error",
                code="api_key_service_unavailable",
                error_format=error_format,
            )

        raw_api_key = self._api_key_service.extract_api_key_from_headers(request.headers)
        if not raw_api_key:
            self._logger.warning("Proxy denied: missing API key")
            return None, self._error_response(
                "Missing API key",
                401,
                error_type="authentication_error",
                code="missing_api_key",
                error_format=error_format,
            )

        api_key = self._api_key_service.authenticate_api_key(raw_api_key)
        if api_key:
            return api_key, None

        self._logger.warning("Proxy denied: invalid API key")
        return None, self._error_response(
            "Invalid API key",
            401,
            error_type="authentication_error",
            code="invalid_api_key",
            error_format=error_format,
        )

    @staticmethod
    def _build_error_payload(
        message: str,
        *,
        error_type: str,
        status_code: int = 400,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        error_format: str = "openai_chat",
    ) -> dict[str, Any]:
        normalized_format = str(error_format or "").strip().lower()
        if normalized_format == "claude_chat":
            error = {
                "type": error_type,
                "message": message,
            }
            if details:
                error["details"] = details
            return {
                "type": "error",
                "error": error,
            }
        error = {
            "message": message,
            "type": error_type,
            "param": None,
            "code": code,
        }
        if details:
            error["details"] = details
        return {"error": error}

    def _error_response(
        self,
        message: str,
        status_code: int,
        *,
        error_type: str,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        error_format: str = "openai_chat",
        trace_id: str | None = None,
        route_name: str | None = None,
        client_ip: str | None = None,
        provider_name: str | None = None,
        request_model: str | None = None,
        target_format: str | None = None,
    ) -> tuple[Response, int]:
        payload = self._build_error_payload(
            message,
            error_type=error_type,
            status_code=status_code,
            code=code,
            details=details,
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
        return tuple(str(item or "").strip().lower() for item in candidate_formats if str(item or "").strip())

    @staticmethod
    def _get_api_key_id(api_key: dict[str, Any] | None) -> int | None:
        """提取 API Key ID，异常数据返回 None。"""
        if not api_key:
            return None
        try:
            return int(api_key.get("id"))
        except (TypeError, ValueError):
            return None

    def _log_request(self, **kwargs: Any) -> int | None:
        """兼容旧测试替身的请求日志写入封装。"""
        api_key_id = kwargs.get("api_key_id")
        try:
            return self._log_service.log_request(**kwargs)
        except TypeError as exc:
            if api_key_id is None or "api_key_id" not in str(exc):
                raise
            fallback_kwargs = dict(kwargs)
            fallback_kwargs.pop("api_key_id", None)
            return self._log_service.log_request(**fallback_kwargs)

    def _list_available_model_names(self, *, visible_provider_models_only: bool = False) -> tuple[str, ...]:
        provider_list_method = self._provider_manager.list_model_names
        if visible_provider_models_only:
            visible_list_method = getattr(self._provider_manager, "list_visible_model_names", None)
            if callable(visible_list_method):
                provider_list_method = visible_list_method
        provider_models = list(provider_list_method())
        codex_models: list[str] = []
        if self._codex_proxy_service is not None:
            try:
                codex_models = list(self._codex_proxy_service.list_model_names())
                codex_models.extend(self._codex_proxy_service.list_image_model_names())
            except Exception as exc:
                self._logger.warning("Codex model list skipped: error=%s", exc)
        claude_models: list[str] = []
        if self._claude_proxy_service is not None:
            try:
                claude_models = list(self._claude_proxy_service.list_model_names())
            except Exception as exc:
                self._logger.warning("Claude model list skipped: error=%s", exc)
        mapping_models: list[str] = []
        if self._model_mapping_service is not None:
            try:
                mapping_models = list(self._model_mapping_service.list_mapping_ids())
            except Exception as exc:
                self._logger.warning("Model mapping list skipped: error=%s", exc)
        return tuple(sorted(dict.fromkeys([*provider_models, *codex_models, *claude_models, *mapping_models])))

    def chat_completions(self) -> ResponseReturnValue:
        return self._proxy_completion_request(
            route_name="chat_completions",
            target_format="openai_chat",
            inspect_stream_usage=True,
        )

    def responses(self) -> ResponseReturnValue:
        return self._proxy_completion_request(
            route_name="responses",
            target_format="openai_responses",
            inspect_stream_usage=False,
        )

    def messages(self) -> ResponseReturnValue:
        return self._proxy_completion_request(
            route_name="messages",
            target_format="claude_chat",
            inspect_stream_usage=False,
        )

    def images_generations(self) -> ResponseReturnValue:
        return self._proxy_image_request(
            route_name="images_generations",
            action="generate",
        )

    def images_edits(self) -> ResponseReturnValue:
        return self._proxy_image_request(
            route_name="images_edits",
            action="edit",
        )

    def _proxy_image_request(
        self,
        *,
        route_name: str,
        action: str,
    ) -> ResponseReturnValue:
        client_ip = self._get_request_client_ip()
        trace_id: str | None = None
        model_name: str | None = None
        provider_name = CODEX_PROVIDER_NAME
        target_format = "openai_images"
        try:
            self._logger.info("Proxy image request received: route=%s ip=%s", route_name, client_ip)
            user, denial_response = self._get_authorized_user_for_request(
                client_ip,
                error_format="openai_chat",
            )
            if denial_response is not None:
                return denial_response
            api_key, denial_response = self._get_authorized_api_key_for_request(
                error_format="openai_chat",
            )
            if denial_response is not None:
                return denial_response
            if self._codex_proxy_service is None:
                return self._error_response(
                    "Codex OAuth image generation is not configured",
                    503,
                    error_type="upstream_error",
                    code="codex_proxy_unavailable",
                    error_format="openai_chat",
                    provider_name=provider_name,
                    target_format=target_format,
                )

            request_data = self._read_image_request_data(action)
            model_name = str(request_data.get("model") or "").strip()
            if not model_name:
                model_name = self._codex_proxy_service.get_default_image_model()
                if model_name:
                    request_data["model"] = model_name
            if not model_name:
                return self._error_response(
                    "Missing image model",
                    400,
                    error_type="invalid_request_error",
                    code="missing_model",
                    error_format="openai_chat",
                    provider_name=provider_name,
                    target_format=target_format,
                )
            is_model_mapping = bool(
                self._model_mapping_service is not None and self._model_mapping_service.has_mapping(model_name)
            )
            if not is_model_mapping and not self._codex_proxy_service.has_image_model(model_name):
                return self._error_response(
                    f"Unknown image model: {model_name}",
                    400,
                    error_type="invalid_request_error",
                    code="unknown_model",
                    error_format="openai_chat",
                    provider_name=provider_name,
                    request_model=model_name,
                    target_format=target_format,
                )

            available_model_names = tuple(sorted(dict.fromkeys([*self._list_available_model_names(), model_name])))
            if self._is_whitelist_required() and not self._user_service.can_user_access_model(
                user,
                model_name,
                available_models=available_model_names,
            ):
                return self._error_response(
                    f"IP address {client_ip} is not allowed to access model {model_name}",
                    403,
                    error_type="permission_error",
                    code="model_not_allowed",
                    error_format="openai_chat",
                    provider_name=provider_name,
                    request_model=model_name,
                    target_format=target_format,
                )
            if self._is_api_key_required() and not self._api_key_service.can_api_key_access_model(
                api_key,
                model_name,
                available_models=available_model_names,
            ):
                return self._error_response(
                    f"API key is not allowed to access model {model_name}",
                    403,
                    error_type="permission_error",
                    code="api_key_model_not_allowed",
                    error_format="openai_chat",
                    provider_name=provider_name,
                    request_model=model_name,
                    target_format=target_format,
                )
            if self._is_api_key_required() and self._api_key_service.is_token_limit_exceeded(api_key):
                return self._error_response(
                    "API key token limit exceeded",
                    429,
                    error_type="rate_limit_error",
                    code="api_key_token_limit_exceeded",
                    error_format="openai_chat",
                    provider_name=provider_name,
                    request_model=model_name,
                    target_format=target_format,
                )

            trace_id = uuid4().hex
            if is_model_mapping:
                provider_name = "model_mapping"
            self._log_downstream_request_trace_safe(
                trace_id=trace_id,
                start_line=self._build_request_start_line(request.method, request.full_path),
                headers=self._copy_headers(request.headers, redact_api_key=self._is_api_key_required()),
                payload=request_data,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider_name,
                request_model=model_name,
                target_format=target_format,
            )
            headers = self._filter_request_headers(request.headers)
            start_time = now_local_datetime()
            api_key_id = self._get_api_key_id(api_key)

            def on_proxy_complete(response_meta: dict[str, Any]) -> None:
                log_kwargs = {
                    "request_model": model_name,
                    "response_model": response_meta.get("response_model"),
                    "total_tokens": response_meta.get("total_tokens", 0),
                    "prompt_tokens": response_meta.get("prompt_tokens", 0),
                    "completion_tokens": response_meta.get("completion_tokens", 0),
                    "start_time": start_time,
                    "end_time": now_local_datetime(),
                    "ip_address": client_ip,
                }
                if api_key_id is not None:
                    log_kwargs["api_key_id"] = api_key_id
                self._log_request(**log_kwargs)

            if is_model_mapping:
                result, status_code, failure_info = self._proxy_model_mapping_image_request(
                    mapping_id=model_name,
                    request_data=request_data,
                    request_headers=headers,
                    action=action,
                    on_complete=on_proxy_complete,
                    trace_id=trace_id,
                    route_name=route_name,
                    client_ip=client_ip,
                )
            else:
                result, status_code, failure_info = self._codex_proxy_service.proxy_image_request(
                    request_data,
                    headers,
                    action=action,
                    on_complete=on_proxy_complete,
                    trace_id=trace_id,
                    route_name=route_name,
                    client_ip=client_ip,
                )
            if result is None:
                failure_info = failure_info or ProxyErrorInfo(
                    message="Upstream image request failed after retries",
                    status_code=status_code,
                    error_type="upstream_error",
                    error_code="upstream_request_failed",
                )
                return self._error_response(
                    failure_info.message,
                    status_code,
                    error_type=failure_info.error_type,
                    code=failure_info.error_code,
                    details=failure_info.details,
                    error_format="openai_chat",
                    trace_id=trace_id,
                    route_name=route_name,
                    client_ip=client_ip,
                    provider_name=provider_name,
                    request_model=model_name,
                    target_format=target_format,
                )
            return result
        except ValueError as exc:
            return self._error_response(
                str(exc),
                400,
                error_type="invalid_request_error",
                code="invalid_request_body",
                error_format="openai_chat",
                trace_id=trace_id,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider_name,
                request_model=model_name,
                target_format=target_format,
            )
        except Exception as exc:
            self._logger.error("Error in %s: %s", route_name, exc)
            return self._error_response(
                str(exc),
                500,
                error_type="server_error",
                code="internal_error",
                error_format="openai_chat",
                trace_id=trace_id,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider_name,
                request_model=model_name,
                target_format=target_format,
            )

    def _proxy_completion_request(
        self,
        *,
        route_name: str,
        target_format: str,
        inspect_stream_usage: bool,
        error_format: str | None = None,
    ) -> ResponseReturnValue:
        resolved_target_format = str(target_format or "").strip().lower()
        if not resolved_target_format:
            raise ValueError("target_format must not be empty")
        resolved_error_format = error_format or resolved_target_format
        client_ip = self._get_request_client_ip()
        trace_id: str | None = None
        model_name: str | None = None
        provider_name: str | None = None
        try:
            self._logger.info("Proxy request received: route=%s ip=%s", route_name, client_ip)
            user, denial_response = self._get_authorized_user_for_request(
                client_ip,
                error_format=resolved_error_format,
            )
            if denial_response is not None:
                return denial_response
            api_key, denial_response = self._get_authorized_api_key_for_request(
                error_format=resolved_error_format,
            )
            if denial_response is not None:
                return denial_response

            raw_request_data = request.get_json(silent=True)
            if raw_request_data is None:
                request_data: dict[str, Any] = {}
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
                self._logger.warning("Proxy rejected: missing model in request body route=%s", route_name)
                return self._error_response(
                    "Missing 'model' in request body",
                    400,
                    error_type="invalid_request_error",
                    code="missing_model",
                    error_format=resolved_error_format,
                )

            model_name = model_name_value.strip()
            provider, is_codex_model, is_claude_model, is_model_mapping = self._resolve_model_route(model_name)
            if provider is None and not (is_codex_model or is_claude_model or is_model_mapping):
                self._logger.warning("Proxy rejected: unknown model=%r route=%s", model_name, route_name)
                return self._error_response(
                    f"Unknown model: {model_name}",
                    400,
                    error_type="invalid_request_error",
                    code="unknown_model",
                    error_format=resolved_error_format,
                )

            available_model_names = self._list_available_model_names()
            if self._is_whitelist_required() and not self._user_service.can_user_access_model(
                user,
                model_name,
                available_models=available_model_names,
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
            if self._is_api_key_required() and not self._api_key_service.can_api_key_access_model(
                api_key,
                model_name,
                available_models=available_model_names,
            ):
                key_preview = str(api_key.get("key_preview") or api_key.get("id") or "") if api_key else ""
                self._logger.warning(
                    "Proxy denied: api_key=%s is not allowed to access model=%s route=%s",
                    key_preview,
                    model_name,
                    route_name,
                )
                return self._error_response(
                    f"API key is not allowed to access model {model_name}",
                    403,
                    error_type="permission_error",
                    code="api_key_model_not_allowed",
                    error_format=resolved_error_format,
                )
            if self._is_api_key_required() and self._api_key_service.is_token_limit_exceeded(api_key):
                key_preview = str(api_key.get("key_preview") or api_key.get("id") or "") if api_key else ""
                self._logger.warning(
                    "Proxy denied: api_key=%s token limit exceeded route=%s model=%s",
                    key_preview,
                    route_name,
                    model_name,
                )
                return self._error_response(
                    "API key token limit exceeded",
                    429,
                    error_type="rate_limit_error",
                    code="api_key_token_limit_exceeded",
                    error_format=resolved_error_format,
                )

            provider_name = getattr(provider, "name", None)
            if is_codex_model:
                provider_name = CODEX_PROVIDER_NAME
            elif is_claude_model:
                provider_name = CLAUDE_PROVIDER_NAME
            elif is_model_mapping:
                provider_name = "model_mapping"

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
                headers=self._copy_headers(request.headers, redact_api_key=self._is_api_key_required()),
                payload=request_data,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider_name,
                request_model=model_name,
                target_format=resolved_target_format,
            )
            headers = self._filter_request_headers(request.headers)
            start_time = now_local_datetime()
            api_key_id = self._get_api_key_id(api_key)

            def on_proxy_complete(response_meta: dict[str, Any]) -> None:
                self._logger.info(
                    "Proxy completed: route=%s model=%s response_model=%s total_tokens=%s ip=%s",
                    route_name,
                    model_name,
                    response_meta.get("response_model"),
                    response_meta.get("total_tokens", 0),
                    client_ip,
                )
                log_kwargs = {
                    "request_model": model_name,
                    "response_model": response_meta.get("response_model"),
                    "total_tokens": response_meta.get("total_tokens", 0),
                    "prompt_tokens": response_meta.get("prompt_tokens", 0),
                    "completion_tokens": response_meta.get("completion_tokens", 0),
                    "start_time": start_time,
                    "end_time": now_local_datetime(),
                    "ip_address": client_ip,
                }
                if api_key_id is not None:
                    log_kwargs["api_key_id"] = api_key_id
                self._log_request(**log_kwargs)

            if is_model_mapping:
                result, status_code, failure_info = self._proxy_model_mapping_request(
                    mapping_id=model_name,
                    request_data=request_data,
                    request_headers=headers,
                    on_complete=on_proxy_complete,
                    forward_stream_usage=client_requested_usage_chunk,
                    resolved_target_format=resolved_target_format,
                    trace_id=trace_id,
                    route_name=route_name,
                    client_ip=client_ip,
                )
            elif is_codex_model:
                result, status_code, failure_info = self._codex_proxy_service.proxy_request(
                    request_data,
                    headers,
                    on_complete=on_proxy_complete,
                    forward_stream_usage=client_requested_usage_chunk,
                    resolved_target_format=resolved_target_format,
                    trace_id=trace_id,
                    route_name=route_name,
                    client_ip=client_ip,
                )
            elif is_claude_model:
                result, status_code, failure_info = self._claude_proxy_service.proxy_request(
                    request_data,
                    headers,
                    on_complete=on_proxy_complete,
                    forward_stream_usage=client_requested_usage_chunk,
                    resolved_target_format=resolved_target_format,
                    trace_id=trace_id,
                    route_name=route_name,
                    client_ip=client_ip,
                )
            else:
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
                    details=failure_info.details,
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
                client_ip,
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

    def _resolve_model_route(self, model_name: str) -> tuple[Any | None, bool, bool, bool]:
        """按模型映射、Provider、OAuth 的优先级解析请求路由。"""
        if self._model_mapping_service is not None and self._model_mapping_service.has_mapping(model_name):
            return None, False, False, True

        provider = self._provider_manager.get_provider_for_model(model_name)
        if provider is not None:
            return provider, False, False, False

        if self._codex_proxy_service is not None and self._codex_proxy_service.has_model(model_name):
            return None, True, False, False
        if self._claude_proxy_service is not None and self._claude_proxy_service.has_model(model_name):
            return None, False, True, False
        return None, False, False, False

    def list_models(self) -> ResponseReturnValue:
        try:
            client_ip = self._get_request_client_ip()
            user, denial_response = self._get_authorized_user_for_request(
                client_ip,
                error_format="openai_chat",
            )
            if denial_response is not None:
                return denial_response
            api_key, denial_response = self._get_authorized_api_key_for_request(
                error_format="openai_chat",
            )
            if denial_response is not None:
                return denial_response

            model_names = list(self._list_available_model_names(visible_provider_models_only=True))
            if self._is_whitelist_required():
                allowed_models = set(
                    self._user_service.get_accessible_models_for_user(
                        user,
                        available_models=model_names,
                    )
                )
                model_names = [model_name for model_name in model_names if model_name in allowed_models]
            if self._is_api_key_required():
                allowed_models = set(
                    self._api_key_service.get_accessible_models_for_api_key(
                        api_key,
                        available_models=model_names,
                    )
                )
                model_names = [model_name for model_name in model_names if model_name in allowed_models]

            data = []
            codex_model_names = set()
            codex_image_model_names = set()
            if self._codex_proxy_service is not None:
                try:
                    codex_model_names = set(self._codex_proxy_service.list_model_names())
                    codex_image_model_names = set(self._codex_proxy_service.list_image_model_names())
                except Exception as exc:
                    self._logger.warning("Codex model list skipped: error=%s", exc)
            claude_model_names = set()
            if self._claude_proxy_service is not None:
                try:
                    claude_model_names = set(self._claude_proxy_service.list_model_names())
                except Exception as exc:
                    self._logger.warning("Claude model list skipped: error=%s", exc)
            mapping_model_names = set()
            image_mapping_model_names = set()
            if self._model_mapping_service is not None:
                try:
                    mapping_model_names = set(self._model_mapping_service.list_mapping_ids())
                    list_image_mapping_ids = getattr(self._model_mapping_service, "list_image_mapping_ids", None)
                    if callable(list_image_mapping_ids):
                        image_mapping_model_names = set(list_image_mapping_ids())
                except Exception as exc:
                    self._logger.warning("Model mapping list skipped: error=%s", exc)
            for model_key in model_names:
                if model_key in mapping_model_names:
                    target_formats = [
                        "openai_chat",
                        "openai_responses",
                        "claude_chat",
                    ]
                    if model_key in image_mapping_model_names:
                        target_formats.append("openai_images")
                    data.append(
                        {
                            "id": model_key,
                            "object": "model",
                            "owned_by": "model_mapping",
                            "provider_name": "model_mapping",
                            "source_format": "model_mapping",
                            "target_formats": target_formats,
                        }
                    )
                    continue
                if model_key in codex_model_names:
                    data.append(
                        {
                            "id": model_key,
                            "object": "model",
                            "owned_by": "openai",
                            "provider_name": CODEX_PROVIDER_NAME,
                            "source_format": "openai_responses",
                            "target_formats": [
                                "openai_chat",
                                "openai_responses",
                                "claude_chat",
                            ],
                        }
                    )
                    continue
                if model_key in codex_image_model_names:
                    data.append(
                        {
                            "id": model_key,
                            "object": "model",
                            "owned_by": "openai",
                            "provider_name": CODEX_PROVIDER_NAME,
                            "source_format": "openai_responses",
                            "target_formats": [
                                "openai_images",
                            ],
                            "capabilities": [
                                "image_generation",
                            ],
                        }
                    )
                    continue
                if model_key in claude_model_names:
                    data.append(
                        {
                            "id": model_key,
                            "object": "model",
                            "owned_by": "anthropic",
                            "provider_name": CLAUDE_PROVIDER_NAME,
                            "source_format": "claude_chat",
                            "target_formats": [
                                "openai_chat",
                                "openai_responses",
                                "claude_chat",
                            ],
                        }
                    )
                    continue
                provider_name, _, _ = str(model_key).partition("/")
                provider_view = self._provider_manager.get_provider_view(provider_name)
                source_format = getattr(provider_view, "source_format", None)
                target_formats = self._get_provider_target_formats(provider_view)
                data.append(
                    {
                        "id": model_key,
                        "object": "model",
                        "owned_by": provider_name or "proxy",
                        "provider_name": provider_name or "proxy",
                        "source_format": source_format,
                        "target_formats": list(target_formats),
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

    def _proxy_model_mapping_image_request(
        self,
        *,
        mapping_id: str,
        request_data: dict[str, Any],
        request_headers: dict[str, str],
        action: str,
        on_complete: Any,
        trace_id: str,
        route_name: str,
        client_ip: str,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        """调用映射中的 Codex 图片目标，并在失败后执行粘滞切换。"""
        if self._model_mapping_service is None:
            failure = ProxyErrorInfo(
                message="Model mapping service is not configured",
                status_code=503,
                error_type="upstream_error",
                error_code="model_mapping_unavailable",
            )
            return None, failure.status_code, failure

        excluded_targets: set[str] = set()
        last_failure: ProxyErrorInfo | None = None
        while True:
            try:
                selection = self._model_mapping_service.acquire_target(mapping_id, excluded_targets)
            except ValueError as exc:
                if last_failure is not None:
                    return None, last_failure.status_code, last_failure
                failure = ProxyErrorInfo(
                    message=str(exc),
                    status_code=503,
                    error_type="upstream_error",
                    error_code="model_mapping_targets_unavailable",
                )
                return None, failure.status_code, failure

            target_model_id = selection.target_model_id
            if getattr(selection, "is_fallback", False) and target_model_id in excluded_targets:
                if last_failure is not None:
                    return None, last_failure.status_code, last_failure
                failure = ProxyErrorInfo(
                    message=f"Mapped fallback target was already attempted: {target_model_id}",
                    status_code=503,
                    error_type="upstream_error",
                    error_code="model_mapping_targets_unavailable",
                )
                return None, failure.status_code, failure
            excluded_targets.add(target_model_id)
            target_request_data = dict(request_data)
            target_request_data["model"] = target_model_id

            def complete_mapped_image_request(meta: dict[str, Any]) -> None:
                self._model_mapping_service.record_success(selection)
                on_complete(dict(meta))

            try:
                if self._codex_proxy_service is not None and self._codex_proxy_service.has_image_model(target_model_id):
                    result, status_code, failure_info = self._codex_proxy_service.proxy_image_request(
                        target_request_data,
                        request_headers,
                        action=action,
                        on_complete=complete_mapped_image_request,
                        trace_id=trace_id,
                        route_name=route_name,
                        client_ip=client_ip,
                    )
                else:
                    failure = ProxyErrorInfo(
                        message=f"Mapped image target is unavailable: {target_model_id}",
                        status_code=503,
                        error_type="upstream_error",
                        error_code="model_mapping_image_target_unavailable",
                    )
                    result, status_code, failure_info = None, failure.status_code, failure
            except Exception as exc:
                self._logger.error("Mapped image target raised an exception: model=%s error=%s", target_model_id, exc)
                result = None
                status_code = 502
                failure_info = ProxyErrorInfo(
                    message=str(exc) or f"Mapped image target failed: {target_model_id}",
                    status_code=502,
                    error_type="upstream_error",
                    error_code="model_mapping_image_target_exception",
                )

            if result is not None and 200 <= status_code < 300:
                return result, status_code, failure_info

            response_headers = dict(result.headers) if result is not None else None
            if failure_info is not None and failure_info.response_headers:
                response_headers = failure_info.response_headers
            failure = failure_info or ProxyErrorInfo(
                message=self._extract_response_error_message(result)
                or f"Mapped image target returned HTTP {status_code}: {target_model_id}",
                status_code=status_code,
                error_type="upstream_error",
                error_code=f"http_{status_code}",
                response_headers=response_headers,
            )
            recorded_status_code = self._coerce_optional_int(
                (failure.details or {}).get("upstream_status") if failure.details else None
            )
            self._model_mapping_service.record_failure(
                selection,
                status_code=recorded_status_code if recorded_status_code is not None else status_code,
                error_type=failure.error_type,
                error_message=failure.message,
                response_headers=response_headers,
            )
            if result is not None:
                result.close()
            last_failure = failure

    def _proxy_model_mapping_request(
        self,
        *,
        mapping_id: str,
        request_data: dict[str, Any],
        request_headers: dict[str, str],
        on_complete: Any,
        forward_stream_usage: bool,
        resolved_target_format: str,
        trace_id: str,
        route_name: str,
        client_ip: str,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        """依次调用映射目标，并在目标自身候选耗尽后执行故障切换。"""
        if self._model_mapping_service is None:
            failure = ProxyErrorInfo(
                message="Model mapping service is not configured",
                status_code=503,
                error_type="upstream_error",
                error_code="model_mapping_unavailable",
            )
            return None, failure.status_code, failure

        excluded_targets: set[str] = set()
        last_failure: ProxyErrorInfo | None = None
        while True:
            try:
                selection = self._model_mapping_service.acquire_target(mapping_id, excluded_targets)
            except ValueError as exc:
                if last_failure is not None:
                    return None, last_failure.status_code, last_failure
                failure = ProxyErrorInfo(
                    message=str(exc),
                    status_code=503,
                    error_type="upstream_error",
                    error_code="model_mapping_targets_unavailable",
                )
                return None, failure.status_code, failure

            target_model_id = selection.target_model_id
            if getattr(selection, "is_fallback", False) and target_model_id in excluded_targets:
                if last_failure is not None:
                    return None, last_failure.status_code, last_failure
                failure = ProxyErrorInfo(
                    message=f"Mapped fallback target was already attempted: {target_model_id}",
                    status_code=503,
                    error_type="upstream_error",
                    error_code="model_mapping_targets_unavailable",
                )
                return None, failure.status_code, failure
            excluded_targets.add(target_model_id)
            target_request_data = dict(request_data)
            target_request_data["model"] = target_model_id
            stream_failed = False

            def record_stream_failure(meta: dict[str, Any]) -> None:
                nonlocal stream_failed
                stream_failed = True
                self._model_mapping_service.record_failure(
                    selection,
                    status_code=self._coerce_optional_int(meta.get("status_code")),
                    error_type=str(meta.get("error_type") or "upstream_stream_error"),
                    error_message=str(meta.get("error_message") or "Upstream stream failed"),
                )

            def complete_mapped_request(meta: dict[str, Any]) -> None:
                if stream_failed:
                    return
                self._model_mapping_service.record_success(selection)
                on_complete(dict(meta))

            try:
                result, status_code, failure_info = self._dispatch_completion_target(
                    target_model_id=target_model_id,
                    request_data=target_request_data,
                    request_headers=request_headers,
                    on_complete=complete_mapped_request,
                    on_stream_failure=record_stream_failure,
                    forward_stream_usage=forward_stream_usage,
                    resolved_target_format=resolved_target_format,
                    trace_id=trace_id,
                    route_name=route_name,
                    client_ip=client_ip,
                )
            except Exception as exc:
                self._logger.error("Mapped target raised an exception: model=%s error=%s", target_model_id, exc)
                result = None
                status_code = 502
                failure_info = ProxyErrorInfo(
                    message=str(exc) or f"Mapped target failed: {target_model_id}",
                    status_code=502,
                    error_type="upstream_error",
                    error_code="model_mapping_target_exception",
                )
            if result is not None and 200 <= status_code < 300:
                return result, status_code, failure_info

            response_headers = dict(result.headers) if result is not None else None
            if failure_info is not None and failure_info.response_headers:
                response_headers = failure_info.response_headers
            failure = failure_info or ProxyErrorInfo(
                message=self._extract_response_error_message(result)
                or f"Mapped target returned HTTP {status_code}: {target_model_id}",
                status_code=status_code,
                error_type="upstream_error",
                error_code=f"http_{status_code}",
                response_headers=response_headers,
            )
            recorded_status_code = self._coerce_optional_int(
                (failure.details or {}).get("upstream_status") if failure.details else None
            )
            self._model_mapping_service.record_failure(
                selection,
                status_code=recorded_status_code if recorded_status_code is not None else status_code,
                error_type=failure.error_type,
                error_message=failure.message,
                response_headers=response_headers,
            )
            if result is not None:
                result.close()
            last_failure = failure

    def _dispatch_completion_target(
        self,
        *,
        target_model_id: str,
        request_data: dict[str, Any],
        request_headers: dict[str, str],
        on_complete: Any,
        on_stream_failure: Any,
        forward_stream_usage: bool,
        resolved_target_format: str,
        trace_id: str,
        route_name: str,
        client_ip: str,
    ) -> tuple[Response | None, int, ProxyErrorInfo | None]:
        provider = self._provider_manager.get_provider_for_model(target_model_id)
        common_kwargs = {
            "on_complete": on_complete,
            "on_stream_failure": on_stream_failure,
            "forward_stream_usage": forward_stream_usage,
            "resolved_target_format": resolved_target_format,
            "trace_id": trace_id,
            "route_name": route_name,
            "client_ip": client_ip,
        }
        if provider is not None:
            return self._proxy_service.proxy_request(provider, request_data, request_headers, **common_kwargs)
        if self._codex_proxy_service is not None and self._codex_proxy_service.has_model(target_model_id):
            return self._codex_proxy_service.proxy_request(request_data, request_headers, **common_kwargs)
        if self._claude_proxy_service is not None and self._claude_proxy_service.has_model(target_model_id):
            return self._claude_proxy_service.proxy_request(request_data, request_headers, **common_kwargs)
        failure = ProxyErrorInfo(
            message=f"Mapped target is unavailable: {target_model_id}",
            status_code=503,
            error_type="upstream_error",
            error_code="model_mapping_target_unavailable",
        )
        return None, failure.status_code, failure

    @staticmethod
    def _coerce_optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_response_error_message(response: Response | None) -> str | None:
        """从映射目标的失败响应中提取可展示的错误内容。"""
        if response is None:
            return None
        payload = response.get_json(force=True, silent=True)
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message") not in (None, ""):
                return str(error["message"]).strip()[:1000]
            if error not in (None, "") and not isinstance(error, dict):
                return str(error).strip()[:1000]
            if payload.get("message") not in (None, ""):
                return str(payload["message"]).strip()[:1000]
        body = response.get_data(as_text=True).strip()
        return body[:1000] or None

    def _read_image_request_data(self, action: str) -> dict[str, Any]:
        """读取 OpenAI Images JSON 或 multipart 请求。"""
        content_type = str(request.content_type or "").lower()
        if "multipart/form-data" not in content_type:
            raw_request_data = request.get_json(silent=True)
            if raw_request_data is None:
                return {}
            if not isinstance(raw_request_data, dict):
                raise ValueError("Request body must be a JSON object")
            return dict(raw_request_data)

        payload: dict[str, Any] = {}
        for field in (
            "prompt",
            "model",
            "response_format",
            "size",
            "quality",
            "background",
            "output_format",
            "input_fidelity",
            "moderation",
            "output_compression",
            "partial_images",
            "stream",
        ):
            value = str(request.form.get(field) or "").strip()
            if value:
                payload[field] = value
        if "stream" in payload:
            payload["stream"] = str(payload["stream"]).strip().lower() == "true"
        if action == "edit":
            images = []
            for field in ("image", "images"):
                for uploaded_file in request.files.getlist(field):
                    data_url = self._uploaded_image_to_data_url(uploaded_file)
                    if data_url:
                        images.append({"image_url": data_url})
            if images:
                payload["images"] = images
            mask_files = request.files.getlist("mask")
            if mask_files:
                mask_url = self._uploaded_image_to_data_url(mask_files[0])
                if mask_url:
                    payload["mask"] = {"image_url": mask_url}
        return payload

    @staticmethod
    def _uploaded_image_to_data_url(uploaded_file: Any) -> str:
        filename = str(getattr(uploaded_file, "filename", "") or "").strip()
        if not filename:
            return ""
        content = uploaded_file.read()
        if not content:
            return ""
        mime_type = str(getattr(uploaded_file, "mimetype", "") or "").strip() or "application/octet-stream"
        encoded_content = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded_content}"

    @staticmethod
    def _filter_request_headers(headers: Any) -> dict[str, str]:
        excluded = {
            "authorization",
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
    def _copy_headers(headers: Any, *, redact_api_key: bool = False) -> dict[str, str]:
        copied_headers = {key: value for key, value in headers.items()}
        if not redact_api_key:
            return copied_headers

        for key in list(copied_headers):
            if key.lower() in {"authorization", "x-api-key"}:
                copied_headers[key] = "[redacted]"
        return copied_headers

    @staticmethod
    def _build_request_start_line(method: str, full_path: str) -> str:
        normalized_path = str(full_path or "").rstrip("?") or "/"
        return f"{str(method or 'POST').upper()} {normalized_path} HTTP/1.1"
