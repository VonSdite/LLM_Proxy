#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理请求 Trace 日志辅助。"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, Dict, Optional


class ProxyTraceLogger:
    """负责格式化并输出代理请求 Trace 日志。"""

    def __init__(self, config_manager: Any, trace_logger: Any):
        self._config_manager = config_manager
        self._trace_logger = trace_logger

    def is_enabled(self, trace_id: Optional[str]) -> bool:
        return bool(trace_id) and self._config_manager.is_llm_request_debug_enabled()

    def log_entry(
        self,
        *,
        stage: str,
        trace_id: Optional[str],
        start_line: str,
        headers: Dict[str, Any],
        payload: Any,
        route_name: Optional[str] = None,
        client_ip: Optional[str] = None,
        provider_name: Optional[str] = None,
        request_model: Optional[str] = None,
        upstream_model: Optional[str] = None,
        target_format: Optional[str] = None,
        status_code: Optional[int] = None,
        stream: Optional[bool] = None,
        attempt: Optional[int] = None,
        completed: Optional[bool] = None,
        error_type: Optional[str] = None,
        error_summary: Optional[str] = None,
    ) -> None:
        if not self.is_enabled(trace_id):
            return

        metadata_parts = [f"trace_id={trace_id}", f"stage={self._format_trace_stage(stage)}"]
        if route_name:
            metadata_parts.append(f"route={route_name}")
        if client_ip:
            metadata_parts.append(f"client_ip={client_ip}")
        if provider_name:
            metadata_parts.append(f"provider={provider_name}")
        if request_model:
            metadata_parts.append(f"request_model={request_model}")
        if upstream_model:
            metadata_parts.append(f"upstream_model={upstream_model}")
        if target_format:
            metadata_parts.append(f"target_format={target_format}")
        if status_code is not None:
            metadata_parts.append(f"status={status_code}")
        if stream is not None:
            metadata_parts.append(f"stream={str(stream).lower()}")
        if attempt is not None:
            metadata_parts.append(f"attempt={attempt}")
        if completed is not None:
            metadata_parts.append(f"completed={str(completed).lower()}")
        if error_type:
            metadata_parts.append(f"error_type={error_type}")
        if error_summary:
            metadata_parts.append(f"error_summary={error_summary}")

        message_parts = [
            f"[LLM TRACE] {' | '.join(metadata_parts)}",
            self._format_trace_http_block(start_line, headers, payload),
        ]
        self._trace_logger.info("\n".join(message_parts))

    @staticmethod
    def build_response_start_line(status_code: int, reason: Optional[str] = None) -> str:
        reason_phrase = str(reason or "").strip()
        if not reason_phrase:
            try:
                reason_phrase = HTTPStatus(int(status_code)).phrase
            except ValueError:
                reason_phrase = ""
        if reason_phrase:
            return f"HTTP/1.1 {status_code} {reason_phrase}"
        return f"HTTP/1.1 {status_code}"

    @staticmethod
    def coerce_trace_bytes(payload: Any) -> bytes:
        if isinstance(payload, bytes):
            return payload
        if payload is None:
            return b""
        return str(payload).encode("utf-8", errors="replace")

    @staticmethod
    def _format_trace_stage(stage: str) -> str:
        stage_map = {
            "downstream_request": "downstream_request(下游请求)",
            "upstream_request": "upstream_request(上游请求)",
            "upstream_response": "upstream_response(上游响应)",
            "downstream_response": "downstream_response(下游响应)",
        }
        normalized_stage = str(stage or "").strip().lower()
        return stage_map.get(normalized_stage, normalized_stage or "unknown")

    @classmethod
    def _format_trace_http_block(
        cls,
        start_line: str,
        headers: Dict[str, Any],
        payload: Any,
    ) -> str:
        normalized_headers = cls._normalize_trace_headers(headers)
        lines = [str(start_line or "").strip() or "<empty start-line>"]
        for key, value in normalized_headers.items():
            lines.append(f"{cls._format_trace_header_name(key)}: {value}")

        formatted_payload = cls._format_trace_body(payload)
        if formatted_payload:
            lines.extend(["", formatted_payload])
        return "\n".join(lines)

    @staticmethod
    def _format_trace_header_name(name: str) -> str:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return "<empty-header>"
        return "-".join(part.capitalize() for part in normalized_name.split("-"))

    @staticmethod
    def _normalize_trace_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for key, value in dict(headers or {}).items():
            normalized[str(key)] = ProxyTraceLogger._normalize_trace_scalar(value)
        return normalized

    @classmethod
    def _normalize_trace_payload(cls, payload: Any) -> Any:
        if isinstance(payload, dict):
            return {
                str(key): cls._normalize_trace_payload(value)
                for key, value in payload.items()
            }
        if isinstance(payload, (list, tuple)):
            return [cls._normalize_trace_payload(item) for item in payload]
        if isinstance(payload, bytes):
            return cls._decode_trace_body(payload)
        if isinstance(payload, str):
            return cls._decode_trace_body(payload.encode("utf-8"))
        return cls._normalize_trace_scalar(payload)

    @classmethod
    def _format_trace_body(cls, payload: Any) -> str:
        normalized_payload = cls._normalize_trace_payload(payload)
        if normalized_payload in (None, "", b""):
            return ""
        if isinstance(normalized_payload, (dict, list)):
            return json.dumps(normalized_payload, ensure_ascii=False, indent=2)
        return str(normalized_payload)

    @staticmethod
    def _normalize_trace_scalar(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _decode_trace_body(payload: bytes) -> Any:
        text = payload.decode("utf-8", errors="replace")
        stripped = text.strip()
        if not stripped:
            return text
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return text
