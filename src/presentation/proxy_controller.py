#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理控制器。"""

from typing import Any, Dict, Optional

from datetime import datetime
from flask import Response, jsonify, request

from ..application.app_context import AppContext
from ..config import ConfigManager, ProviderManager
from ..hooks import HookAbortError
from ..services import LogService, ProxyService, UserService
from ..services.proxy_service import ProxyErrorInfo
from ..utils import normalize_ip


class ProxyController:
    """处理模型代理相关路由。"""

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
        """注册代理路由。"""
        self._app.route("/v1/chat/completions", methods=["POST"])(self.chat_completions)
        self._app.route("/v1/models", methods=["GET"])(self.list_models)

    def _get_user_by_ip(self, ip_address: str) -> Optional[Dict[str, Any]]:
        """按 IP 查询白名单用户。"""
        return self._user_service.get_user_by_ip(ip_address, require_whitelist_access=True)

    def _is_whitelist_required(self) -> bool:
        """读取是否开启白名单访问控制。"""
        return self._config_manager.is_chat_whitelist_enabled()

    @staticmethod
    def _build_error_payload(
        message: str,
        *,
        error_type: str,
        code: Optional[str] = None,
    ) -> Dict[str, Any]:
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
    ) -> tuple[Response, int]:
        return (
            jsonify(self._build_error_payload(message, error_type=error_type, code=code)),
            status_code,
        )

    def chat_completions(self) -> Response:
        """代理上游 chat/completions 请求。"""
        try:
            client_ip = normalize_ip(request.remote_addr)
            self._logger.info(f"Proxy request received: ip={client_ip}")
            whitelist_required = self._is_whitelist_required()
            user = self._get_user_by_ip(client_ip) if whitelist_required else None
            if whitelist_required and not user:
                self._logger.warning(f"Proxy denied: ip={client_ip} is not in whitelist")
                return self._error_response(
                    f"IP address {client_ip} is not in whitelist",
                    403,
                    error_type="permission_error",
                    code="ip_not_whitelisted",
                )

            request_data = request.get_json(silent=True)
            if not request_data or "model" not in request_data:
                self._logger.warning("Proxy rejected: missing model in request body")
                return self._error_response(
                    "Missing 'model' in request body",
                    400,
                    error_type="invalid_request_error",
                    code="missing_model",
                )

            stream_options = request_data.get("stream_options")
            client_requested_usage_chunk = isinstance(stream_options, dict) and (
                stream_options.get("include_usage") is True
            )
            if not isinstance(stream_options, dict):
                stream_options = {}
            if stream_options.get("include_usage") is not True:
                stream_options["include_usage"] = True
            request_data["stream_options"] = stream_options

            model_name = request_data["model"]
            provider = self._provider_manager.get_provider_for_model(model_name)
            if not provider:
                self._logger.warning(f"Proxy rejected: unknown model={model_name!r}")
                return self._error_response(
                    f"Unknown model: {model_name}",
                    400,
                    error_type="invalid_request_error",
                    code="unknown_model",
                )

            headers = self._filter_request_headers(request.headers)
            start_time = datetime.now()

            def on_proxy_complete(response_meta: Dict[str, Any]) -> None:
                self._logger.info(
                    "Proxy completed: model=%s response_model=%s total_tokens=%s ip=%s",
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
                    end_time=datetime.now(),
                    ip_address=client_ip,
                )

            result, status_code, failure_info = self._proxy_service.proxy_request(
                provider,
                request_data,
                headers,
                on_complete=on_proxy_complete,
                forward_stream_usage=client_requested_usage_chunk,
            )
            if result is None:
                failure_info = failure_info or ProxyErrorInfo(
                    message="Upstream request failed after retries",
                    status_code=status_code,
                    error_type="upstream_error",
                    error_code="upstream_request_failed",
                )
                self._logger.error(
                    "Proxy failed after retries: model=%s ip=%s status=%s upstream_error=%s",
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
                )

            return result
        except HookAbortError as exc:
            self._logger.warning(
                "Proxy blocked by hook: ip=%s status=%s type=%s message=%s",
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
            )
        except Exception as exc:
            self._logger.error(f"Error in chat_completions: {exc}")
            return self._error_response(
                str(exc),
                500,
                error_type="server_error",
                code="internal_error",
            )

    def list_models(self) -> Response:
        """返回当前可用模型列表。"""
        try:
            data = [{"id": model_key} for model_key in self._provider_manager.list_model_names()]
            return jsonify({"object": "list", "data": data})
        except Exception as exc:
            self._logger.error(f"Error listing models: {exc}")
            return self._error_response(
                str(exc),
                500,
                error_type="server_error",
                code="internal_error",
            )

    @staticmethod
    def _filter_request_headers(headers: Any) -> Dict[str, str]:
        """过滤不应转发到上游的 hop-by-hop 请求头。"""
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
