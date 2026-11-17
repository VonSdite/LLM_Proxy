"""观测最终下游响应的结果与性能，不改变响应内容或重试过程。"""

from __future__ import annotations

import codecs
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from ..proxy_core.usage import create_usage_meta, merge_usage_meta, public_usage_meta
from ..utils.local_time import now_local_datetime
from ..utils.request_metrics import output_tps
from .upstream_observation import attempt_recorder

LOGGER = logging.getLogger("app")


class RequestObservation:
    """一次客户端请求只提交一条记录，跨内部重试共享计时起点。"""

    def __init__(
        self,
        record: Callable[..., Any],
        *,
        started_at: float,
        start_time: Any,
        is_model_mapping: bool = False,
        capture: Any = None,
        **identity: Any,
    ):
        self.record = record
        self.capture = capture
        self.started_at = started_at
        self.start_time = start_time
        self.identity = identity
        self.is_model_mapping = is_model_mapping
        self.meta = create_usage_meta()
        self.first_at = None
        self.measurable = True
        self.terminal = False
        self.error_type = None
        self.status_code = None
        self.stream = False
        self.finished = False
        self.buffer = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.source_format = "openai_chat"
        self.upstream_attempts = []
        self.attempt_token = None

    def activate(self):
        self.attempt_token = attempt_recorder.set(self.record_attempt)

    def deactivate(self):
        if self.attempt_token is not None:
            attempt_recorder.reset(self.attempt_token)
            self.attempt_token = None

    def _call_capture(self, operation: str, *args: Any) -> None:
        capture = self.capture
        if capture is None:
            return
        try:
            getattr(capture, operation)(*args)
        except Exception as exc:
            self.capture = None
            LOGGER.warning("LLM recording capture disabled after %s failure: %s", operation, exc)

    def record_attempt(self, item):
        item = dict(item)
        self._call_capture("observe_attempt", dict(item))
        item.pop("actual_request_model", None)
        item.pop("upstream_protocol", None)
        if not self.is_model_mapping and item.get("target_model_id") == self.identity.get("request_model"):
            item["target_model_id"] = None
        self.upstream_attempts.append(item)

    def complete(self, meta: dict[str, Any]) -> None:
        self.meta.update(meta)

    def feed(self, chunk: bytes | str) -> None:
        """同一响应块内的事件使用相同时间，事件缓存有大小上限。"""
        received_at = time.perf_counter()
        self.buffer += self.decoder.decode(chunk) if isinstance(chunk, bytes) else chunk
        self.buffer = self.buffer.replace("\r\n", "\n")
        while "\n\n" in self.buffer:
            event, self.buffer = self.buffer.split("\n\n", 1)
            data = "\n".join(line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:"))
            if data == "[DONE]":
                self.terminal = True
                continue
            try:
                payload = json.loads(data)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict):
                self.observe_payload(payload, received_at)
        if len(self.buffer) > 1_048_576:
            self.buffer = ""
            self.measurable = False

    def observe_payload(self, payload: dict[str, Any], received_at: float) -> None:
        kind = payload.get("type", "")
        response = payload.get("response")
        status = response.get("status") if isinstance(response, dict) else payload.get("status")
        if payload.get("error") or kind in {"error", "response.failed"} or status == "failed":
            self.error_type = "stream_error"
        if kind in {"message_stop", "response.completed", "response.failed", "response.incomplete"}:
            self.terminal = True
        merge_usage_meta(self.meta, payload, source_format=self.source_format)
        content = False
        reasoning = False
        for choice in payload.get("choices", []) or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                continue
            reasoning |= bool(delta.get("reasoning_content") or delta.get("reasoning"))
            content |= bool(delta.get("content")) or reasoning
            for call in delta.get("tool_calls", []) or []:
                if isinstance(call, dict):
                    content |= bool((call.get("function") or {}).get("arguments"))
            content |= bool((delta.get("function_call") or {}).get("arguments"))
        delta = payload.get("delta")
        if kind == "content_block_delta" and isinstance(delta, dict):
            reasoning |= bool(delta.get("thinking"))
            content |= bool(delta.get("text") or delta.get("thinking") or delta.get("partial_json"))
        if kind in {
            "response.output_text.delta",
            "response.function_call_arguments.delta",
            "response.custom_tool_call_input.delta",
            "response.refusal.delta",
        }:
            content |= bool(delta)
        if kind in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
            reasoning |= bool(delta)
            content |= bool(delta)
        if content:
            if self.first_at is None:
                self.first_at = received_at

    def finish(self, outcome: str, *, status_code: int | None = None, error_type: str | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        if status_code is not None:
            self.status_code = status_code
        if error_type:
            self.error_type = error_type
        if self.error_type or (self.status_code is not None and self.status_code >= 400):
            outcome = "error"
        self._call_capture("finish", outcome)
        usage = public_usage_meta(self.meta)
        duration_ms = max(time.perf_counter() - self.started_at, 0) * 1000
        tps, tps_unavailable_reason = output_tps(
            usage.get("completion_tokens"),
            duration_ms,
            usage_status=usage.get("usage_status"),
            outcome=outcome,
        )
        fields = {
            key: usage.get(key)
            for key in (
                "response_model",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "usage_status",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "cache_usage_status",
                "target_model_id",
                "auth_file_name",
                "auth_account_id",
                "estimated_cost_usd",
            )
        }
        self.record(
            **self.identity,
            **fields,
            start_time=self.start_time,
            end_time=now_local_datetime(),
            outcome=outcome,
            http_status=self.status_code,
            upstream_attempts=self.upstream_attempts,
            error_type=self.error_type,
            is_stream=self.stream,
            duration_ms=duration_ms,
            first_token_latency_ms=(
                round(max(self.first_at - self.started_at, 0) * 1000, 2)
                if self.first_at is not None and self.measurable
                else None
            ),
            tps=tps,
            tps_unavailable_reason=tps_unavailable_reason,
        )

    def wrap(self, response: Any, source_format: str) -> Any:
        self.status_code = response.status_code
        self.source_format = source_format
        self.stream = "text/event-stream" in response.content_type.lower()
        if not self.stream:
            self._call_capture("feed", response.get_data())
            payload = response.get_json(silent=True)
            if isinstance(payload, dict) and payload.get("error"):
                self.error_type = "response_error"
            self.finish("success")
            return response
        original = response.response
        original_closed = False

        def close_original():
            nonlocal original_closed
            if not original_closed:
                original_closed = True
                close = getattr(original, "close", None)
                if callable(close):
                    close()

        def close_response():
            try:
                close_original()
            finally:
                self.finish("success" if self.terminal else "cancelled")

        def generate():
            exhausted = False
            failed = False
            try:
                for chunk in original:
                    try:
                        self.feed(chunk)
                    except (TypeError, ValueError, AttributeError):
                        self.measurable = False
                    self._call_capture("feed", chunk)
                    yield chunk
                exhausted = True
            except GeneratorExit:
                raise
            except BaseException:
                failed = True
                raise
            finally:
                try:
                    close_original()
                finally:
                    self.finish(
                        "success" if exhausted or self.terminal else "cancelled",
                        error_type="stream_error" if failed else None,
                    )

        response.response = generate()
        response.call_on_close(close_response)
        return response
