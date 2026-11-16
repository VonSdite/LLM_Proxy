"""在响应转换之前观测每次上游调用，独立于客户端请求的最终结果。"""

from __future__ import annotations

import codecs
import json
from contextvars import ContextVar
from dataclasses import replace

import requests

from .proxy_transport_gateway import ProxyTransportGateway

attempt_recorder = ContextVar("upstream_attempt_recorder", default=None)


class ObservedResponse:
    """透传原始响应；读取结束、错误和关闭共用幂等结果提交。"""

    def __init__(self, response, record, target_model, *, stream, stream_format="sse_json"):
        self.response = response
        self.record = record
        self.target_model = target_model
        self.stream = stream
        self.stream_format = stream_format
        self.done = False
        self.model = None
        self.buffer = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        if response.status_code >= 400:
            self.finish("error")
        elif response.status_code >= 300:
            self.finish("exception")

    def __getattr__(self, name):
        return getattr(self.response, name)

    def finish(self, outcome):
        if self.done:
            return
        self.done = True
        self.record(
            {
                "target_model_id": self.target_model,
                "response_model": self.model,
                "outcome": outcome,
                "http_status": self.response.status_code,
            }
        )

    def payload(self, payload):
        if not isinstance(payload, dict):
            return
        nested = payload.get("response") or payload.get("message") or {}
        if not isinstance(nested, dict):
            nested = {}
        model = payload.get("model") or nested.get("model")
        if isinstance(model, str):
            self.model = model
        kind = payload.get("type")
        if (
            payload.get("error")
            or nested.get("error")
            or kind in {"error", "response.failed"}
            or nested.get("status") == "failed"
            or payload.get("status") == "failed"
        ):
            self.finish("error")
        elif kind == "response.cancelled":
            self.finish("cancelled")
        elif kind in {"response.completed", "response.done", "response.incomplete", "message_stop"}:
            self.finish("success")
        elif self.stream_format == "ndjson" and payload.get("done") is True:
            self.finish("success")

    def event(self, event):
        data = (
            event.strip()
            if self.stream_format == "ndjson"
            else "\n".join(line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:")).strip()
        )
        if data == "[DONE]":
            self.finish("success")
            return
        try:
            self.payload(json.loads(data))
        except (ValueError, TypeError):
            pass

    def feed(self, chunk):
        if self.done or self.stream_format == "raw_text":
            return
        self.buffer += self.decoder.decode(chunk) if isinstance(chunk, bytes) else chunk
        self.buffer = self.buffer.replace("\r\n", "\n")
        separator = "\n" if self.stream_format == "ndjson" else "\n\n"
        while separator in self.buffer:
            event, self.buffer = self.buffer.split(separator, 1)
            self.event(event)
        if len(self.buffer) > 1_048_576:
            self.buffer = ""

    def end_stream(self):
        if self.buffer.strip():
            self.event(self.buffer)
        self.buffer = ""
        # 纯文本以正常 EOF 为终点；事件流需要协议终止事件。
        self.finish("success" if self.stream_format == "raw_text" else "exception")

    def iter_content(self, *args, **kwargs):
        try:
            for chunk in self.response.iter_content(*args, **kwargs):
                self.feed(chunk)
                yield chunk
        except (requests.exceptions.RequestException, OSError):
            self.finish("exception")
            raise
        else:
            self.end_stream()

    @property
    def content(self):
        try:
            value = self.response.content
        except (requests.exceptions.RequestException, OSError):
            self.finish("exception")
            raise
        if isinstance(value, (bytes, str)) and not self.done:
            if self.stream:
                self.feed(value)
                self.end_stream()
            else:
                try:
                    self.payload(json.loads(value))
                except (ValueError, TypeError):
                    self.finish("exception")
                else:
                    self.finish("success")
        return value

    @property
    def text(self):
        self.content
        return self.response.text

    def json(self, **kwargs):
        try:
            payload = self.response.json(**kwargs)
        except (requests.exceptions.RequestException, OSError, ValueError):
            self.finish("exception")
            raise
        self.payload(payload)
        self.finish("success")
        return payload

    def close(self):
        try:
            return self.response.close()
        finally:
            # 未读完即关闭可能来自客户端或内部处理，单列中止，不计上游失败。
            self.finish("cancelled")


def _open(call, target_model, *, opened):
    record = attempt_recorder.get()
    if record is None:
        return call()
    try:
        result = call()
    except (requests.exceptions.RequestException, OSError):
        record({"target_model_id": target_model, "response_model": None, "outcome": "exception", "http_status": None})
        raise
    if opened:
        result = ProxyTransportGateway.coerce_opened_response(result)
        return replace(
            result,
            response=ObservedResponse(
                result.response, record, target_model, stream=result.is_stream, stream_format=result.stream_format
            ),
        )
    stream = "text/event-stream" in str(result.headers.get("Content-Type", "")).lower()
    return ObservedResponse(result, record, target_model, stream=stream)


def observe_opened(call, target_model):
    return _open(call, target_model, opened=True)


def observe_response(call, target_model):
    return _open(call, target_model, opened=False)
