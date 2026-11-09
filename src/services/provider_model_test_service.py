#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 模型可用性与性能测试服务。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator, Mapping
from typing import Any

import requests

from ..application.app_context import AppContext
from ..config.provider_config import SUPPORTED_PROVIDER_FIELDS, ProviderConfigSchema
from ..config.provider_runtime_factory import ProviderRuntimeFactory
from ..executors import OpenedUpstreamResponse, build_default_executor_registry
from ..hooks import HookContext, HookErrorType
from ..proxy_core import decode_stream_events
from ..proxy_core.usage import extract_canonical_usage
from ..translators import build_default_translator_registry
from ..translators.reasoning_utils import openai_reasoning_effort_to_claude_thinking
from ..utils.http_headers import merge_http_headers, normalize_http_headers
from ..utils.local_time import now_local_datetime
from ..utils.net import build_requests_proxy_settings, build_requests_request_proxies
from .log_service import LogService
from .upstream_request_builder import build_upstream_request


class ProviderModelTestService:
    """直连上游 provider 测试模型可用性、首字延迟与 TPS。"""

    _TEST_PROVIDER_NAME = "ProviderModelTest"
    _TEST_TARGET_FORMAT = "openai_chat"
    _NO_MODEL_OUTPUT_ERROR = (
        "Upstream returned success but no valid model output; check Provider API endpoint and source_format"
    )

    def __init__(
        self,
        ctx: AppContext,
        runtime_factory: ProviderRuntimeFactory,
        log_service: LogService,
    ):
        self._logger = ctx.logger
        self._root_path = ctx.root_path
        self._runtime_factory = runtime_factory
        self._log_service = log_service
        self._executor_registry = build_default_executor_registry(self._logger)
        self._translator_registry = build_default_translator_registry()

    def test_models(
        self,
        payload: dict[str, Any],
        *,
        request_headers: Mapping[str, str] | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        models = self._normalize_test_models(payload.get("models"))
        provider = self._build_provider(payload, models)
        normalized_headers = self._normalize_request_headers(request_headers)
        auth_entry_id = str(payload.get("auth_entry_id") or "").strip() or None
        provider_name = str(payload.get("name") or "").strip()
        results = [
            self._test_and_log_single_model(
                provider,
                model_name,
                request_headers=normalized_headers,
                auth_entry_id=auth_entry_id,
                request_model=self._build_log_request_model(provider_name, model_name),
                ip_address=ip_address,
            )
            for model_name in models
        ]
        return {"results": results}

    @staticmethod
    def _build_log_request_model(provider_name: str, model_name: str) -> str:
        if provider_name:
            return f"{provider_name}/{model_name}"
        return model_name

    @staticmethod
    def _normalize_test_models(value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("Model test models must be a non-empty list")

        normalized_models = [str(item or "").strip() for item in value]
        normalized_models = [item for item in normalized_models if item]
        if not normalized_models:
            raise ValueError("Model test models must be a non-empty list")
        return normalized_models

    @staticmethod
    def _normalize_request_headers(request_headers: Mapping[str, str] | None) -> dict[str, str]:
        return normalize_http_headers(request_headers)

    def _build_provider(self, payload: dict[str, Any], models: list[str]):
        provider_payload = {str(key): value for key, value in payload.items() if str(key) in SUPPORTED_PROVIDER_FIELDS}
        provider_payload["name"] = self._TEST_PROVIDER_NAME
        provider_payload["model_list"] = list(models)
        provider_config = ProviderConfigSchema.from_payload(provider_payload)
        return self._runtime_factory.build_provider_from_schema(provider_config)

    def _test_and_log_single_model(
        self,
        provider,
        model_name: str,
        *,
        request_headers: dict[str, str],
        auth_entry_id: str | None,
        request_model: str,
        ip_address: str | None,
    ) -> dict[str, Any]:
        log_started_at = now_local_datetime()
        response_meta = self._create_empty_meta()
        result = self._test_single_model(
            provider,
            model_name,
            request_headers=request_headers,
            auth_entry_id=auth_entry_id,
            response_meta=response_meta,
        )
        if result.get("available") is not True:
            return result

        self._log_service.log_request(
            request_model=request_model,
            response_model=str(response_meta.get("response_model") or "") or None,
            total_tokens=int(response_meta.get("total_tokens") or 0),
            prompt_tokens=int(response_meta.get("prompt_tokens") or 0),
            completion_tokens=int(response_meta.get("completion_tokens") or 0),
            usage_status=str(response_meta.get("usage_status") or "unknown"),
            cache_read_input_tokens=int(response_meta.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=int(response_meta.get("cache_creation_input_tokens") or 0),
            cache_usage_status=str(response_meta.get("cache_usage_status") or "unknown"),
            start_time=log_started_at,
            end_time=now_local_datetime(),
            ip_address=ip_address,
        )
        return result

    def _test_single_model(
        self,
        provider,
        model_name: str,
        *,
        request_headers: dict[str, str],
        auth_entry_id: str | None,
        response_meta: dict[str, Any],
    ) -> dict[str, Any]:
        translator = self._translator_registry.get(provider.source_format, self._TEST_TARGET_FORMAT)
        max_retries = max(int(provider.max_retries or 1), 1)
        previous_status_code: int | None = None
        previous_error_type: HookErrorType | None = None
        last_error_message: str | None = None
        use_completion_token_limit = False

        for attempt in range(max_retries):
            opened: OpenedUpstreamResponse | None = None
            try:
                headers, benchmark_request, translated_request, request_ctx = self._build_upstream_request(
                    provider,
                    model_name,
                    request_headers=request_headers,
                    auth_entry_id=auth_entry_id,
                    translator=translator,
                    attempt=attempt,
                    previous_status_code=previous_status_code,
                    previous_error_type=previous_error_type,
                    use_completion_token_limit=use_completion_token_limit,
                )
                request_proxies = build_requests_request_proxies(
                    build_requests_proxy_settings(
                        provider.proxy_mode,
                        provider.proxy,
                        proxy_mode_error_message="Provider proxy_mode must be one of: direct, system, custom",
                        proxy_url_error_message="Provider proxy must be a valid absolute URL",
                    )
                )
                request_started_at = time.perf_counter()
                opened = self._open_upstream_response(
                    provider,
                    headers,
                    translated_request,
                    requested_stream=request_ctx.stream,
                    request_proxies=request_proxies,
                    timeout_seconds=provider.timeout_seconds,
                    verify_ssl=provider.verify_ssl,
                )

                if opened.status_code >= 400:
                    error_message = self._consume_error_response(opened)
                    try:
                        opened.response.close()
                    except Exception:
                        pass
                    previous_status_code = opened.status_code
                    previous_error_type = HookErrorType.TRANSPORT_ERROR
                    last_error_message = error_message
                    if (
                        attempt + 1 < max_retries
                        and not use_completion_token_limit
                        and provider.source_format == "openai_chat"
                        and "max_tokens" in translated_request
                        and "max_completion_tokens" not in translated_request
                        and self._requires_completion_token_limit(opened.status_code, error_message)
                    ):
                        # 仅按上游明确的参数替换建议重试，状态局限于本次模型测试。
                        use_completion_token_limit = True
                        continue
                    if attempt + 1 < max_retries and self._should_retry_status_code(opened.status_code):
                        continue
                    return self._build_failure_result(
                        model_name=model_name,
                        response_model=None,
                        error=error_message,
                    )

                if opened.is_stream:
                    return self._collect_stream_result(
                        opened=opened,
                        provider=provider,
                        model_name=model_name,
                        original_request=benchmark_request,
                        translated_request=translated_request,
                        translator=translator,
                        request_started_at=request_started_at,
                        meta=response_meta,
                    )

                return self._collect_nonstream_result(
                    opened=opened,
                    model_name=model_name,
                    original_request=benchmark_request,
                    translated_request=translated_request,
                    translator=translator,
                    meta=response_meta,
                )
            except requests.RequestException as exc:
                previous_error_type = self._classify_request_error(exc)
                previous_status_code = None
                last_error_message = str(exc)
                if attempt + 1 < max_retries:
                    continue
            except Exception as exc:
                self._logger.error(
                    "Provider model test failed: provider=%s model=%s error=%s", provider.name, model_name, exc
                )
                return self._build_failure_result(
                    model_name=model_name,
                    response_model=None,
                    error=str(exc) or "Provider model test failed",
                )
            finally:
                if opened is not None and not opened.is_stream:
                    try:
                        opened.response.close()
                    except Exception:
                        pass

        return self._build_failure_result(
            model_name=model_name,
            response_model=None,
            error=last_error_message or "Provider model test failed",
        )

    def _build_upstream_request(
        self,
        provider,
        model_name: str,
        *,
        request_headers: dict[str, str],
        auth_entry_id: str | None,
        translator,
        attempt: int,
        previous_status_code: int | None,
        previous_error_type: HookErrorType | None,
        use_completion_token_limit: bool = False,
    ) -> tuple[dict[str, str], dict[str, Any], dict[str, Any], HookContext]:
        request_data = self._build_benchmark_request(provider, model_name)
        if use_completion_token_limit:
            request_data["max_completion_tokens"] = request_data.pop("max_tokens")
        headers = {"content-type": "application/json"}
        if request_headers:
            headers = merge_http_headers(headers, request_headers)
        elif provider.api_key:
            headers["authorization"] = f"Bearer {provider.api_key}"
        built_request = build_upstream_request(
            root_path=self._root_path,
            logger=self._logger,
            provider=provider,
            request_model=model_name,
            upstream_model=model_name,
            provider_target_format=self._TEST_TARGET_FORMAT,
            request_data=request_data,
            request_headers=headers,
            translator=translator,
            attempt=attempt,
            previous_status_code=previous_status_code,
            previous_error_type=previous_error_type,
            auth_group_name=provider.auth_group,
            auth_entry_id=auth_entry_id,
        )
        return (
            built_request.headers,
            built_request.original_body,
            built_request.translated_body,
            built_request.request_ctx,
        )

    @staticmethod
    def _build_benchmark_request(provider, model_name: str) -> dict[str, Any]:
        benchmark_block = "\n".join(
            f"{index:02d}: the quick brown fox jumps over the lazy dog" for index in range(1, 13)
        )
        request: dict[str, Any] = {
            "model": model_name,
            "stream": True,
            "max_tokens": 192,
            "messages": [
                {
                    "role": "system",
                    "content": "You are running a latency check. Output only the requested text.",
                },
                {
                    "role": "user",
                    "content": (
                        "Copy the following block exactly once. Keep every line unchanged and output nothing else.\n\n"
                        f"{benchmark_block}"
                    ),
                },
            ],
        }
        if (
            str(getattr(provider, "source_format", "") or "").strip().lower() == "claude_chat"
            and "glm" in model_name.lower()
        ):
            # GLM Claude 接口要求始终开启思考；模型测试使用最小的 low 档位。
            request["reasoning_effort"] = "low"
            thinking = openai_reasoning_effort_to_claude_thinking("low", max_tokens=request["max_tokens"])
            if thinking is not None:
                # 总额度同时容纳 low 思考预算和基准正文，预算与协议转换器保持一致。
                request["max_tokens"] += int(thinking["budget_tokens"])
        return request

    @staticmethod
    def _requires_completion_token_limit(status_code: int, error_message: str) -> bool:
        """识别明确要求用 max_completion_tokens 替代 max_tokens 的参数错误。"""
        if status_code not in {400, 422}:
            return False
        message = error_message.lower()
        if not re.search(r"\bmax_completion_tokens\b", message):
            return False
        return bool(
            re.search(r"unsupported\s+(?:parameter|argument)\s*:\s*['\"`]?max_tokens\b", message)
            or re.search(r"\bmax_tokens\b['\"`]?\s+(?:is\s+)?(?:not supported|unsupported|not allowed)\b", message)
        )

    def _open_upstream_response(
        self,
        provider,
        headers: dict[str, str],
        body: dict[str, Any],
        *,
        requested_stream: bool,
        request_proxies: dict[str, str | None] | None,
        timeout_seconds: int,
        verify_ssl: bool,
    ) -> OpenedUpstreamResponse:
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

    def _collect_stream_result(
        self,
        *,
        opened: OpenedUpstreamResponse,
        provider,
        model_name: str,
        original_request: dict[str, Any],
        translated_request: dict[str, Any],
        translator,
        request_started_at: float,
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        first_token_at: float | None = None
        last_token_at: float | None = None
        received_at = request_started_at
        reasoning_observed = False
        reasoning_tokens = 0
        redacted_reasoning = False
        stream_error: str | None = None
        has_model_output = False
        state: dict[str, Any] = {}

        def timed_chunks() -> Iterator[bytes]:
            nonlocal received_at
            for data in opened.response.iter_content(chunk_size=None):
                if data:
                    # 同一网络数据块中的多个事件共享到达时间，避免本地解析制造虚假生成区间。
                    received_at = time.perf_counter()
                    yield data

        try:
            for event in decode_stream_events(timed_chunks(), opened.stream_format):
                if event.kind == "json":
                    if isinstance(event.payload, dict):
                        block = event.payload.get("content_block")
                        if isinstance(block, dict) and block.get("type") == "redacted_thinking":
                            redacted_reasoning = True
                    usage = extract_canonical_usage(event.payload, provider.source_format)
                    if usage is not None and "reasoning_tokens" in usage:
                        reasoning_tokens = int(usage["reasoning_tokens"])
                translated_chunks = translator.translate_stream_event(
                    model_name,
                    original_request,
                    translated_request,
                    event,
                    state,
                )
                for chunk in translated_chunks:
                    if chunk.kind == "json" and isinstance(chunk.payload, dict):
                        self._update_meta_from_payload(meta, chunk.payload)
                        if self._has_openai_chat_output_delta(chunk.payload):
                            if first_token_at is None:
                                first_token_at = received_at
                            last_token_at = received_at
                            has_model_output = True
                        if self._has_openai_chat_output_delta(chunk.payload, fields=("reasoning_content",)):
                            reasoning_observed = True
                        error_message = self._extract_error_message(chunk.payload)
                        if error_message:
                            stream_error = error_message
        finally:
            try:
                opened.response.close()
            except Exception:
                pass

        if stream_error:
            return self._build_failure_result(
                model_name=model_name,
                response_model=str(meta.get("response_model") or "") or None,
                error=stream_error,
            )

        if not has_model_output:
            return self._build_failure_result(
                model_name=model_name,
                response_model=str(meta.get("response_model") or "") or None,
                error=self._NO_MODEL_OUTPUT_ERROR,
            )

        return self._build_success_result(
            model_name=model_name,
            response_model=str(meta.get("response_model") or "") or None,
            first_token_at=first_token_at,
            last_token_at=last_token_at,
            request_started_at=request_started_at,
            completion_tokens=int(meta.get("completion_tokens") or 0),
            # Responses 思考摘要不是逐 Token 的完整思考输出，不能用于隐藏思考的生成区间。
            unobserved_reasoning=redacted_reasoning
            or (reasoning_tokens > 0 and not reasoning_observed)
            or (provider.source_format == "openai_responses" and (reasoning_tokens > 0 or reasoning_observed)),
        )

    def _collect_nonstream_result(
        self,
        *,
        opened: OpenedUpstreamResponse,
        model_name: str,
        original_request: dict[str, Any],
        translated_request: dict[str, Any],
        translator,
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        raw_body = self._read_response_body(opened)
        decoded_payload = self._decode_response_payload(raw_body)
        translated_payload = translator.translate_nonstream_response(
            model_name,
            original_request,
            translated_request,
            decoded_payload,
        )

        if isinstance(translated_payload, dict):
            error_message = self._extract_error_message(translated_payload)
            if error_message:
                return self._build_failure_result(
                    model_name=model_name,
                    response_model=self._extract_response_model(translated_payload),
                    error=error_message,
                )

        if isinstance(translated_payload, dict):
            self._update_meta_from_payload(meta, translated_payload)

        if not self._has_openai_chat_nonstream_output(translated_payload):
            return self._build_failure_result(
                model_name=model_name,
                response_model=str(meta.get("response_model") or "") or None,
                error=self._NO_MODEL_OUTPUT_ERROR,
            )

        return self._build_success_result(
            model_name=model_name,
            response_model=str(meta.get("response_model") or "") or None,
            first_token_at=None,
            last_token_at=None,
            request_started_at=None,
            completion_tokens=int(meta.get("completion_tokens") or 0),
        )

    @classmethod
    def _build_success_result(
        cls,
        *,
        model_name: str,
        response_model: str | None,
        first_token_at: float | None,
        last_token_at: float | None,
        request_started_at: float | None,
        completion_tokens: int,
        unobserved_reasoning: bool = False,
    ) -> dict[str, Any]:
        first_token_latency_ms: float | None = None
        tps: float | None = None

        if first_token_at is not None and request_started_at is not None:
            first_token_latency_ms = round(max((first_token_at - request_started_at) * 1000, 0), 2)
        if (
            first_token_at is not None
            and last_token_at is not None
            and completion_tokens > 1
            and not unobserved_reasoning
        ):
            elapsed_seconds = last_token_at - first_token_at
            if elapsed_seconds > 0:
                tps = round((completion_tokens - 1) / elapsed_seconds, 2)

        return {
            "requested_model": model_name,
            "available": True,
            "first_token_latency_ms": first_token_latency_ms,
            "tps": tps,
            "response_model": response_model,
            "error": None,
        }

    @staticmethod
    def _build_failure_result(
        *,
        model_name: str,
        response_model: str | None,
        error: str,
    ) -> dict[str, Any]:
        return {
            "requested_model": model_name,
            "available": False,
            "first_token_latency_ms": None,
            "tps": None,
            "response_model": response_model,
            "error": error,
        }

    @staticmethod
    def _create_empty_meta() -> dict[str, Any]:
        return {
            "response_model": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "usage_status": "unknown",
        }

    @staticmethod
    def _update_meta_from_payload(meta: dict[str, Any], payload: dict[str, Any]) -> None:
        model = payload.get("model")
        if model:
            meta["response_model"] = str(model)

        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return

        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        if prompt_tokens is not None:
            meta["prompt_tokens"] = int(prompt_tokens or 0)
        if completion_tokens is not None:
            meta["completion_tokens"] = int(completion_tokens or 0)
        if total_tokens not in (None, "") and int(total_tokens or 0) > 0:
            meta["total_tokens"] = int(total_tokens)
        elif prompt_tokens is not None or completion_tokens is not None:
            meta["total_tokens"] = int(meta["prompt_tokens"]) + int(meta["completion_tokens"])
        if prompt_tokens is not None and completion_tokens is not None:
            meta["usage_status"] = "known"
        elif prompt_tokens is not None or completion_tokens is not None:
            meta["usage_status"] = "partial"

    @staticmethod
    def _has_openai_chat_output_delta(
        payload: dict[str, Any], *, fields: tuple[str, ...] = ("content", "reasoning_content")
    ) -> bool:
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return False
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            for field_name in fields:
                content = delta.get(field_name)
                if isinstance(content, str) and content:
                    return True
        return False

    @staticmethod
    def _has_openai_chat_nonstream_output(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False

        choices = payload.get("choices")
        if not isinstance(choices, list):
            return False

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            for field_name in ("content", "reasoning_content"):
                content = message.get(field_name)
                if isinstance(content, str) and content:
                    return True
        return False

    @staticmethod
    def _extract_error_message(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None

        error_value = payload.get("error")
        if isinstance(error_value, str) and error_value.strip():
            return error_value.strip()
        if isinstance(error_value, dict):
            message = error_value.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
            serialized = json.dumps(error_value, ensure_ascii=False)
            if serialized:
                return serialized

        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return None

    @staticmethod
    def _extract_response_model(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        model = payload.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
        return None

    @staticmethod
    def _decode_response_payload(raw_body: bytes) -> Any:
        text = raw_body.decode("utf-8", errors="replace").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    @staticmethod
    def _read_response_body(opened: OpenedUpstreamResponse) -> bytes:
        response_body = getattr(opened.response, "content", None)
        if isinstance(response_body, bytes):
            return response_body
        if isinstance(response_body, str):
            return response_body.encode("utf-8")
        return b""

    def _consume_error_response(self, opened: OpenedUpstreamResponse) -> str:
        raw_body = self._read_response_body(opened)
        if not raw_body and opened.is_stream:
            raw_body = b"".join(opened.response.iter_content(chunk_size=None))

        payload = self._decode_response_payload(raw_body)
        error_message = self._extract_error_message(payload)
        if error_message:
            return error_message

        if isinstance(payload, str) and payload.strip():
            return payload.strip()
        return f"Upstream returned status {opened.status_code}"

    @staticmethod
    def _should_retry_status_code(status_code: int) -> bool:
        return status_code in {408, 409, 425, 429, 500, 502, 503, 504}

    @staticmethod
    def _classify_request_error(exc: requests.exceptions.RequestException) -> HookErrorType:
        if isinstance(exc, requests.exceptions.Timeout):
            return HookErrorType.TIMEOUT
        if isinstance(exc, requests.exceptions.ConnectionError):
            return HookErrorType.CONNECTION_ERROR
        return HookErrorType.TRANSPORT_ERROR
