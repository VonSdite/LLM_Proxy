#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理服务。"""

import threading
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from flask import Response, stream_with_context
from urllib3.util.retry import Retry

from ..application.app_context import AppContext
from ..external import LLMProvider
from ..hooks import HookContext


class _PrefetchedStreamResponse:
    def __init__(self, response: requests.Response, first_chunk: bytes):
        self._response = response
        self._first_chunk = first_chunk
        self.status_code = response.status_code
        self.headers = response.headers

    def iter_content(self, chunk_size: Optional[int] = None) -> Iterator[bytes]:
        if self._first_chunk:
            yield self._first_chunk
            self._first_chunk = b""
        yield from self._response.iter_content(chunk_size=chunk_size)

    def close(self) -> None:
        self._response.close()


class _BufferedUpstreamResponse:
    def __init__(self, response: requests.Response, body: bytes):
        self._response = response
        self.content = body
        self.status_code = response.status_code
        self.headers = response.headers

    def close(self) -> None:
        self._response.close()


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
        """代理请求到指定 provider，并执行重试与输出钩子。"""
        target_url = provider.api
        model_name = request_data["model"]

        timeout_seconds = provider.timeout_seconds
        max_retries = provider.max_retries
        verify_ssl = provider.verify_ssl

        def build_request(attempt: int) -> Tuple[Dict[str, str], Dict[str, Any], HookContext]:
            """构建重试请求的 headers/body/context。"""
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
            http_session: Optional[requests.Session] = None
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
                    response_for_hook, is_stream = self._probe_stream_response(upstream_response)
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

                response = provider.apply_output_body_hook(
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
    def _looks_like_sse_chunk(chunk: bytes) -> bool:
        if not chunk:
            return False
        text = chunk.decode("utf-8", errors="ignore").lstrip()
        return text.startswith("data:") or text.startswith("event:") or text.startswith(":")

    def _probe_stream_response(self, upstream_response: requests.Response) -> Tuple[Any, bool]:
        chunk_iter = upstream_response.iter_content(chunk_size=None)
        first_chunk = b""
        for chunk in chunk_iter:
            if chunk:
                first_chunk = chunk
                break

        if not first_chunk:
            return _BufferedUpstreamResponse(upstream_response, b""), False

        if self._looks_like_sse_chunk(first_chunk):
            return _PrefetchedStreamResponse(upstream_response, first_chunk), True

        remaining = b"".join(chunk_iter)
        return _BufferedUpstreamResponse(upstream_response, first_chunk + remaining), False

    @staticmethod
    def _filter_response_headers(headers: Any) -> Dict[str, str]:
        """过滤不应透传给客户端的 hop-by-hop 响应头。"""
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
