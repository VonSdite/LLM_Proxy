#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理下游响应构建辅助。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import requests
from flask import Response, stream_with_context

from ..executors import OpenedUpstreamResponse
from ..external import LLMProvider
from ..hooks import HookAbortError, HookContext, HookErrorType
from ..proxy_core import (
    DownstreamChunk,
    StreamEvent,
    decode_stream_events,
    encode_downstream_chunk,
    encode_downstream_response_body,
    is_terminal_chunk,
    should_emit_terminal_chunk,
)
from ..translators import Translator
from ..translators.stream_aggregator import (
    StreamAggregationError,
    aggregate_stream_to_native_response,
    infer_stream_aggregation_status_code,
)
from .proxy_trace_logger import ProxyTraceLogger


class _PrefetchedStreamIterator:
    """保存预取首块，并在下游首次拉取数据时标记流已提交。"""

    def __init__(
        self,
        first_chunk: bytes | None,
        stream: Iterator[bytes],
        on_started: Callable[[], None],
        on_cancelled: Callable[[], None],
    ) -> None:
        self._first_chunk = first_chunk
        self._stream = stream
        self._on_started = on_started
        self._on_cancelled = on_cancelled
        self._closed = False
        self._exhausted = False

    def __iter__(self) -> _PrefetchedStreamIterator:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        try:
            if self._first_chunk is not None:
                chunk = self._first_chunk
                self._first_chunk = None
            else:
                chunk = next(self._stream)
        except StopIteration:
            self._exhausted = True
            raise
        self._on_started()
        return chunk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._exhausted:
            self._on_cancelled()
        close = getattr(self._stream, "close", None)
        if callable(close):
            close()


