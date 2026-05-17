#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex OAuth 模型代理服务。"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterator, Optional
from uuid import uuid4

import requests
from flask import Response, stream_with_context

from ..application.app_context import AppContext
from ..proxy_core import (
    DownstreamChunk,
    decode_stream_events,
    encode_downstream_chunk,
    encode_downstream_response_body,
    is_terminal_chunk,
    should_emit_terminal_chunk,
)
from ..utils.http_headers import merge_http_headers
from ..utils.net import build_requests_proxies
from .codex_oauth_service import (
    CODEX_USER_AGENT,
    CodexAuthCandidate,
    CodexOAuthService,
)
from .proxy_response_builder import ProxyResponseBuilder
from .proxy_service import ProxyErrorInfo


CODEX_BACKEND_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
# 当前 Codex 模型目录中 gpt-5.5 要求客户端版本不低于 0.124.0。
CODEX_CLIENT_VERSION = "0.124.0"
CODEX_PROVIDER_NAME = "codex"


class CodexProxyService:
    """使用本地 Codex OAuth 认证文件代理 Responses 风格模型。"""

    def __init__(self, ctx: AppContext, codex_oauth_service: CodexOAuthService):
        self._logger = ctx.logger
        self._config_manager = ctx.config_manager
        self._codex_oauth_service = codex_oauth_service
        from ..translators import build_default_translator_registry

        self._translator_registry = build_default_translator_registry()

    def has_model(self, model_name: str) -> bool:
        """判断 Codex OAuth 是否支持指定模型。"""
        return self._codex_oauth_service.has_model(model_name)

    def list_model_names(self) -> tuple[str, ...]:
        """返回 Codex OAuth 当前模型名。"""
        return self._codex_oauth_service.list_model_names()

    def proxy_request(
        self,
        request_data: Dict[str, Any],
        request_headers: Dict[str, str],
        on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
        forward_stream_usage: bool = False,
        resolved_target_format: Optional[str] = None,
        trace_id: Optional[str] = None,
        route_name: Optional[str] = None,
        client_ip: Optional[str] = None,
    ) -> tuple[Optional[Response], int, Optional[ProxyErrorInfo]]:
        """按账号配额顺序代理 Codex 请求。"""
        del trace_id
        model_name = str(request_data.get("model") or "").strip()
        target_format = str(resolved_target_format or "").strip().lower()
        if not model_name:
            return None, 400, ProxyErrorInfo(
                message="Missing 'model' in request body",
                status_code=400,
                error_type="invalid_request_error",
                error_code="missing_model",
            )
        if not target_format:
            return None, 400, ProxyErrorInfo(
                message="Missing downstream target format",
                status_code=400,
                error_type="invalid_request_error",
                error_code="missing_target_format",
            )

        candidates = self._codex_oauth_service.iter_auth_candidates_for_model(model_name)
        if not candidates:
            return None, 503, ProxyErrorInfo(
                message=f"No available Codex OAuth account for model: {model_name}",
                status_code=503,
                error_type="upstream_error",
                error_code="codex_auth_unavailable",
            )

        last_failure: Optional[ProxyErrorInfo] = None
        for candidate in candidates:
            response, status_code, failure = self._proxy_with_candidate(
                candidate=candidate,
                model_name=model_name,
                request_data=request_data,
                request_headers=request_headers,
                on_complete=on_complete,
                forward_stream_usage=forward_stream_usage,
                target_format=target_format,
                route_name=route_name,
                client_ip=client_ip,
            )
            if failure is not None:
                last_failure = failure
                continue
            return response, status_code, failure

        if last_failure is None:
            last_failure = ProxyErrorInfo(
                message="All Codex OAuth accounts are unavailable",
                status_code=503,
                error_type="upstream_error",
                error_code="codex_auth_unavailable",
            )
        return None, last_failure.status_code, last_failure

    def _proxy_with_candidate(
        self,
        *,
        candidate: CodexAuthCandidate,
        model_name: str,
        request_data: Dict[str, Any],
        request_headers: Dict[str, str],
        on_complete: Optional[Callable[[Dict[str, Any]], None]],
        forward_stream_usage: bool,
        target_format: str,
        route_name: Optional[str],
        client_ip: Optional[str],
    ) -> tuple[Optional[Response], int, Optional[ProxyErrorInfo]]:
        translator = self._translator_registry.get("openai_responses", target_format)
        upstream_body = translator.translate_request(
            model_name,
            dict(request_data),
            True,
        )
        self._apply_codex_body_defaults(upstream_body, model_name)
        upstream_headers = self._build_codex_headers(
            request_headers,
            candidate,
            stream=True,
        )

        try:
            upstream_response = requests.post(
                CODEX_BACKEND_RESPONSES_URL,
                headers=upstream_headers,
                json=upstream_body,
                stream=True,
                timeout=1200,
                **self._build_request_options(),
            )
        except requests.exceptions.RequestException as exc:
            self._logger.error(
                "Codex upstream request error: model=%s auth_file=%s error=%s",
                model_name,
                candidate.name,
                exc,
            )
            self._codex_oauth_service.record_auth_file_failure(
                candidate.name,
                f"HTTP upstream request failed after 1 attempts: {exc}",
                status_code=502,
                error_type="upstream_request_failed",
            )
            return None, 502, ProxyErrorInfo(
                message=f"HTTP upstream request failed after 1 attempts: {exc}",
                status_code=502,
                error_type="upstream_error",
                error_code="upstream_request_failed",
            )

        if upstream_response.status_code >= 400:
            body = self._read_response_body(upstream_response)
            error_message, error_type = self._extract_response_error_info(
                body,
                fallback=f"Codex upstream returned {upstream_response.status_code}",
            )
            if self._is_quota_exhausted_response(upstream_response.status_code, body):
                retry_after = self._extract_retry_after_seconds(upstream_response, body)
                self._codex_oauth_service.mark_auth_file_quota_exhausted(
                    candidate.name,
                    retry_after_seconds=retry_after,
                )
                self._codex_oauth_service.record_auth_file_failure(
                    candidate.name,
                    error_message or "Codex OAuth account quota exhausted",
                    status_code=429,
                    error_type=error_type or "usage_limit_reached",
                    retry_after_seconds=retry_after,
                )
                self._logger.warning(
                    "Codex OAuth account quota exhausted: model=%s auth_file=%s",
                    model_name,
                    candidate.name,
                )
                return None, 429, ProxyErrorInfo(
                    message="Codex OAuth account quota exhausted",
                    status_code=429,
                    error_type="upstream_error",
                    error_code="codex_quota_exhausted",
                )
            self._codex_oauth_service.record_auth_file_failure(
                candidate.name,
                error_message,
                status_code=upstream_response.status_code,
                error_type=error_type,
            )
            return None, upstream_response.status_code, ProxyErrorInfo(
                message=error_message,
                status_code=upstream_response.status_code,
                error_type="upstream_error",
                error_code=error_type or "codex_upstream_error",
            )

        if bool(request_data.get("stream", False)):
            return (
                self._build_stream_response(
                    response=upstream_response,
                    translator=translator,
                    model_name=model_name,
                    original_request=request_data,
                    translated_request=upstream_body,
                    target_format=target_format,
                    on_complete=on_complete,
                    forward_stream_usage=forward_stream_usage,
                    route_name=route_name,
                    client_ip=client_ip,
                    auth_file_name=candidate.name,
                ),
                upstream_response.status_code,
                None,
            )

        return self._build_nonstream_response(
            response=upstream_response,
            translator=translator,
            model_name=model_name,
            original_request=request_data,
            translated_request=upstream_body,
            target_format=target_format,
            on_complete=on_complete,
            route_name=route_name,
            client_ip=client_ip,
            auth_file_name=candidate.name,
        )

    @staticmethod
    def _apply_codex_body_defaults(body: Dict[str, Any], model_name: str) -> None:
        body["model"] = model_name
        body["stream"] = True
        body["store"] = False
        body["parallel_tool_calls"] = True
        body["include"] = ["reasoning.encrypted_content"]
        if isinstance(body.get("input"), str):
            body["input"] = [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": body["input"],
                        }
                    ],
                }
            ]
        for item in body.get("input") or []:
            role = str(item.get("role") or "").strip().lower() if isinstance(item, dict) else ""
            if role == "system":
                item["role"] = "developer"
        if str(body.get("service_tier") or "").strip() not in {"priority", "fast"}:
            body.pop("service_tier", None)
        for field in (
            "max_output_tokens",
            "max_completion_tokens",
            "temperature",
            "top_p",
            "truncation",
            "context_management",
            "user",
        ):
            body.pop(field, None)
        body.pop("previous_response_id", None)
        CodexProxyService._normalize_codex_builtin_tools(body)
        body.setdefault("instructions", "")

    @staticmethod
    def _normalize_codex_builtin_tools(body: Dict[str, Any]) -> None:
        """归一 Codex 上游当前接受的内置工具名称。"""

        def normalize_tool(tool: Any) -> None:
            if not isinstance(tool, dict):
                return
            if tool.get("type") in {
                "web_search_preview",
                "web_search_preview_2025_03_11",
            }:
                tool["type"] = "web_search"

        tools = body.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                normalize_tool(tool)

        tool_choice = body.get("tool_choice")
        if isinstance(tool_choice, dict):
            normalize_tool(tool_choice)
            choice_tools = tool_choice.get("tools")
            if isinstance(choice_tools, list):
                for tool in choice_tools:
                    normalize_tool(tool)

    def _build_codex_headers(
        self,
        request_headers: Dict[str, str],
        candidate: CodexAuthCandidate,
        *,
        stream: bool,
    ) -> Dict[str, str]:
        headers = merge_http_headers({}, request_headers)
        headers = merge_http_headers(
            headers,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {candidate.access_token}",
                "Version": CODEX_CLIENT_VERSION,
                "Session_id": uuid4().hex,
                "User-Agent": CODEX_USER_AGENT,
                "Accept": "text/event-stream" if stream else "application/json",
                "Connection": "Keep-Alive",
                "Originator": "codex_cli_rs",
            },
        )
        if candidate.account_id:
            headers = merge_http_headers(
                headers,
                {"Chatgpt-Account-Id": candidate.account_id},
            )
        return headers

    def _build_request_options(self) -> Dict[str, Any]:
        if self._config_manager is None:
            return {
                "proxies": None,
                "verify": False,
            }
        return {
            "proxies": build_requests_proxies(self._config_manager.get_oauth_proxy()),
            "verify": self._config_manager.is_oauth_verify_ssl_enabled(),
        }

    def _build_stream_response(
        self,
        *,
        response: requests.Response,
        translator: Any,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        target_format: str,
        on_complete: Optional[Callable[[Dict[str, Any]], None]],
        forward_stream_usage: bool,
        route_name: Optional[str],
        client_ip: Optional[str],
        auth_file_name: str,
    ) -> Response:
        del route_name, client_ip
        downstream_headers = self._filter_response_headers(response.headers)
        downstream_headers["Content-Type"] = "text/event-stream; charset=utf-8"
        downstream_headers["Cache-Control"] = "no-cache"

        def generate() -> Iterator[bytes]:
            state: Dict[str, Any] = {}
            meta = ProxyResponseBuilder._create_empty_meta()
            terminal_sent = False
            completed = False
            failed_payload: Optional[Dict[str, Any]] = None
            stream_error_message = ""
            try:
                for event in decode_stream_events(response.iter_content(chunk_size=None), "sse_json"):
                    if event.kind == "json" and isinstance(event.payload, dict):
                        event_type = str(event.payload.get("type") or event.event or "").strip()
                        if event_type == "response.completed":
                            completed = True
                        elif event_type == "response.failed":
                            failed_payload = event.payload
                    chunks = translator.translate_stream_event(
                        model_name,
                        original_request,
                        translated_request,
                        event,
                        state,
                    )
                    ProxyResponseBuilder._update_meta_from_stream_state(meta, state)
                    for chunk in chunks:
                        if chunk.kind == "done":
                            if terminal_sent:
                                continue
                            terminal_sent = True
                        elif is_terminal_chunk(chunk, target_format):
                            terminal_sent = True

                        if chunk.kind == "json" and isinstance(chunk.payload, dict):
                            ProxyResponseBuilder._update_meta_from_payload(meta, chunk.payload)
                            if (
                                target_format == "openai_chat"
                                and not forward_stream_usage
                                and self._is_usage_only_stream_chunk(chunk.payload)
                            ):
                                continue
                        encoded = encode_downstream_chunk(chunk, target_format)
                        if encoded:
                            yield encoded

                if should_emit_terminal_chunk(target_format) and not terminal_sent:
                    yield encode_downstream_chunk(DownstreamChunk(kind="done"), target_format)
            except Exception as exc:
                stream_error_message = str(exc)
                raise
            finally:
                response.close()
                if stream_error_message:
                    self._codex_oauth_service.record_auth_file_failure(
                        auth_file_name,
                        stream_error_message,
                        status_code=502,
                        error_type="codex_stream_failed",
                    )
                elif failed_payload is not None:
                    self._codex_oauth_service.record_auth_file_failure(
                        auth_file_name,
                        self._extract_stream_failure_message(failed_payload),
                        status_code=502,
                        error_type="codex_stream_failed",
                    )
                elif completed:
                    self._codex_oauth_service.record_auth_file_success(auth_file_name)
                if on_complete is not None:
                    try:
                        on_complete(meta)
                    except Exception as exc:
                        self._logger.error("Error in Codex on_complete callback: %s", exc)

        return Response(
            stream_with_context(generate()),
            status=response.status_code,
            headers=downstream_headers,
        )

    def _build_nonstream_response(
        self,
        *,
        response: requests.Response,
        translator: Any,
        model_name: str,
        original_request: Dict[str, Any],
        translated_request: Dict[str, Any],
        target_format: str,
        on_complete: Optional[Callable[[Dict[str, Any]], None]],
        route_name: Optional[str],
        client_ip: Optional[str],
        auth_file_name: str,
    ) -> tuple[Optional[Response], int, Optional[ProxyErrorInfo]]:
        del route_name, client_ip
        try:
            completed_payload: Optional[Dict[str, Any]] = None
            failed_payload: Optional[Dict[str, Any]] = None
            for event in decode_stream_events(response.iter_content(chunk_size=None), "sse_json"):
                if event.kind != "json" or not isinstance(event.payload, dict):
                    continue
                event_type = str(event.payload.get("type") or event.event or "").strip()
                if event_type == "response.completed":
                    completed_payload = event.payload
                elif event_type == "response.failed":
                    failed_payload = event.payload

            if completed_payload is None:
                error_message = self._extract_stream_failure_message(failed_payload)
                self._codex_oauth_service.record_auth_file_failure(
                    auth_file_name,
                    error_message,
                    status_code=502,
                    error_type="codex_stream_incomplete",
                )
                return None, 502, ProxyErrorInfo(
                    message=error_message,
                    status_code=502,
                    error_type="upstream_error",
                    error_code="codex_stream_incomplete",
                )

            payload_for_translation: Any = completed_payload
            if target_format == "openai_responses" and isinstance(completed_payload.get("response"), dict):
                payload_for_translation = completed_payload["response"]
            translated_payload = translator.translate_nonstream_response(
                model_name,
                original_request,
                translated_request,
                payload_for_translation,
            )
            meta = ProxyResponseBuilder._create_empty_meta()
            if isinstance(translated_payload, dict):
                ProxyResponseBuilder._update_meta_from_payload(meta, translated_payload)
            if on_complete is not None:
                try:
                    on_complete(meta)
                except Exception as exc:
                    self._logger.error("Error in Codex on_complete callback: %s", exc)

            self._codex_oauth_service.record_auth_file_success(auth_file_name)
            return (
                Response(
                    encode_downstream_response_body(translated_payload, target_format),
                    status=response.status_code,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                ),
                response.status_code,
                None,
            )
        finally:
            response.close()

    @staticmethod
    def _extract_stream_failure_message(payload: Optional[Dict[str, Any]]) -> str:
        if not isinstance(payload, dict):
            return "Codex stream closed before response.completed"
        response = payload.get("response")
        error = response.get("error") if isinstance(response, dict) else payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error.get("message"))
        if isinstance(error, dict) and error.get("type"):
            return str(error.get("type"))
        return "Codex stream closed before response.completed"

    @staticmethod
    def _extract_response_error_info(body: bytes, *, fallback: str) -> tuple[str, str]:
        text = body.decode("utf-8", errors="replace").strip()
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            return (text[:1000] if text else fallback, "")

        if not isinstance(payload, dict):
            return (text[:1000] if text else fallback, "")

        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("type") or fallback).strip()
            error_type = str(error.get("type") or "").strip()
            return message, error_type
        if isinstance(error, str) and error.strip():
            return error.strip(), ""

        message = str(payload.get("message") or fallback).strip()
        return message, ""

    @staticmethod
    def _is_quota_exhausted_response(status_code: int, body: bytes) -> bool:
        if status_code != 429:
            return False
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return True
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return True
        return str(error.get("type") or "").strip() in {"usage_limit_reached", "rate_limit_exceeded", ""}

    @staticmethod
    def _extract_retry_after_seconds(response: requests.Response, body: bytes) -> Optional[float]:
        retry_after = response.headers.get("Retry-After")
        try:
            if retry_after:
                return float(retry_after)
        except ValueError:
            pass

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return None
        resets_in_seconds = error.get("resets_in_seconds") or error.get("resetsInSeconds")
        try:
            if resets_in_seconds is not None:
                return float(resets_in_seconds)
        except (TypeError, ValueError):
            return None
        resets_at = error.get("resets_at") or error.get("resetsAt")
        try:
            if resets_at is not None:
                return max(float(resets_at) - time.time(), 1.0)
        except (TypeError, ValueError):
            return None
        return None

    @staticmethod
    def _read_response_body(response: requests.Response) -> bytes:
        try:
            content = getattr(response, "content", None)
            if isinstance(content, bytes):
                return content
            if isinstance(content, str):
                return content.encode("utf-8")
            return b"".join(response.iter_content(chunk_size=None))
        finally:
            response.close()

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

    @staticmethod
    def _is_usage_only_stream_chunk(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        if not isinstance(payload.get("usage"), dict):
            return False
        choices = payload.get("choices")
        return choices is None or (isinstance(choices, list) and len(choices) == 0)
