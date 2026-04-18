#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理下游响应构建辅助。"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterator, Optional

import requests
import websocket
from flask import Response, stream_with_context

from ..executors import OpenedUpstreamResponse
from ..external import LLMProvider
from ..hooks import HookAbortError, HookContext, HookErrorType
from ..proxy_core import (
    DownstreamChunk,
    decode_stream_events,
    encode_downstream_chunk,
    encode_downstream_response_body,
    is_terminal_chunk,
    should_emit_terminal_chunk,
)
from ..translators import Translator
from .proxy_trace_logger import ProxyTraceLogger


class ProxyResponseBuilder:
    """负责把上游响应转换为下游响应。"""

    def __init__(
        self,
        *,
        logger: Any,
        trace: ProxyTraceLogger,
        filter_response_headers: Callable[[Any], Dict[str, str]],
        extend_trace_buffer: Callable[[Optional[bytearray], Any], None],
    ) -> None:
        self._logger = logger
        self._trace = trace
        self._filter_response_headers = filter_response_headers
        self._extend_trace_buffer = extend_trace_buffer

    def build_stream_response(
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
        trace_id: Optional[str] = None,
        route_name: Optional[str] = None,
        client_ip: Optional[str] = None,
    ) -> Response:
        response = opened.response
        meta = self._create_empty_meta()
        completed = False
        terminal_sent = False
        trace_enabled = self._trace.is_enabled(trace_id)
        raw_response_headers = dict(getattr(response, "headers", {}) or {})
        upstream_payload_buffer = bytearray() if trace_enabled else None
        downstream_payload_buffer = bytearray() if trace_enabled else None
        downstream_headers = self._filter_response_headers(getattr(response, "headers", {}))
        downstream_headers["Content-Type"] = "text/event-stream; charset=utf-8"
        downstream_headers["Cache-Control"] = "no-cache"

        def safe_on_complete(
            *,
            error_type: Optional[HookErrorType] = None,
            error_message: Optional[str] = None,
            hook_abort: Optional[HookAbortError] = None,
        ) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            if finalize_attempt is not None:
                if hook_abort is not None:
                    if meta.get("response_model") is None:
                        meta["response_model"] = request_ctx.upstream_model or request_ctx.request_model
                    finalize_attempt(
                        status_code=opened.status_code,
                        error_message=hook_abort.message,
                        usage=meta,
                    )
                else:
                    finalize_attempt(
                        status_code=(opened.status_code if error_type is None else None),
                        error_type=error_type,
                        error_message=error_message,
                        usage=(meta if error_type is None else None),
                    )
            if on_complete and (error_type is None or hook_abort is not None):
                try:
                    on_complete(meta)
                except Exception as exc:
                    self._logger.error("Error in on_complete callback: %s", exc)

        def generate() -> Iterator[bytes]:
            nonlocal terminal_sent
            state: Dict[str, Any] = {}
            completion_error_type: Optional[HookErrorType] = None
            completion_trace_error_type: Optional[str] = None
            completion_error_message: Optional[str] = None
            completion_hook_abort: Optional[HookAbortError] = None
            try:
                upstream_chunks = self._iter_stream_chunks_with_trace(
                    response.iter_content(chunk_size=None),
                    upstream_payload_buffer,
                )
                for event in decode_stream_events(upstream_chunks, opened.stream_format):
                    downstream_chunks = translator.translate_stream_event(
                        request_ctx.upstream_model,
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
                                self._extend_trace_buffer(downstream_payload_buffer, encoded_terminal)
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
                            self._extend_trace_buffer(downstream_payload_buffer, encoded_chunk)
                            yield encoded_chunk

                if should_emit_terminal_chunk(downstream_target_format) and not terminal_sent:
                    encoded_terminal = encode_downstream_chunk(
                        DownstreamChunk(kind="done"),
                        downstream_target_format,
                    )
                    if encoded_terminal:
                        self._extend_trace_buffer(downstream_payload_buffer, encoded_terminal)
                        yield encoded_terminal
            except HookAbortError as exc:
                completion_hook_abort = exc
                completion_error_message = exc.message
                completion_trace_error_type = exc.error_type
                self._logger.warning(
                    "Stream aborted by hook: provider=%s type=%s status=%s message=%s",
                    provider.name,
                    exc.error_type,
                    exc.status_code,
                    exc.message,
                )
                for abort_chunk in self._build_stream_hook_abort_chunks(
                    request_ctx=request_ctx,
                    downstream_target_format=downstream_target_format,
                    message=exc.message,
                    error_type=exc.error_type,
                ):
                    if is_terminal_chunk(abort_chunk, downstream_target_format):
                        terminal_sent = True
                    encoded_chunk = encode_downstream_chunk(abort_chunk, downstream_target_format)
                    if encoded_chunk:
                        self._extend_trace_buffer(downstream_payload_buffer, encoded_chunk)
                        yield encoded_chunk
            except requests.exceptions.RequestException as exc:
                completion_error_type = self._classify_request_error(exc)
                completion_trace_error_type = completion_error_type.value
                completion_error_message = str(exc)
                self._logger.error("Streamed HTTP upstream error: provider=%s error=%s", provider.name, exc)
                raise
            except (websocket.WebSocketException, OSError) as exc:
                completion_error_type = self._classify_websocket_error(exc)
                completion_trace_error_type = completion_error_type.value
                completion_error_message = str(exc)
                self._logger.error("Streamed WebSocket upstream error: provider=%s error=%s", provider.name, exc)
                raise
            except Exception as exc:
                completion_error_type = HookErrorType.TRANSPORT_ERROR
                completion_trace_error_type = completion_error_type.value
                completion_error_message = str(exc)
                self._logger.error("Streamed upstream processing error: provider=%s error=%s", provider.name, exc)
                raise
            finally:
                try:
                    response.close()
                finally:
                    if trace_enabled:
                        self._trace.log_entry(
                            stage="upstream_response",
                            trace_id=trace_id,
                            start_line=self._trace.build_response_start_line(
                                opened.status_code,
                                getattr(response, "reason", None),
                            ),
                            headers=raw_response_headers,
                            payload=bytes(upstream_payload_buffer or b""),
                            route_name=route_name,
                            client_ip=client_ip,
                            provider_name=provider.name,
                            request_model=request_ctx.request_model,
                            upstream_model=request_ctx.upstream_model,
                            target_format=downstream_target_format,
                            status_code=opened.status_code,
                            stream=True,
                            completed=completion_trace_error_type is None,
                            error_type=completion_trace_error_type,
                        )
                        self._trace.log_entry(
                            stage="downstream_response",
                            trace_id=trace_id,
                            start_line=self._trace.build_response_start_line(opened.status_code),
                            headers=downstream_headers,
                            payload=bytes(downstream_payload_buffer or b""),
                            route_name=route_name,
                            client_ip=client_ip,
                            provider_name=provider.name,
                            request_model=request_ctx.request_model,
                            upstream_model=request_ctx.upstream_model,
                            target_format=downstream_target_format,
                            status_code=opened.status_code,
                            stream=True,
                            completed=completion_trace_error_type is None,
                            error_type=completion_trace_error_type,
                        )
                    safe_on_complete(
                        error_type=completion_error_type,
                        error_message=completion_error_message,
                        hook_abort=completion_hook_abort,
                    )

        return Response(
            stream_with_context(generate()),
            status=opened.status_code,
            headers=downstream_headers,
        )

    def build_nonstream_response(
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
        trace_id: Optional[str] = None,
        route_name: Optional[str] = None,
        client_ip: Optional[str] = None,
    ) -> Response:
        response = opened.response
        try:
            raw_body = self._read_response_body(response)
            raw_response_headers = dict(getattr(response, "headers", {}) or {})
            self._trace.log_entry(
                stage="upstream_response",
                trace_id=trace_id,
                start_line=self._trace.build_response_start_line(
                    opened.status_code,
                    getattr(response, "reason", None),
                ),
                headers=raw_response_headers,
                payload=raw_body,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider.name,
                request_model=request_ctx.request_model,
                upstream_model=request_ctx.upstream_model,
                target_format=downstream_target_format,
                status_code=opened.status_code,
                stream=False,
            )
            parsed_body = self._parse_json_bytes(raw_body)
            translated_payload = translator.translate_nonstream_response(
                request_ctx.upstream_model,
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
            self._trace.log_entry(
                stage="downstream_response",
                trace_id=trace_id,
                start_line=self._trace.build_response_start_line(opened.status_code),
                headers=headers,
                payload=response_body,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider.name,
                request_model=request_ctx.request_model,
                upstream_model=request_ctx.upstream_model,
                target_format=downstream_target_format,
                status_code=opened.status_code,
                stream=False,
            )
            return Response(
                response_body,
                status=opened.status_code,
                headers=headers,
            )
        finally:
            response.close()

    def consume_upstream_error(
        self,
        *,
        provider: LLMProvider,
        opened: OpenedUpstreamResponse,
        downstream_target_format: str,
        trace_id: Optional[str] = None,
        route_name: Optional[str] = None,
        client_ip: Optional[str] = None,
        request_model: Optional[str] = None,
        upstream_model: Optional[str] = None,
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
                f"{provider.source_format}->{downstream_target_format}",
                opened.status_code,
                opened.is_stream,
                summary or "<empty>",
            )
            headers = self._filter_response_headers(getattr(response, "headers", {}))
            if opened.content_type:
                headers["Content-Type"] = opened.content_type
            self._trace.log_entry(
                stage="upstream_response",
                trace_id=trace_id,
                start_line=self._trace.build_response_start_line(
                    opened.status_code,
                    getattr(response, "reason", None),
                ),
                headers=dict(getattr(response, "headers", {}) or {}),
                payload=body,
                route_name=route_name,
                client_ip=client_ip,
                provider_name=provider.name,
                request_model=request_model,
                upstream_model=upstream_model,
                target_format=downstream_target_format,
                status_code=opened.status_code,
                stream=opened.is_stream,
                error_summary=summary,
            )
            return body, headers, summary
        finally:
            response.close()

    def build_error_response(
        self,
        *,
        provider: LLMProvider,
        opened: OpenedUpstreamResponse,
        downstream_target_format: str,
        trace_id: Optional[str] = None,
        route_name: Optional[str] = None,
        client_ip: Optional[str] = None,
        request_model: Optional[str] = None,
        upstream_model: Optional[str] = None,
    ) -> tuple[Response, Optional[str]]:
        body, headers, summary = self.consume_upstream_error(
            provider=provider,
            opened=opened,
            downstream_target_format=downstream_target_format,
            trace_id=trace_id,
            route_name=route_name,
            client_ip=client_ip,
            request_model=request_model,
            upstream_model=upstream_model,
        )
        self._trace.log_entry(
            stage="downstream_response",
            trace_id=trace_id,
            start_line=self._trace.build_response_start_line(opened.status_code),
            headers=headers,
            payload=body,
            route_name=route_name,
            client_ip=client_ip,
            provider_name=provider.name,
            request_model=request_model,
            upstream_model=upstream_model,
            target_format=downstream_target_format,
            status_code=opened.status_code,
            stream=opened.is_stream,
            error_summary=summary,
        )
        return Response(body, status=opened.status_code, headers=headers), summary

    @staticmethod
    def _iter_stream_chunks_with_trace(
        upstream_chunks: Iterator[bytes],
        payload_buffer: Optional[bytearray],
    ) -> Iterator[bytes]:
        for chunk in upstream_chunks:
            if not chunk:
                continue
            yield chunk

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

    @staticmethod
    def _build_stream_hook_abort_chunks(
        *,
        request_ctx: HookContext,
        downstream_target_format: str,
        message: str,
        error_type: str,
    ) -> list[DownstreamChunk]:
        normalized_target_format = str(downstream_target_format or "").strip().lower()
        if normalized_target_format == "claude_chat":
            return [
                DownstreamChunk(
                    kind="json",
                    event="error",
                    payload={
                        "type": "error",
                        "error": {
                            "type": error_type,
                            "message": message,
                        },
                    },
                )
            ]
        if normalized_target_format == "openai_responses":
            return [
                DownstreamChunk(
                    kind="json",
                    event="response.failed",
                    payload={
                        "type": "response.failed",
                        "response": {
                            "id": f"hook_abort_{request_ctx.provider_name}",
                            "object": "response",
                            "status": "failed",
                            "error": {
                                "message": message,
                                "type": error_type,
                                "code": error_type,
                            },
                            "model": request_ctx.upstream_model or request_ctx.request_model,
                        },
                    },
                )
            ]
        return [
            DownstreamChunk(
                kind="json",
                payload={
                    "error": {
                        "message": message,
                        "type": error_type,
                        "param": None,
                        "code": error_type,
                    }
                },
            ),
            DownstreamChunk(kind="done"),
        ]

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