class ProxyResponseBuilder:
    """负责把上游响应转换为下游响应。"""

    def __init__(
        self,
        *,
        logger: Any,
        trace: ProxyTraceLogger,
        filter_response_headers: Callable[[Any], dict[str, str]],
        extend_trace_buffer: Callable[[bytearray | None, Any], None],
    ) -> None:
        self._logger = logger
        self._trace = trace
        self._filter_response_headers = filter_response_headers
        self._extend_trace_buffer = extend_trace_buffer

    @staticmethod
    def create_streaming_response(
        stream: Iterator[bytes],
        *,
        status_code: int,
        headers: dict[str, str],
        on_started: Callable[[], None],
        on_cancelled: Callable[[], None] | None = None,
    ) -> Response:
        """预取首个下游块，并创建具备关闭传递能力的流式响应。"""
        try:
            first_chunk = next(stream)
        except StopIteration:
            first_chunk = None
        response_stream = _PrefetchedStreamIterator(
            first_chunk,
            stream,
            on_started,
            on_cancelled or (lambda: None),
        )
        try:
            response = Response(
                stream_with_context(response_stream),
                status=status_code,
                headers=headers,
            )
        except BaseException:
            response_stream.close()
            raise
        response.call_on_close(response_stream.close)
        return response

    def build_stream_response(
        self,
        *,
        provider: LLMProvider,
        translator: Translator,
        request_ctx: HookContext,
        downstream_target_format: str,
        original_request: dict[str, Any],
        translated_request: dict[str, Any],
        opened: OpenedUpstreamResponse,
        on_complete: Callable[[dict[str, Any]], None] | None,
        forward_stream_usage: bool,
        finalize_attempt: Callable[..., None] | None = None,
        trace_id: str | None = None,
        route_name: str | None = None,
        client_ip: str | None = None,
    ) -> Response:
        response = opened.response
        meta = self._create_empty_meta()
        completed = False
        terminal_sent = False
        downstream_started = False
        downstream_cancelled = False
        trace_enabled = self._trace.is_enabled(trace_id)
        raw_response_headers = dict(getattr(response, "headers", {}) or {})
        upstream_payload_buffer = bytearray() if trace_enabled else None
        downstream_payload_buffer = bytearray() if trace_enabled else None
        downstream_headers = self._filter_response_headers(getattr(response, "headers", {}))
        downstream_headers["Content-Type"] = "text/event-stream; charset=utf-8"
        downstream_headers["Cache-Control"] = "no-cache"

        def mark_downstream_started() -> None:
            nonlocal downstream_started
            downstream_started = True

        def mark_downstream_cancelled() -> None:
            nonlocal downstream_cancelled
            downstream_cancelled = True

        def safe_on_complete(
            *,
            outcome: str,
            error_type: HookErrorType | None = None,
            error_message: str | None = None,
            hook_abort: HookAbortError | None = None,
        ) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            if finalize_attempt is not None:
                try:
                    if hook_abort is not None:
                        if meta.get("response_model") is None:
                            meta["response_model"] = request_ctx.upstream_model or request_ctx.request_model
                        finalize_attempt(
                            status_code=opened.status_code,
                            error_message=hook_abort.message,
                            usage=meta,
                        )
                    elif outcome == "success":
                        finalize_attempt(status_code=opened.status_code, usage=meta)
                    elif error_type is not None:
                        finalize_attempt(
                            error_type=error_type,
                            error_message=error_message,
                        )
                    else:
                        finalize_attempt()
                except Exception as exc:
                    self._logger.error("Error finalizing upstream stream attempt: %s", exc)
            if on_complete and (outcome == "success" or hook_abort is not None):
                try:
                    on_complete(meta)
                except Exception as exc:
                    self._logger.error("Error in on_complete callback: %s", exc)

        def generate() -> Iterator[bytes]:
            nonlocal terminal_sent
            state: dict[str, Any] = {}
            completion_error_type: HookErrorType | None = None
            completion_trace_error_type: str | None = None
            completion_error_message: str | None = None
            completion_hook_abort: HookAbortError | None = None
            completion_outcome = "pending"

            def emit_downstream_chunks(downstream_chunks: list[DownstreamChunk]) -> Iterator[bytes]:
                nonlocal terminal_sent
                for downstream_chunk in downstream_chunks:
                    guarded_chunk = self._guard_stream_chunk(provider, request_ctx, downstream_chunk)
                    if guarded_chunk is None:
                        continue
                    terminal_chunk = is_terminal_chunk(guarded_chunk, downstream_target_format)
                    terminal_already_sent = terminal_sent
                    if guarded_chunk.kind == "done":
                        if terminal_already_sent:
                            continue
                        encoded_terminal = encode_downstream_chunk(guarded_chunk, downstream_target_format)
                        if encoded_terminal:
                            self._extend_trace_buffer(downstream_payload_buffer, encoded_terminal)
                            if terminal_chunk:
                                terminal_sent = True
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
                        if terminal_chunk:
                            terminal_sent = True
                        yield encoded_chunk

            def emit_stream_error(message: str, error_type: str) -> Iterator[bytes]:
                nonlocal terminal_sent
                for error_chunk in self.build_stream_error_chunks(
                    downstream_target_format=downstream_target_format,
                    message=message,
                    error_type=error_type,
                    response_id=f"stream_error_{provider.name}",
                    response_model=request_ctx.upstream_model or request_ctx.request_model,
                ):
                    terminal_chunk = is_terminal_chunk(error_chunk, downstream_target_format)
                    encoded_chunk = encode_downstream_chunk(error_chunk, downstream_target_format)
                    if encoded_chunk:
                        self._extend_trace_buffer(downstream_payload_buffer, encoded_chunk)
                        if terminal_chunk:
                            terminal_sent = True
                        yield encoded_chunk

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
                    self._update_meta_from_stream_state(meta, state)
                    yield from emit_downstream_chunks(downstream_chunks)

                if not terminal_sent:
                    downstream_chunks = translator.translate_stream_event(
                        request_ctx.upstream_model,
                        original_request,
                        translated_request,
                        StreamEvent(kind="done", payload="[DONE]"),
                        state,
                    )
                    self._update_meta_from_stream_state(meta, state)
                    yield from emit_downstream_chunks(downstream_chunks)

                if should_emit_terminal_chunk(downstream_target_format) and not terminal_sent:
                    yield from emit_downstream_chunks([DownstreamChunk(kind="done")])
                completion_outcome = "success"
            except HookAbortError as exc:
                completion_outcome = "hook_abort"
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
                for abort_chunk in self.build_stream_error_chunks(
                    downstream_target_format=downstream_target_format,
                    message=exc.message,
                    error_type=exc.error_type,
                    response_id=f"hook_abort_{request_ctx.provider_name}",
                    response_model=request_ctx.upstream_model or request_ctx.request_model,
                ):
                    terminal_chunk = is_terminal_chunk(abort_chunk, downstream_target_format)
                    encoded_chunk = encode_downstream_chunk(abort_chunk, downstream_target_format)
                    if encoded_chunk:
                        self._extend_trace_buffer(downstream_payload_buffer, encoded_chunk)
                        if terminal_chunk:
                            terminal_sent = True
                        yield encoded_chunk
            except requests.exceptions.RequestException as exc:
                completion_outcome = "upstream_error"
                completion_error_type = self._classify_request_error(exc)
                completion_trace_error_type = completion_error_type.value
                completion_error_message = str(exc)
                if not downstream_started:
                    raise
                if terminal_sent:
                    completion_outcome = "success"
                    completion_error_type = None
                    completion_trace_error_type = None
                    completion_error_message = None
                    self._logger.warning(
                        "Upstream HTTP framing error ignored after terminal event: provider=%s route=%s "
                        "model=%s error=%s",
                        provider.name,
                        route_name or "<none>",
                        request_ctx.request_model,
                        exc,
                    )
                    return
                self._logger.error(
                    "Streamed HTTP upstream error: provider=%s route=%s model=%s attempt=%s "
                    "downstream_started=true error=%s",
                    provider.name,
                    route_name or "<none>",
                    request_ctx.request_model,
                    request_ctx.retry + 1,
                    exc,
                )
                if not terminal_sent:
                    yield from emit_stream_error(
                        "Upstream stream interrupted",
                        "upstream_stream_error",
                    )
            except OSError as exc:
                completion_outcome = "upstream_error"
                completion_error_type = HookErrorType.TRANSPORT_ERROR
                completion_trace_error_type = completion_error_type.value
                completion_error_message = str(exc)
                if not downstream_started:
                    raise
                if terminal_sent:
                    completion_outcome = "success"
                    completion_error_type = None
                    completion_trace_error_type = None
                    completion_error_message = None
                    self._logger.warning(
                        "Upstream socket error ignored after terminal event: provider=%s route=%s model=%s error=%s",
                        provider.name,
                        route_name or "<none>",
                        request_ctx.request_model,
                        exc,
                    )
                    return
                self._logger.error(
                    "Streamed HTTP upstream error: provider=%s route=%s model=%s attempt=%s "
                    "downstream_started=true error=%s",
                    provider.name,
                    route_name or "<none>",
                    request_ctx.request_model,
                    request_ctx.retry + 1,
                    exc,
                )
                if not terminal_sent:
                    yield from emit_stream_error(
                        "Upstream stream interrupted",
                        "upstream_stream_error",
                    )
            except GeneratorExit:
                completion_outcome = "client_cancelled"
                completion_trace_error_type = "client_cancelled"
                completion_error_message = "Downstream client cancelled the stream"
                raise
            except Exception as exc:
                completion_outcome = "processing_error"
                completion_trace_error_type = "stream_processing_error"
                completion_error_message = str(exc)
                if not downstream_started:
                    raise
                if terminal_sent:
                    completion_outcome = "success"
                    completion_error_type = None
                    completion_trace_error_type = None
                    completion_error_message = None
                    self._logger.warning(
                        "Upstream processing error ignored after terminal event: provider=%s route=%s "
                        "model=%s error=%s",
                        provider.name,
                        route_name or "<none>",
                        request_ctx.request_model,
                        exc,
                    )
                    return
                self._logger.error(
                    "Streamed upstream processing error: provider=%s route=%s model=%s attempt=%s error=%s",
                    provider.name,
                    route_name or "<none>",
                    request_ctx.request_model,
                    request_ctx.retry + 1,
                    exc,
                )
                if not terminal_sent:
                    yield from emit_stream_error(
                        "Upstream stream processing failed",
                        "upstream_stream_processing_error",
                    )
            finally:
                if downstream_cancelled and completion_outcome not in {
                    "upstream_error",
                    "processing_error",
                }:
                    completion_outcome = "client_cancelled"
                    completion_error_type = None
                    completion_trace_error_type = "client_cancelled"
                    completion_error_message = "Downstream client cancelled the stream"
                    completion_hook_abort = None
                try:
                    response.close()
                except Exception as exc:
                    self._logger.error("Error closing upstream stream response: %s", exc)
                try:
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
                            completed=completion_outcome == "success",
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
                            completed=completion_outcome == "success",
                            error_type=completion_trace_error_type,
                        )
                except Exception as exc:
                    self._logger.error("Error writing upstream stream trace: %s", exc)
                if completion_outcome == "success":
                    self._logger.info(
                        "Upstream stream completed: provider=%s route=%s model=%s status=%s",
                        provider.name,
                        route_name or "<none>",
                        request_ctx.request_model,
                        opened.status_code,
                    )
                elif completion_outcome == "client_cancelled":
                    self._logger.info(
                        "Downstream stream cancelled: provider=%s route=%s model=%s",
                        provider.name,
                        route_name or "<none>",
                        request_ctx.request_model,
                    )
                safe_on_complete(
                    outcome=completion_outcome,
                    error_type=completion_error_type,
                    error_message=completion_error_message,
                    hook_abort=completion_hook_abort,
                )

        return self.create_streaming_response(
            generate(),
            status_code=opened.status_code,
            headers=downstream_headers,
            on_started=mark_downstream_started,
            on_cancelled=mark_downstream_cancelled,
        )

    def build_aggregated_nonstream_response(
        self,
        *,
        provider: LLMProvider,
        translator: Translator,
        request_ctx: HookContext,
        downstream_target_format: str,
        original_request: dict[str, Any],
        translated_request: dict[str, Any],
        opened: OpenedUpstreamResponse,
        on_complete: Callable[[dict[str, Any]], None] | None,
        finalize_attempt: Callable[..., None] | None = None,
        return_error_response: bool = True,
        trace_id: str | None = None,
        route_name: str | None = None,
        client_ip: str | None = None,
    ) -> Response:
        """聚合上游流式响应，并以单个非流式响应返回给下游。"""
        response = opened.response
        trace_enabled = self._trace.is_enabled(trace_id)
        raw_response_headers = dict(getattr(response, "headers", {}) or {})
        upstream_payload_buffer = bytearray() if trace_enabled else None

        try:
            upstream_chunks = self._iter_stream_chunks_with_trace(
                response.iter_content(chunk_size=None),
                upstream_payload_buffer,
            )
            native_payload = aggregate_stream_to_native_response(
                source_format=translator.source_format,
                model_name=request_ctx.upstream_model,
                events=decode_stream_events(upstream_chunks, opened.stream_format),
            )
            translated_payload = translator.translate_nonstream_response(
                request_ctx.upstream_model,
                original_request,
                translated_request,
                native_payload,
            )
            guarded_payload = provider.apply_response_guard(request_ctx, translated_payload)
            body_to_send = translated_payload if guarded_payload is None else guarded_payload
            response_body = encode_downstream_response_body(body_to_send, downstream_target_format)
            headers = self._filter_response_headers(getattr(response, "headers", {}))
            headers["Content-Type"] = self._resolve_nonstream_content_type(body_to_send, opened.content_type)

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
                    completed=True,
                )
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
                    completed=True,
                )
            return Response(
                response_body,
                status=opened.status_code,
                headers=headers,
            )
        except StreamAggregationError as exc:
            if not return_error_response:
                raise
            return self._build_aggregated_error_response(
                provider=provider,
                opened=opened,
                request_ctx=request_ctx,
                downstream_target_format=downstream_target_format,
                status_code=502,
                error_type=exc.error_type,
                error_code=exc.error_code,
                message=exc.message,
                error_payload=exc.error_payload,
                finalize_attempt=finalize_attempt,
                finalize_status_code=infer_stream_aggregation_status_code(exc),
                trace_id=trace_id,
                route_name=route_name,
                client_ip=client_ip,
                raw_response_headers=raw_response_headers,
                upstream_payload_buffer=upstream_payload_buffer,
            )
        except HookAbortError as exc:
            return self._build_aggregated_error_response(
                provider=provider,
                opened=opened,
                request_ctx=request_ctx,
                downstream_target_format=downstream_target_format,
                status_code=exc.status_code,
                error_type=exc.error_type,
                error_code=exc.error_type,
                message=exc.message,
                error_payload=None,
                finalize_attempt=finalize_attempt,
                trace_id=trace_id,
                route_name=route_name,
                client_ip=client_ip,
                raw_response_headers=raw_response_headers,
                upstream_payload_buffer=upstream_payload_buffer,
            )
        except UnicodeError as exc:
            aggregation_error = StreamAggregationError.from_message(
                "Upstream stream contains invalid text",
                error_type="upstream_stream_processing_error",
            )
            if not return_error_response:
                raise aggregation_error from exc
            return self._build_aggregated_error_response(
                provider=provider,
                opened=opened,
                request_ctx=request_ctx,
                downstream_target_format=downstream_target_format,
                status_code=502,
                error_type="upstream_stream_processing_error",
                error_code="upstream_stream_processing_error",
                message="Upstream stream contains invalid text",
                error_payload=None,
                finalize_attempt=finalize_attempt,
                trace_id=trace_id,
                route_name=route_name,
                client_ip=client_ip,
                raw_response_headers=raw_response_headers,
                upstream_payload_buffer=upstream_payload_buffer,
            )
        finally:
            response.close()

    def _build_aggregated_error_response(
        self,
        *,
        provider: LLMProvider,
        opened: OpenedUpstreamResponse,
        request_ctx: HookContext,
        downstream_target_format: str,
        status_code: int,
        error_type: str,
        error_code: Any,
        message: str,
        error_payload: dict[str, Any] | None,
        finalize_attempt: Callable[..., None] | None,
        trace_id: str | None,
        route_name: str | None,
        client_ip: str | None,
        raw_response_headers: dict[str, Any],
        upstream_payload_buffer: bytearray | None,
        finalize_status_code: int | None = None,
    ) -> Response:
        """把尚未提交下游的聚合错误转换为目标协议的普通响应。"""
        payload = self._build_nonstream_error_payload(
            downstream_target_format=downstream_target_format,
            message=message,
            error_type=error_type,
            error_code=error_code,
            error_payload=error_payload,
        )
        response_body = encode_downstream_response_body(payload, downstream_target_format)
        headers = self._filter_response_headers(raw_response_headers)
        headers["Content-Type"] = "application/json; charset=utf-8"

        if finalize_attempt is not None:
            try:
                finalize_attempt(
                    status_code=finalize_status_code or status_code,
                    error_message=message,
                    response_headers=raw_response_headers,
                )
            except Exception as exc:
                self._logger.error("Error finalizing aggregated upstream error: %s", exc)

        self._trace.log_entry(
            stage="upstream_response",
            trace_id=trace_id,
            start_line=self._trace.build_response_start_line(
                opened.status_code,
                getattr(opened.response, "reason", None),
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
            completed=False,
            error_type=error_type,
        )
        self._trace.log_entry(
            stage="downstream_response",
            trace_id=trace_id,
            start_line=self._trace.build_response_start_line(status_code),
            headers=headers,
            payload=response_body,
            route_name=route_name,
            client_ip=client_ip,
            provider_name=provider.name,
            request_model=request_ctx.request_model,
            upstream_model=request_ctx.upstream_model,
            target_format=downstream_target_format,
            status_code=status_code,
            stream=False,
            completed=False,
            error_type=error_type,
        )
        return Response(response_body, status=status_code, headers=headers)

    @staticmethod
    def _build_nonstream_error_payload(
        *,
        downstream_target_format: str,
        message: str,
        error_type: str,
        error_code: Any,
        error_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized_target_format = str(downstream_target_format or "").strip().lower()
        resolved_message = str((error_payload or {}).get("message") or message)
        resolved_type = str((error_payload or {}).get("type") or error_type or "upstream_error")
        resolved_code = (error_payload or {}).get("code")
        if resolved_code in (None, ""):
            resolved_code = error_code
        if normalized_target_format == "claude_chat":
            return {
                "type": "error",
                "error": {
                    "type": resolved_type,
                    "message": resolved_message,
                },
            }
        error = {
            "message": resolved_message,
            "type": resolved_type,
            "param": (error_payload or {}).get("param"),
            "code": resolved_code,
        }
        return {"error": error}

    def build_nonstream_response(
        self,
        *,
        provider: LLMProvider,
        translator: Translator,
        request_ctx: HookContext,
        downstream_target_format: str,
        original_request: dict[str, Any],
        translated_request: dict[str, Any],
        opened: OpenedUpstreamResponse,
        on_complete: Callable[[dict[str, Any]], None] | None,
        finalize_attempt: Callable[..., None] | None = None,
        trace_id: str | None = None,
        route_name: str | None = None,
        client_ip: str | None = None,
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
        trace_id: str | None = None,
        route_name: str | None = None,
        client_ip: str | None = None,
        request_model: str | None = None,
        upstream_model: str | None = None,
    ) -> tuple[bytes, dict[str, str], str | None]:
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
        trace_id: str | None = None,
        route_name: str | None = None,
        client_ip: str | None = None,
        request_model: str | None = None,
        upstream_model: str | None = None,
    ) -> tuple[Response, str | None]:
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

    def _iter_stream_chunks_with_trace(
        self,
        upstream_chunks: Iterator[bytes],
        payload_buffer: bytearray | None,
    ) -> Iterator[bytes]:
        for chunk in upstream_chunks:
            if not chunk:
                continue
            self._extend_trace_buffer(payload_buffer, chunk)
            yield chunk

    def _guard_stream_chunk(
        self,
        provider: LLMProvider,
        request_ctx: HookContext,
        chunk: DownstreamChunk,
    ) -> DownstreamChunk | None:
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
    def build_stream_error_chunks(
        *,
        downstream_target_format: str,
        message: str,
        error_type: str,
        response_id: str,
        response_model: str,
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
                            "id": response_id,
                            "object": "response",
                            "status": "failed",
                            "error": {
                                "message": message,
                                "type": error_type,
                                "code": error_type,
                            },
                            "model": response_model,
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
    def _summarize_upstream_error(cls, raw_body: bytes, content_type: str) -> str | None:
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
    def _extract_error_message(cls, payload: Any) -> str | None:
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
    ) -> str | None:
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
    def _create_empty_meta() -> dict[str, Any]:
        return {
            "response_model": None,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    @staticmethod
    def _update_meta_from_payload(meta: dict[str, Any], payload: dict[str, Any]) -> None:
        model = payload.get("model")
        if model:
            meta["response_model"] = model
        if payload.get("modelVersion") is not None:
            meta["response_model"] = payload.get("modelVersion")
        message = payload.get("message")
        if isinstance(message, dict):
            if message.get("model") is not None:
                meta["response_model"] = message.get("model")
            if message.get("modelVersion") is not None:
                meta["response_model"] = message.get("modelVersion")
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

    @classmethod
    def _update_meta_from_stream_state(cls, meta: dict[str, Any], state: dict[str, Any]) -> None:
        """从 translator 内部状态补充不会显式出现在下游 payload 中的元数据。"""
        response_model = cls._extract_response_model_from_stream_state(state)
        if response_model:
            meta["response_model"] = response_model

    @classmethod
    def _extract_response_model_from_stream_state(cls, state: Any) -> str | None:
        if not isinstance(state, dict):
            return None

        candidate_paths = (
            ("response_model",),
            ("chat_state", "response_model"),
            ("claude_bridge", "model"),
            ("target_state", "claude_bridge", "model"),
        )
        for path in candidate_paths:
            value = cls._get_nested_mapping_value(state, *path)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _get_nested_mapping_value(payload: Any, *path: str) -> Any:
        current = payload
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

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
    def _parse_json_bytes(raw_body: bytes) -> Any | None:
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
