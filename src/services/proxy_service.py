#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游 LLM 代理服务。"""

import threading
from typing import Any, Callable, Dict, Optional, Tuple

import requests
from flask import Response, stream_with_context
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..application.app_context import AppContext
from ..external import LLMProvider, build_proxy_response, probe_stream_response
from ..hooks import HookContext
from ..utils.net import build_requests_proxies


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
    ) -> Tuple[Optional[Response], int]:
        """代理请求到目标 provider，并处理重试与输出钩子。"""
        target_url = provider.api
        model_name = request_data["model"]

        timeout_seconds = provider.timeout_seconds
        max_retries = provider.max_retries
        verify_ssl = provider.verify_ssl
        request_proxies = build_requests_proxies(provider.proxy)

        def build_request(attempt: int) -> Tuple[Dict[str, str], Dict[str, Any], HookContext]:
            headers = dict(request_headers)
            headers["content-type"] = "application/json"

            if provider.api_key:
                headers["authorization"] = f"Bearer {provider.api_key}"

            request_ctx = HookContext(retry=attempt, root_path=self._root_path, logger=self._logger)
            headers = provider.apply_header_hook(request_ctx, headers)

            body = dict(request_data)
            body = provider.apply_input_body_hook(request_ctx, body)
            body["model"] = model_name.rsplit("/", 1)[-1]
            return headers, body, request_ctx

        for attempt in range(max_retries):
            headers, body, request_ctx = build_request(attempt)
            requested_stream = bool(body.get("stream", False))
            self._logger.info(
                "Proxying upstream request: provider=%s model=%s attempt=%s/%s stream=%s",
                provider.name,
                body.get("model"),
                attempt + 1,
                max_retries,
                requested_stream,
            )

            try:
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
                should_retry_status = self._should_retry_status_code(status_code)
                if should_retry_status and attempt < max_retries - 1:
                    self._logger.warning(
                        "Retryable upstream status (attempt %s/%s): provider=%s status=%s, retrying",
                        attempt + 1,
                        max_retries,
                        provider.name,
                        status_code,
                    )
                    upstream_response.close()
                    continue

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

                if requested_stream != upstream_stream:
                    self._logger.warning(
                        "Stream mode mismatch: requested_stream=%s, upstream_stream=%s",
                        requested_stream,
                        upstream_stream,
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
                )
                self._logger.info(
                    "Upstream request completed: provider=%s status=%s stream=%s",
                    provider.name,
                    status_code,
                    is_stream,
                )
                if is_stream:

                    def _close_upstream_resources() -> None:
                        response_for_hook.close()

                    response.call_on_close(_close_upstream_resources)
                else:
                    response_for_hook.close()
                return response, status_code
            except requests.exceptions.RequestException as exc:
                self._logger.error(
                    "Request error (attempt %s/%s): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )
                if attempt < max_retries - 1:
                    continue

        return None, 502

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
        return {k: v for k, v in headers.items() if k.lower() not in excluded}
