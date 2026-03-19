#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游 LLM 代理服务。"""

import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests
import websocket
from flask import Response, stream_with_context
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..application.app_context import AppContext
from ..external import (
    LLMProvider,
    StaticUpstreamResponse,
    WebSocketUpstreamResponse,
    build_proxy_response,
    collect_websocket_response_body,
    probe_stream_response,
)
from ..external.stream_probe import BufferedUpstreamResponse
from ..hooks import HookContext
from ..utils.net import build_requests_proxies, build_websocket_connect_options


@dataclass(frozen=True, slots=True)
class ProxyErrorInfo:
    """Structured failure information for locally generated proxy errors."""

    message: str
    status_code: int = 502
    error_type: str = "upstream_error"
    error_code: Optional[str] = None


class ProxyService:
    """处理上游 LLM 代理请求。"""

    def __init__(self, ctx: AppContext):
        self._logger = ctx.logger
        self._root_path = ctx.root_path
        self._http_local = threading.local()

    def proxy_request(
        self,
        provider: LLMProvider,
        request_data: Dict[str, Any],
        request_headers: Dict[str, str],
        on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
        forward_stream_usage: bool = False,
    ) -> Tuple[Optional[Response], int, Optional[ProxyErrorInfo]]:
        """代理请求到目标 provider，并处理重试与输出钩子。"""
        target_url = provider.api
        model_name = request_data["model"]

        timeout_seconds = provider.timeout_seconds
        max_retries = provider.max_retries
        verify_ssl = provider.verify_ssl
        request_proxies = build_requests_proxies(provider.proxy)
        last_error: Optional[ProxyErrorInfo] = None

        def build_request(attempt: int) -> Tuple[Dict[str, str], Dict[str, Any], HookContext]:
            headers = dict(request_headers)
            headers["content-type"] = "application/json"

            if provider.api_key:
                headers["authorization"] = f"Bearer {provider.api_key}"

            request_ctx = HookContext(retry=attempt, root_path=self._root_path, logger=self._logger)
            headers = provider.apply_header_hook(request_ctx, headers)

            body = dict(request_data)
            body = provider.apply_input_body_hook(request_ctx, body)
            body["model"] = self._get_upstream_model_name(provider.name, model_name)
            return headers, body, request_ctx

        for attempt in range(max_retries):
            headers, body, request_ctx = build_request(attempt)
            requested_stream = bool(body.get("stream", False))
            self._logger.info(
                "Proxying upstream request: provider=%s transport=%s model=%s attempt=%s/%s stream=%s",
                provider.name,
                provider.transport,
                body.get("model"),
                attempt + 1,
                max_retries,
                requested_stream,
            )

            try:
                response_for_hook, is_stream, status_code = self._open_upstream_response(
                    provider,
                    headers,
                    body,
                    requested_stream,
                    target_url,
                    request_proxies,
                    timeout_seconds,
                    verify_ssl,
                )

                upstream_error = None
                if status_code >= 400:
                    upstream_error = self._summarize_upstream_error(response_for_hook, is_stream)

                should_retry_status = self._should_retry_status_code(status_code)
                if should_retry_status and attempt < max_retries - 1:
                    self._logger.warning(
                        "Retryable upstream status (attempt %s/%s): provider=%s transport=%s status=%s error=%s, retrying",
                        attempt + 1,
                        max_retries,
                        provider.name,
                        provider.transport,
                        status_code,
                        upstream_error or "<empty>",
                    )
                    response_for_hook.close()
                    continue

                if requested_stream != is_stream:
                    self._logger.warning(
                        "Stream mode mismatch: requested_stream=%s upstream_stream=%s transport=%s",
                        requested_stream,
                        is_stream,
                        provider.transport,
                    )

                if status_code >= 400:
                    log_method = self._logger.warning if status_code < 500 else self._logger.error
                    log_method(
                        "Upstream returned error: provider=%s transport=%s status=%s stream=%s error=%s",
                        provider.name,
                        provider.transport,
                        status_code,
                        is_stream,
                        upstream_error or "<empty>",
                    )

                response = build_proxy_response(
                    provider.hook,
                    request_ctx,
                    response_for_hook,
                    is_stream,
                    self._filter_response_headers,
                    stream_with_context,
                    self._logger,
                    on_complete=on_complete,
                    forward_stream_usage=forward_stream_usage,
                )
                self._logger.info(
                    "Upstream request completed: provider=%s transport=%s status=%s stream=%s",
                    provider.name,
                    provider.transport,
                    status_code,
                    is_stream,
                )
                if is_stream:
                    response.call_on_close(response_for_hook.close)
                else:
                    response_for_hook.close()
                return response, status_code, None
            except requests.exceptions.RequestException as exc:
                last_error = self._build_transport_error_info("HTTP", exc, max_retries)
                self._logger.error(
                    "HTTP upstream request error (attempt %s/%s): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )
                if attempt < max_retries - 1:
                    continue
            except (websocket.WebSocketException, OSError) as exc:
                last_error = self._build_transport_error_info("WebSocket", exc, max_retries)
                self._logger.error(
                    "WebSocket upstream request error (attempt %s/%s): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )
                if attempt < max_retries - 1:
                    continue

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
    ) -> Tuple[Any, bool, int]:
        if provider.transport == "websocket":
            return self._open_websocket_upstream_response(
                provider,
                headers,
                body,
                requested_stream,
                timeout_seconds,
                verify_ssl,
            )

        return self._open_http_upstream_response(
            provider,
            headers,
            body,
            requested_stream,
            target_url,
            request_proxies,
            timeout_seconds,
            verify_ssl,
        )

    def _open_http_upstream_response(
        self,
        provider: LLMProvider,
        headers: Dict[str, str],
        body: Dict[str, Any],
        requested_stream: bool,
        target_url: str,
        request_proxies: Optional[Dict[str, str]],
        timeout_seconds: int,
        verify_ssl: bool,
    ) -> Tuple[Any, bool, int]:
        http_session = self._get_http_session()
        upstream_response = http_session.post(
            target_url,
            headers=headers,
            json=body,
            stream=requested_stream,
            proxies=request_proxies,
            verify=verify_ssl,
            timeout=timeout_seconds,
        )

        status_code = upstream_response.status_code
        content_type = (upstream_response.headers.get("Content-Type") or "").lower()
        upstream_stream = "text/event-stream" in content_type
        response_for_hook: Any = upstream_response
        if requested_stream and not upstream_stream:
            response_for_hook, is_stream = probe_stream_response(upstream_response)
            self._logger.warning(
                "Stream mode probed for mismatch: requested_stream=%s upstream_stream=%s probed_stream=%s content_type=%s",
                requested_stream,
                upstream_stream,
                is_stream,
                content_type,
            )
        else:
            is_stream = upstream_stream

        return response_for_hook, is_stream, status_code

    def _open_websocket_upstream_response(
        self,
        provider: LLMProvider,
        headers: Dict[str, str],
        body: Dict[str, Any],
        requested_stream: bool,
        timeout_seconds: int,
        verify_ssl: bool,
    ) -> Tuple[Any, bool, int]:
        websocket_url = self._get_upstream_websocket_url(provider.api)
        handshake_headers = self._build_websocket_handshake_headers(headers)

        try:
            connection = websocket.create_connection(
                websocket_url,
                timeout=timeout_seconds,
                header=handshake_headers,
                **build_websocket_connect_options(provider.proxy, verify_ssl),
            )
        except websocket.WebSocketBadStatusException as exc:
            status_code = int(getattr(exc, "status_code", 502) or 502)
            body_bytes = self._build_websocket_error_body(exc)
            response = BufferedUpstreamResponse(
                StaticUpstreamResponse(
                    status_code=status_code,
                    headers={"Content-Type": "application/json"},
                ),
                body_bytes,
            )
            return response, False, status_code

        connection.send(json.dumps(body, ensure_ascii=False))

        if requested_stream:
            response = WebSocketUpstreamResponse(connection)
            return response, True, response.status_code

        response_body = collect_websocket_response_body(connection, self._logger)
        response = BufferedUpstreamResponse(
            StaticUpstreamResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                on_close=connection.close,
            ),
            response_body,
        )
        return response, False, response.status_code

    @staticmethod
    def _build_websocket_handshake_headers(headers: Dict[str, str]) -> Dict[str, str]:
        excluded = {
            "accept",
            "content-length",
            "content-type",
            "connection",
            "upgrade",
        }
        return {
            key: value
            for key, value in headers.items()
            if key.lower() not in excluded
        }

    @staticmethod
    def _build_websocket_error_body(exc: websocket.WebSocketBadStatusException) -> bytes:
        response_body = getattr(exc, "resp_body", None)
        if isinstance(response_body, bytes) and response_body.strip():
            return response_body
        if isinstance(response_body, str) and response_body.strip():
            return response_body.encode("utf-8")

        payload = {
            "error": f"WebSocket upstream handshake failed: {exc}",
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _build_transport_error_info(
        transport: str,
        exc: Exception,
        max_retries: int,
    ) -> ProxyErrorInfo:
        attempt_label = "attempt" if max_retries == 1 else "attempts"
        return ProxyErrorInfo(
            message=f"{transport} upstream request failed after {max_retries} {attempt_label}: {exc}",
            status_code=502,
            error_type="upstream_error",
            error_code="upstream_request_failed",
        )

    @classmethod
    def _summarize_upstream_error(cls, response: Any, is_stream: bool) -> Optional[str]:
        if is_stream:
            return None

        raw_body = getattr(response, "content", None)
        if raw_body is None:
            return None

        if isinstance(raw_body, bytes):
            body_text = raw_body.decode("utf-8", errors="ignore").strip()
        else:
            body_text = str(raw_body).strip()

        if not body_text:
            return None

        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            return cls._truncate_error_text(body_text)

        message = cls._extract_error_message(payload)
        if message:
            return cls._truncate_error_text(message)
        return cls._truncate_error_text(json.dumps(payload, ensure_ascii=False))

    @classmethod
    def _extract_error_message(cls, payload: Any) -> Optional[str]:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return cls._join_error_parts(
                    error.get("message"),
                    error_type=error.get("type"),
                    error_code=error.get("code"),
                )
            if error not in (None, ""):
                return str(error)
            if payload.get("message") not in (None, ""):
                return str(payload.get("message"))
        return None

    @staticmethod
    def _join_error_parts(
        message: Any,
        *,
        error_type: Any = None,
        error_code: Any = None,
    ) -> Optional[str]:
        parts = []
        if message not in (None, ""):
            parts.append(str(message).strip())

        tags = []
        if error_type not in (None, ""):
            tags.append(f"type={error_type}")
        if error_code not in (None, ""):
            tags.append(f"code={error_code}")

        if tags:
            suffix = ", ".join(tags)
            if parts:
                parts[0] = f"{parts[0]} ({suffix})"
            else:
                parts.append(suffix)

        if not parts:
            return None
        return parts[0]

    @staticmethod
    def _truncate_error_text(text: str, limit: int = 1000) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def _get_upstream_model_name(provider_name: str, requested_model_name: str) -> str:
        prefix = f"{provider_name}/"
        if requested_model_name.startswith(prefix):
            return requested_model_name[len(prefix):]
        return requested_model_name

    @staticmethod
    def _get_upstream_websocket_url(api: str) -> str:
        parsed = urlparse(api.strip())
        if parsed.scheme.lower() in {"ws", "wss"}:
            return parsed.geturl()
        if parsed.scheme.lower() == "http":
            return parsed._replace(scheme="ws").geturl()
        if parsed.scheme.lower() == "https":
            return parsed._replace(scheme="wss").geturl()
        raise ValueError("WebSocket upstream api must use ws://, wss://, http:// or https://")

    @staticmethod
    def _should_retry_status_code(status_code: int) -> bool:
        retryable_status_codes = {408, 409, 425, 429, 500, 502, 503, 504}
        return status_code in retryable_status_codes

    def _get_http_session(self) -> requests.Session:
        session = getattr(self._http_local, "session", None)
        if session is None:
            session = self._build_http_session()
            self._http_local.session = session
        return session

    @staticmethod
    def _build_http_session() -> requests.Session:
        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=100,
            pool_maxsize=100,
            max_retries=Retry(total=0, connect=0, read=0, redirect=0, status=0),
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

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
        }
        return {key: value for key, value in headers.items() if key.lower() not in excluded}
