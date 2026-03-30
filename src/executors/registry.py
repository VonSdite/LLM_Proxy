#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Executor registry and built-in transport executors."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
import websocket
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..application.app_context import Logger
from ..external import (
    LLMProvider,
    StaticUpstreamResponse,
    WebSocketUpstreamResponse,
    collect_websocket_response_body,
    probe_stream_response,
)
from ..external.stream_probe import BufferedUpstreamResponse
from ..proxy_core import resolve_stream_format
from ..utils.net import build_websocket_connect_options
from .contracts import Executor, OpenedUpstreamResponse


@dataclass
class HttpExecutor:
    logger: Logger
    transport: str = "http"

    def __post_init__(self) -> None:
        self._http_local = threading.local()

    def execute(
        self,
        provider: LLMProvider,
        headers: Dict[str, str],
        body: Dict[str, Any],
        requested_stream: bool,
        timeout_seconds: int,
        verify_ssl: bool,
        request_proxies: Optional[Dict[str, str]],
    ) -> OpenedUpstreamResponse:
        http_session = self._get_http_session()
        upstream_response = http_session.post(
            provider.api,
            headers=headers,
            json=body,
            stream=requested_stream,
            proxies=request_proxies,
            verify=verify_ssl,
            timeout=timeout_seconds,
        )

        status_code = upstream_response.status_code
        content_type = (upstream_response.headers.get("Content-Type") or "").lower()
        if not requested_stream:
            return OpenedUpstreamResponse(
                response=upstream_response,
                status_code=status_code,
                content_type=content_type,
                is_stream=False,
                stream_format="nonstream",
            )

        stream_format = resolve_stream_format(None, content_type, provider.transport)
        response_for_proxy: Any = upstream_response
        is_stream = stream_format != "nonstream"
        if stream_format == "nonstream":
            response_for_proxy, is_stream = probe_stream_response(upstream_response)
            if is_stream:
                stream_format = "sse_json"
                self.logger.warning(
                    "Stream mode probed for mismatch: provider=%s content_type=%s resolved_format=%s",
                    provider.name,
                    content_type,
                    stream_format,
                )

        return OpenedUpstreamResponse(
            response=response_for_proxy,
            status_code=status_code,
            content_type=content_type,
            is_stream=is_stream,
            stream_format=stream_format if is_stream else "nonstream",
        )

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


@dataclass(frozen=True)
class WebSocketExecutor:
    logger: Logger
    transport: str = "websocket"

    def execute(
        self,
        provider: LLMProvider,
        headers: Dict[str, str],
        body: Dict[str, Any],
        requested_stream: bool,
        timeout_seconds: int,
        verify_ssl: bool,
        request_proxies: Optional[Dict[str, str]],
    ) -> OpenedUpstreamResponse:
        del request_proxies
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
            return OpenedUpstreamResponse(
                response=response,
                status_code=status_code,
                content_type="application/json",
                is_stream=False,
                stream_format="nonstream",
            )

        connection.send(json.dumps(body, ensure_ascii=False))
        if requested_stream:
            response = WebSocketUpstreamResponse(connection)
            stream_format = resolve_stream_format(None, "", provider.transport)
            return OpenedUpstreamResponse(
                response=response,
                status_code=response.status_code,
                content_type=(response.headers.get("Content-Type") or "").lower(),
                is_stream=True,
                stream_format=stream_format,
            )

        response_body = collect_websocket_response_body(connection, self.logger)
        response = BufferedUpstreamResponse(
            StaticUpstreamResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                on_close=connection.close,
            ),
            response_body,
        )
        return OpenedUpstreamResponse(
            response=response,
            status_code=response.status_code,
            content_type="application/json",
            is_stream=False,
            stream_format="nonstream",
        )

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
    def _get_upstream_websocket_url(api: str) -> str:
        parsed = urlparse(api.strip())
        if parsed.scheme.lower() in {"ws", "wss"}:
            return parsed.geturl()
        if parsed.scheme.lower() == "http":
            return parsed._replace(scheme="ws").geturl()
        if parsed.scheme.lower() == "https":
            return parsed._replace(scheme="wss").geturl()
        raise ValueError("WebSocket upstream api must use ws://, wss://, http:// or https://")


class ExecutorRegistry:
    """Registry keyed by transport name."""

    def __init__(self) -> None:
        self._executors: Dict[str, Executor] = {}

    def register(self, executor: Executor) -> None:
        self._executors[str(executor.transport).strip().lower()] = executor

    def get(self, transport: str) -> Executor:
        key = str(transport or "").strip().lower()
        executor = self._executors.get(key)
        if executor is None:
            raise ValueError(f"Unsupported executor transport: {transport}")
        return executor


def build_default_executor_registry(logger: Logger) -> ExecutorRegistry:
    registry = ExecutorRegistry()
    registry.register(HttpExecutor(logger=logger))
    registry.register(WebSocketExecutor(logger=logger))
    return registry
