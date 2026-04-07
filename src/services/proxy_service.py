#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游 LLM 代理服务。"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

import requests
import websocket
from flask import Response, stream_with_context

from ..application.app_context import AppContext
from ..config.auth_group_manager import AuthGroupManager, AuthGroupSelectionError, SelectedAuthEntry
from ..executors import OpenedUpstreamResponse, build_default_executor_registry
from ..external import LLMProvider
from ..hooks import HookContext, HookErrorType
from ..proxy_core import (
    DownstreamChunk,
    decode_stream_events,
    encode_downstream_chunk,
    encode_downstream_response_body,
    is_terminal_chunk,
    should_emit_terminal_chunk,
)
from ..translators import Translator, build_default_translator_registry
from ..utils.compat import dataclass
from ..utils.net import build_requests_proxies


@dataclass(frozen=True, slots=True)
class ProxyErrorInfo:
    """Structured failure information for locally generated proxy errors."""

    message: str
    status_code: int = 502
    error_type: str = "upstream_error"
    error_code: Optional[str] = None


class ProxyService:
    """处理上游 LLM 代理请求。"""

    def __init__(self, ctx: AppContext, auth_group_manager: Optional[AuthGroupManager] = None):
        self._logger = ctx.logger
        self._root_path = ctx.root_path
        self._auth_group_manager = auth_group_manager
        self._executor_registry = build_default_executor_registry(self._logger)
        self._translator_registry = build_default_translator_registry()

    def proxy_request(
        self,
        provider: LLMProvider,
        request_data: Dict[str, Any],
        request_headers: Dict[str, str],
        on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
        forward_stream_usage: bool = False,
        resolved_target_format: Optional[str] = None,
    ) -> Tuple[Optional[Response], int, Optional[ProxyErrorInfo]]:
        """代理请求到目标 provider，并处理重试、格式转换与 guard。"""
        target_url = provider.api
        requested_model = request_data["model"]
        upstream_model = self._get_upstream_model_name(provider.name, requested_model)
        timeout_seconds = provider.timeout_seconds
        max_retries = provider.max_retries
        verify_ssl = provider.verify_ssl
        request_proxies = build_requests_proxies(provider.proxy)
        downstream_target_format = self._resolve_downstream_target_format(
            provider,
            resolved_target_format,
        )
        translator = self._translator_registry.get(provider.source_format, downstream_target_format)
        last_error: Optional[ProxyErrorInfo] = None
        previous_status_code: Optional[int] = None
        previous_error_type: Optional[HookErrorType] = None
        self._ensure_supported_target_format(downstream_target_format)

        def build_request(
            attempt: int,
            selected_auth: Optional[SelectedAuthEntry],
        ) -> Tuple[Dict[str, str], Dict[str, Any], Dict[str, Any], HookContext]:
            initial_stream = bool(request_data.get("stream", False))
            request_ctx = HookContext(
                retry=attempt,
                root_path=self._root_path,
                logger=self._logger,
                provider_name=provider.name,
                request_model=requested_model,
                upstream_model=upstream_model,
                provider_source_format=provider.source_format,
                provider_target_format=downstream_target_format,
                transport=provider.transport,
                stream=initial_stream,
                auth_group_name=(
                    selected_auth.auth_group_name
                    if selected_auth is not None
                    else provider.auth_group
                ),
                auth_entry_id=(selected_auth.entry_id if selected_auth is not None else None),
                last_status_code=previous_status_code,
                last_error_type=previous_error_type,
            )

            headers = dict(request_headers)
            headers["content-type"] = "application/json"
            if selected_auth is not None:
                headers.update(selected_auth.headers_mapping())
            elif provider.api_key:
                headers["authorization"] = f"Bearer {provider.api_key}"
            headers = provider.apply_header_hook(request_ctx, headers)

            guarded_body = provider.apply_request_guard(request_ctx, dict(request_data))
            guarded_stream = bool(guarded_body.get("stream", False))
            if guarded_stream != request_ctx.stream:
                request_ctx = replace(request_ctx, stream=guarded_stream)

            translated_body = translator.translate_request(upstream_model, guarded_body, guarded_stream)
            self._ensure_upstream_usage_capture(provider.source_format, translated_body, guarded_stream)
            return headers, guarded_body, translated_body, request_ctx

        for attempt in range(max_retries):
            selected_auth: Optional[SelectedAuthEntry] = None
            attempt_finalized = False

            def finalize_attempt(
                *,
                status_code: Optional[int] = None,
                error_type: Optional[HookErrorType] = None,
                error_message: Optional[str] = None,
                response_headers: Optional[Dict[str, Any]] = None,
                usage: Optional[Dict[str, Any]] = None,
            ) -> None:
                nonlocal attempt_finalized
                if attempt_finalized:
                    return
                attempt_finalized = True
                if self._auth_group_manager is None:
                    return
                self._auth_group_manager.finish(
                    selected_auth,
                    status_code=status_code,
                    error_type=error_type,
                    error_message=error_message,
                    response_headers=response_headers,
                    usage=usage,
                )

            try:
                if self._auth_group_manager is not None and provider.auth_group:
                    selected_auth = self._auth_group_manager.acquire(provider.auth_group)

                headers, guarded_body, translated_body, request_ctx = build_request(attempt, selected_auth)
                requested_stream = request_ctx.stream
                self._logger.info(
                    "Proxying upstream request: provider=%s transport=%s source=%s target=%s model=%s attempt=%s/%s stream=%s auth_group=%s auth_entry=%s",
                    provider.name,
                    provider.transport,
                    provider.source_format,
                    downstream_target_format,
                    translated_body.get("model"),
                    attempt + 1,
                    max_retries,
                    requested_stream,
                    request_ctx.auth_group_name or "<none>",
                    request_ctx.auth_entry_id or "<none>",
                )
                if self._auth_group_manager is not None:
                    self._auth_group_manager.mark_request_dispatched(selected_auth)

                opened = self._coerce_opened_response(
                    self._open_upstream_response(
                        provider,
                        headers,
                        translated_body,
                        requested_stream,
                        target_url,
                        request_proxies,
                        timeout_seconds,
                        verify_ssl,
                    )
                )

                if self._should_retry_status_code(opened.status_code) and attempt < max_retries - 1:
                    previous_status_code = opened.status_code
                    previous_error_type = None
                    raw_response_headers = dict(getattr(opened.response, "headers", {}) or {})
                    _, _, retry_summary = self._consume_upstream_error(
                        provider,
                        opened,
                        downstream_target_format,
                    )
                    finalize_attempt(
                        status_code=opened.status_code,
                        error_message=retry_summary,
                        response_headers=raw_response_headers,
                    )
                    self._logger.warning(
                        "Retryable upstream status (attempt %s/%s): provider=%s status=%s stream=%s, retrying",
                        attempt + 1,
                        max_retries,
                        provider.name,
                        opened.status_code,
                        opened.is_stream,
                    )
                    continue

                if requested_stream != opened.is_stream:
                    self._logger.warning(
                        "Stream mode mismatch: provider=%s requested_stream=%s upstream_stream=%s decoder=%s content_type=%s",
                        provider.name,
                        requested_stream,
                        opened.is_stream,
                        opened.stream_format,
                        opened.content_type,
                    )

                if opened.status_code >= 400:
                    raw_response_headers = dict(getattr(opened.response, "headers", {}) or {})
                    response, error_summary = self._build_error_response(
                        provider,
                        opened,
                        downstream_target_format,
                    )
                    finalize_attempt(
                        status_code=opened.status_code,
                        error_message=error_summary,
                        response_headers=raw_response_headers,
                    )
                    return response, opened.status_code, None

                if opened.is_stream:
                    response = self._build_stream_response(
                        provider=provider,
                        translator=translator,
                        request_ctx=request_ctx,
                        downstream_target_format=downstream_target_format,
                        original_request=guarded_body,
                        translated_request=translated_body,
                        opened=opened,
                        on_complete=on_complete,
                        forward_stream_usage=forward_stream_usage,
                        finalize_attempt=finalize_attempt,
                    )
                else:
                    response = self._build_nonstream_response(
                        provider=provider,
                        translator=translator,
                        request_ctx=request_ctx,
                        downstream_target_format=downstream_target_format,
                        original_request=guarded_body,
                        translated_request=translated_body,
                        opened=opened,
                        on_complete=on_complete,
                        finalize_attempt=finalize_attempt,
                    )

                self._logger.info(
                    "Upstream request completed: provider=%s transport=%s source=%s target=%s status=%s stream=%s",
                    provider.name,
                    provider.transport,
                    provider.source_format,
                    downstream_target_format,
                    opened.status_code,
                    opened.is_stream,
                )
                return response, opened.status_code, None
            except AuthGroupSelectionError as exc:
                last_error = ProxyErrorInfo(
                    message=exc.message,
                    status_code=exc.status_code,
                    error_type=exc.error_type,
                    error_code=exc.error_code,
                )
                self._logger.warning(
                    "Auth group unavailable: provider=%s auth_group=%s status=%s error=%s",
                    provider.name,
                    provider.auth_group or "<none>",
                    exc.status_code,
                    exc.message,
                )
                return None, exc.status_code, last_error
            except requests.exceptions.RequestException as exc:
                previous_status_code = None
                previous_error_type = self._classify_request_error(exc)
                last_error = self._build_transport_error_info("HTTP", exc, max_retries)
                self._logger.error(
                    "HTTP upstream request error (attempt %s/%s): provider=%s error=%s",
                    attempt + 1,
                    max_retries,
                    provider.name,
                    exc,
                )
                finalize_attempt(
                    error_type=previous_error_type,
                    error_message=str(exc),
                )
                if attempt < max_retries - 1:
                    continue
            except (websocket.WebSocketException, OSError) as exc:
                previous_status_code = None
                previous_error_type = self._classify_websocket_error(exc)
                last_error = self._build_transport_error_info("WebSocket", exc, max_retries)
                self._logger.error(
                    "WebSocket upstream request error (attempt %s/%s): provider=%s error=%s",
                    attempt + 1,
                    max_retries,
                    provider.name,
                    exc,
                )
                finalize_attempt(
                    error_type=previous_error_type,
                    error_message=str(exc),
                )
                if attempt < max_retries - 1:
                    continue
            except Exception:
                finalize_attempt()
                raise

        if last_error is None:
            last_error = ProxyErrorInfo(
                message="Upstream request failed after retries",
                status_code=502,
                error_type="upstream_error",
                error_code="upstream_request_failed",
            )
        return None, last_error.status_code, last_error

    def _build_stream_response(
        self,
        *,
        provider: LLMProvider,
        translator: Translator,
        request_ctx: HookContext,
        downstream_target_format: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        opened: OpenedUpstreamResponse,
        on_complete: Optional[Callable[[Dict[str, Any]], None]],
        forward_stream_usage: bool,
        finalize_attempt: Optional[Callable[..., None]] = None,
    ) -> Response:
        response = opened.response
        meta = self._create_empty_meta()
        completed = False
        terminal_sent = False

        def safe_on_complete(
            *,
            error_type: Optional[HookErrorType] = None,
            error_message: Optional[str] = None,
        ) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            if finalize_attempt is not None:
                finalize_attempt(
                    status_code=(opened.status_code if error_type is None else None),
                    error_type=error_type,
                    error_message=error_message,
                    usage=(meta if error_type is None else None),
                )
            if on_complete and error_type is None:
                try:
                    on_complete(meta)
                except Exception as exc:
                    self._logger.error("Error in on_complete callback: %s", exc)

        def generate() -> Iterator[bytes]:
            nonlocal terminal_sent
            state: Dict[str, Any] = {}
            completion_error_type: Optional[HookErrorType] = None
            completion_error_message: Optional[str] = None
            try:
                upstream_chunks = response.iter_content(chunk_size=None)
                for event in decode_stream_events(upstream_chunks, opened.stream_format):
                    downstream_chunks = translator.translate_stream_event(
                        self._get_upstream_model_name(provider.name, request_ctx.request_model),
                        original_request,
                        translated_request,
                        event,
                        state,
                    )
                    for downstream_chunk in downstream_chunks:
                        guarded_chunk = self._guard_stream_chunk(provider, request_ctx, downstream_chunk)
                        if guarded_chunk is None:
                            continue
                        if is_terminal_chunk(guarded_chunk, downstream_target_format):
                            terminal_sent = True
                        if guarded_chunk.kind == "done":
                            encoded_terminal = encode_downstream_chunk(guarded_chunk, downstream_target_format)
                            if encoded_terminal:
                                yield encoded_terminal
                            continue
                        if guarded_chunk.kind == "json" and isinstance(guarded_chunk.payload, dict):
                            self._update_meta_from_payload(meta, guarded_chunk.payload)
                        if (
                            downstream_target_format == "openai_chat"
                            and guarded_chunk.kind == "json"
                            and not forward_stream_usage
                            and self._is_usage_only_stream_chunk(guarded_chunk.payload)
                        ):
                            continue
                        encoded_chunk = encode_downstream_chunk(guarded_chunk, downstream_target_format)
                        if encoded_chunk:
                            yield encoded_chunk

                if should_emit_terminal_chunk(downstream_target_format) and not terminal_sent:
                    encoded_terminal = encode_downstream_chunk(
                        DownstreamChunk(kind="done"),
                        downstream_target_format,
                    )
                    if encoded_terminal:
                        yield encoded_terminal
            except requests.exceptions.RequestException as exc:
                completion_error_type = self._classify_request_error(exc)
                completion_error_message = str(exc)
                self._logger.error("Streamed HTTP upstream error: provider=%s error=%s", provider.name, exc)
                raise
            except (websocket.WebSocketException, OSError) as exc:
                completion_error_type = self._classify_websocket_error(exc)
                completion_error_message = str(exc)
                self._logger.error("Streamed WebSocket upstream error: provider=%s error=%s", provider.name, exc)
                raise
            except Exception as exc:
                completion_error_type = HookErrorType.TRANSPORT_ERROR
                completion_error_message = str(exc)
                self._logger.error("Streamed upstream processing error: provider=%s error=%s", provider.name, exc)
                raise
            finally:
                try:
                    response.close()
                finally:
                    safe_on_complete(
                        error_type=completion_error_type,
                        error_message=completion_error_message,
                    )

        headers = self._filter_response_headers(getattr(response, "headers", {}))
        headers["Content-Type"] = "text/event-stream; charset=utf-8"
        headers["Cache-Control"] = "no-cache"
        return Response(
            stream_with_context(generate()),
            status=opened.status_code,
            headers=headers,
        )

    def _build_nonstream_response(
        self,
        *,
        provider: LLMProvider,
        translator: Translator,
        request_ctx: HookContext,
        downstream_target_format: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        opened: OpenedUpstreamResponse,
        on_complete: Optional[Callable[[Dict[str, Any]], None]],
        finalize_attempt: Optional[Callable[..., None]] = None,
    ) -> Response:
        response = opened.response
        try:
            raw_body = self._read_response_body(response)
            parsed_body = self._parse_json_bytes(raw_body)
            translated_payload = translator.translate_nonstream_response(
                self._get_upstream_model_name(provider.name, request_ctx.request_model),
                original_request,
                translated_request,
                parsed_body if parsed_body is not None else raw_body,
            )
            guarded_payload = provider.apply_response_guard(request_ctx, translated_payload)
            body_to_send = translated_payload if guarded_payload is None else guarded_payload

            meta = self._create_empty_meta()
            if isinstance(body_to_send, dict):
                self._update_meta_from_payload(meta, body_to_send)
            if on_complete:
                try:
                    on_complete(meta)
                except Exception as exc:
                    self._logger.error("Error in on_complete callback: %s", exc)
            if finalize_attempt is not None:
                finalize_attempt(status_code=opened.status_code, usage=meta)

            response_body = encode_downstream_response_body(body_to_send, downstream_target_format)
            headers = self._filter_response_headers(getattr(response, "headers", {}))
            headers["Content-Type"] = self._resolve_nonstream_content_type(body_to_send, opened.content_type)
            return Response(
                response_body,
                status=opened.status_code,
                headers=headers,
            )
        finally:
            response.close()

    def _consume_upstream_error(
        self,
        provider: LLMProvider,
        opened: OpenedUpstreamResponse,
        target_format: Optional[str] = None,
    ) -> tuple[bytes, Dict[str, str], Optional[str]]:
        response = opened.response
        try:
            body = self._read_response_body(response)
            summary = self._summarize_upstream_error(body, opened.content_type)
            log_method = self._logger.warning if opened.status_code < 500 else self._logger.error
            log_method(
                "Upstream returned error: provider=%s transport=%s format=%s status=%s stream=%s error=%s",
                provider.name,
                provider.transport,
                f"{provider.source_format}->{self._resolve_downstream_target_format(provider, target_format)}",
                opened.status_code,
                opened.is_stream,
                summary or "<empty>",
            )
            headers = self._filter_response_headers(getattr(response, "headers", {}))
            if opened.content_type:
                headers["Content-Type"] = opened.content_type
            return body, headers, summary
        finally:
            response.close()

    def _build_error_response(
        self,
        provider: LLMProvider,
        opened: OpenedUpstreamResponse,
        target_format: Optional[str] = None,
    ) -> tuple[Response, Optional[str]]:
        body, headers, summary = self._consume_upstream_error(provider, opened, target_format)
        return Response(body, status=opened.status_code, headers=headers), summary

    def _guard_stream_chunk(
        self,
        provider: LLMProvider,
        request_ctx: HookContext,
        chunk: DownstreamChunk,
    ) -> Optional[DownstreamChunk]:
        if chunk.kind == "done":
            return chunk

        guarded_payload = provider.apply_response_guard(request_ctx, chunk.payload)
        payload = chunk.payload if guarded_payload is None else guarded_payload
        if isinstance(payload, DownstreamChunk):
            return payload
        if isinstance(payload, (dict, list)):
            return DownstreamChunk(kind="json", payload=payload, event=chunk.event)
        return DownstreamChunk(kind="text", payload=payload, event=chunk.event)

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
    ) -> OpenedUpstreamResponse:
        del target_url
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
    def _coerce_opened_response(result: Any) -> OpenedUpstreamResponse:
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
    def _summarize_upstream_error(cls, raw_body: bytes, content_type: str) -> Optional[str]:
        del content_type
        if not raw_body:
            return None

        body_text = raw_body.decode("utf-8", errors="ignore").strip()
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
    def _create_empty_meta() -> Dict[str, Any]:
        return {
            "response_model": None,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    @staticmethod
    def _update_meta_from_payload(meta: Dict[str, Any], payload: Dict[str, Any]) -> None:
        model = payload.get("model")
        if model:
            meta["response_model"] = model
        if payload.get("modelVersion") is not None:
            meta["response_model"] = payload.get("modelVersion")
        usage = payload.get("usage")
        usage_metadata = payload.get("usageMetadata")
        response = payload.get("response")
        if isinstance(response, dict):
            if response.get("model") is not None:
                meta["response_model"] = response.get("model")
            if response.get("modelVersion") is not None:
                meta["response_model"] = response.get("modelVersion")
            if isinstance(response.get("usage"), dict):
                usage = response.get("usage")
            if isinstance(response.get("usageMetadata"), dict):
                usage_metadata = response.get("usageMetadata")
        if isinstance(usage, dict):
            if usage.get("total_tokens") is not None:
                meta["total_tokens"] = int(usage.get("total_tokens") or 0)
            elif usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
                meta["total_tokens"] = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
            if usage.get("prompt_tokens") is not None:
                meta["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
            elif usage.get("input_tokens") is not None:
                meta["prompt_tokens"] = int(usage.get("input_tokens") or 0)
            if usage.get("completion_tokens") is not None:
                meta["completion_tokens"] = int(usage.get("completion_tokens") or 0)
            elif usage.get("output_tokens") is not None:
                meta["completion_tokens"] = int(usage.get("output_tokens") or 0)
            return
        if not isinstance(usage_metadata, dict):
            return
        meta["prompt_tokens"] = int(usage_metadata.get("promptTokenCount") or 0)
        meta["completion_tokens"] = int(usage_metadata.get("candidatesTokenCount") or 0)
        meta["total_tokens"] = int(
            usage_metadata.get("totalTokenCount") or (meta["prompt_tokens"] + meta["completion_tokens"])
        )

    @staticmethod
    def _is_usage_only_stream_chunk(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        if not isinstance(payload.get("usage"), dict):
            return False
        choices = payload.get("choices")
        return choices is None or (isinstance(choices, list) and len(choices) == 0)

    @staticmethod
    def _get_upstream_model_name(provider_name: str, requested_model_name: str) -> str:
        prefix = f"{provider_name}/"
        if requested_model_name.startswith(prefix):
            return requested_model_name[len(prefix):]
        return requested_model_name

    @staticmethod
    def _ensure_upstream_usage_capture(
        source_format: str,
        translated_body: Dict[str, Any],
        stream: bool,
    ) -> None:
        if str(source_format or "").strip().lower() != "openai_chat" or not stream:
            return
        stream_options = translated_body.get("stream_options")
        if not isinstance(stream_options, dict):
            stream_options = {}
        else:
            stream_options = dict(stream_options)
        if stream_options.get("include_usage") is not True:
            stream_options["include_usage"] = True
        translated_body["stream_options"] = stream_options

    @staticmethod
    def _ensure_supported_target_format(target_format: str) -> None:
        if str(target_format or "").strip().lower() not in {
            "openai_chat",
            "openai_responses",
            "claude_chat",
            "codex",
        }:
            raise ValueError(f"Unsupported downstream target_format: {target_format}")

    @staticmethod
    def _resolve_downstream_target_format(
        provider: LLMProvider,
        resolved_target_format: Optional[str] = None,
    ) -> str:
        normalized_target_format = str(resolved_target_format or "").strip().lower()
        if normalized_target_format:
            return normalized_target_format

        provider_target_formats = tuple(
            str(item or "").strip().lower()
            for item in getattr(provider, "target_formats", ())
            if str(item or "").strip()
        )
        if provider_target_formats:
            return provider_target_formats[0]

        # DEPRECATED compatibility path for legacy provider objects that still
        # rely on a single `target_format` field. New callers should only pass
        # `target_formats`, and this fallback can be removed later.
        return str(getattr(provider, "target_format", "") or "").strip().lower()

    @staticmethod
    def _read_response_body(response: Any) -> bytes:
        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            return content
        if isinstance(content, str):
            return content.encode("utf-8")
        return b"".join(response.iter_content(chunk_size=None))

    @staticmethod
    def _parse_json_bytes(raw_body: bytes) -> Optional[Any]:
        if not raw_body:
            return None
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _resolve_nonstream_content_type(payload: Any, upstream_content_type: str) -> str:
        if isinstance(payload, (dict, list)):
            return "application/json; charset=utf-8"
        return upstream_content_type or "application/octet-stream"

    @staticmethod
    def _should_retry_status_code(status_code: int) -> bool:
        retryable_status_codes = {408, 409, 425, 429, 500, 502, 503, 504}
        return status_code in retryable_status_codes

    @staticmethod
    def _classify_request_error(exc: requests.exceptions.RequestException) -> HookErrorType:
        if isinstance(exc, requests.exceptions.Timeout):
            return HookErrorType.TIMEOUT
        if isinstance(exc, requests.exceptions.ConnectionError):
            return HookErrorType.CONNECTION_ERROR
        return HookErrorType.TRANSPORT_ERROR

    @staticmethod
    def _classify_websocket_error(exc: Exception) -> HookErrorType:
        if isinstance(exc, websocket.WebSocketException):
            return HookErrorType.WEBSOCKET_ERROR
        return HookErrorType.TRANSPORT_ERROR

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
            "content-length",
            "content-encoding",
        }
        return {key: value for key, value in headers.items() if key.lower() not in excluded}
