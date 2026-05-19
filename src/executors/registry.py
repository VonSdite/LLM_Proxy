#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Executor registry and built-in HTTP upstream executor."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..application.app_context import Logger
from ..external import (
    LLMProvider,
    probe_stream_response,
)
from ..proxy_core import resolve_stream_format
from ..utils.proxy_warning import request_with_proxy_warning_retry
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
        headers: dict[str, str],
        body: dict[str, Any],
        requested_stream: bool,
        timeout_seconds: int,
        verify_ssl: bool,
        request_proxies: dict[str, str] | None,
    ) -> OpenedUpstreamResponse:
        http_session = self._get_http_session()
        self._reset_http_session_state(http_session)
        request_options = {
            "proxies": request_proxies,
            "verify": verify_ssl,
        }
        upstream_response = request_with_proxy_warning_retry(
            lambda: http_session.post(
                provider.api,
                headers=headers,
                json=body,
                stream=requested_stream,
                timeout=timeout_seconds,
                allow_redirects=False,
                **request_options,
            ),
            request_options=request_options,
            confirm_session=http_session,
            logger=self.logger,
            log_context=f"provider={provider.name}",
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
    def _reset_http_session_state(session: requests.Session) -> None:
        session.cookies.clear()

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


class ExecutorRegistry:
    """Registry keyed by transport name."""

    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

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
    return registry
