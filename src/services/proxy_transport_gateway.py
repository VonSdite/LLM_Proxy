#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理上游传输网关。"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
import websocket

from ..executors import OpenedUpstreamResponse
from ..external import LLMProvider
from ..hooks import HookErrorType


class ProxyTransportGateway:
    """负责打开上游响应并归一化传输层错误。"""

    def __init__(self, executor_registry: Any):
        self._executor_registry = executor_registry

    def open_upstream_response(
        self,
        *,
        provider: LLMProvider,
        headers: Dict[str, str],
        body: Dict[str, Any],
        requested_stream: bool,
        request_proxies: Optional[Dict[str, str]],
        timeout_seconds: int,
        verify_ssl: bool,
    ) -> OpenedUpstreamResponse:
        executor = self._executor_registry.get(provider.transport)
        return executor.execute(
            provider=provider,
            headers=headers,
            body=body,
            requested_stream=requested_stream,
            timeout_seconds=timeout_seconds,
            verify_ssl=verify_ssl,
            request_proxies=request_proxies,
        )

    @staticmethod
    def coerce_opened_response(result: Any) -> OpenedUpstreamResponse:
        if isinstance(result, OpenedUpstreamResponse):
            return result
        if isinstance(result, tuple) and len(result) == 3:
            response, is_stream, status_code = result
            headers = getattr(response, "headers", {}) or {}
            content_type = (headers.get("Content-Type") or "").lower()
            return OpenedUpstreamResponse(
                response=response,
                status_code=int(status_code),
                content_type=content_type,
                is_stream=bool(is_stream),
                stream_format="sse_json" if is_stream else "nonstream",
            )
        raise TypeError(f"Unsupported upstream response result: {type(result)!r}")

    @staticmethod
    def build_transport_error_info(
        transport: str,
        exc: Exception,
        max_retries: int,
    ) -> tuple[HookErrorType, str]:
        attempt_label = "attempt" if max_retries == 1 else "attempts"
        return (
            ProxyTransportGateway.classify_request_or_socket_error(exc),
            f"{transport} upstream request failed after {max_retries} {attempt_label}: {exc}",
        )

    @staticmethod
    def should_retry_status_code(status_code: int) -> bool:
        return status_code in {408, 409, 425, 429, 500, 502, 503, 504}

    @staticmethod
    def classify_request_error(exc: requests.exceptions.RequestException) -> HookErrorType:
        if isinstance(exc, requests.exceptions.Timeout):
            return HookErrorType.TIMEOUT
        if isinstance(exc, requests.exceptions.ConnectionError):
            return HookErrorType.CONNECTION_ERROR
        return HookErrorType.TRANSPORT_ERROR

    @staticmethod
    def classify_websocket_error(exc: Exception) -> HookErrorType:
        if isinstance(exc, websocket.WebSocketException):
            return HookErrorType.WEBSOCKET_ERROR
        return HookErrorType.TRANSPORT_ERROR

    @staticmethod
    def classify_request_or_socket_error(exc: Exception) -> HookErrorType:
        if isinstance(exc, requests.exceptions.RequestException):
            return ProxyTransportGateway.classify_request_error(exc)
        return ProxyTransportGateway.classify_websocket_error(exc)

    @staticmethod
    def build_upstream_request_start_line(transport: str, target_url: str) -> str:
        normalized_transport = str(transport or "").strip().lower()
        if normalized_transport == "websocket":
            parsed = urlparse(str(target_url or "").strip())
            if parsed.scheme.lower() == "http":
                parsed = parsed._replace(scheme="ws")
            elif parsed.scheme.lower() == "https":
                parsed = parsed._replace(scheme="wss")
            return f"CONNECT {parsed.geturl()} HTTP/1.1"
        return f"POST {target_url} HTTP/1.1"
