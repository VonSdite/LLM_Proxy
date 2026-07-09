#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游模型发现服务。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import requests

from ..application.app_context import AppContext
from ..config.provider_config import (
    ProviderConfigSchema,
    clean_optional_string,
    parse_optional_bool,
    parse_optional_positive_int,
)
from ..config.provider_runtime_factory import ProviderRuntimeFactory
from ..hooks import HookContext
from ..utils.http_headers import merge_http_headers
from ..utils.net import apply_requests_proxy_settings, build_requests_proxy_settings, build_requests_request_proxies
from ..utils.proxy_warning import (
    ProxyWarningRequired,
    close_response,
    request_with_proxy_warning_retry,
)


class ModelDiscoveryService:
    """负责探测上游 provider 的模型列表。"""

    _HOOK_PROVIDER_NAME = "ModelDiscovery"
    _HOOK_PROVIDER_MODEL = "__model_discovery__"

    def __init__(self, ctx: AppContext, runtime_factory: ProviderRuntimeFactory | None = None):
        self._logger = ctx.logger
        self._root_path = ctx.root_path
        self._runtime_factory = runtime_factory or ProviderRuntimeFactory(ctx)

    def fetch_models_preview(
        self,
        api: str,
        api_key: str | None = None,
        request_headers: Mapping[str, str] | None = None,
        hook: str | None = None,
        provider_name: str | None = None,
        source_format: str | None = None,
        auth_group: str | None = None,
        auth_entry_id: str | None = None,
        proxy_mode: str | None = None,
        proxy: str | None = None,
        timeout_seconds: Any | None = None,
        verify_ssl: Any | None = None,
    ) -> dict[str, Any]:
        if not api or not str(api).strip():
            raise ValueError("Provider api is required")

        normalized_api = str(api).strip()
        normalized_api_key = clean_optional_string(api_key)
        headers = self._build_model_fetch_headers(normalized_api_key, request_headers)
        normalized_timeout_seconds = parse_optional_positive_int(timeout_seconds, default=10) or 10
        normalized_verify_ssl = parse_optional_bool(verify_ssl, default=False) or False
        normalized_hook = clean_optional_string(hook)

        if normalized_hook:
            hook_models = self._fetch_models_from_hook(
                api=normalized_api,
                hook=normalized_hook,
                headers=headers,
                api_key=normalized_api_key,
                request_headers=request_headers,
                provider_name=provider_name,
                source_format=source_format,
                auth_group=auth_group,
                auth_entry_id=auth_entry_id,
                proxy_mode=proxy_mode,
                proxy=proxy,
                timeout_seconds=normalized_timeout_seconds,
                verify_ssl=normalized_verify_ssl,
            )
            if hook_models is not None:
                return {
                    "fetched_models": hook_models,
                    "fetched_count": len(hook_models),
                }

        fetched_models = self._fetch_models_from_upstream(
            api=normalized_api,
            headers=headers,
            proxy_mode=proxy_mode,
            proxy=proxy,
            timeout_seconds=normalized_timeout_seconds,
            verify_ssl=normalized_verify_ssl,
        )

        return {
            "fetched_models": fetched_models,
            "fetched_count": len(fetched_models),
        }

    def _fetch_models_from_hook(
        self,
        *,
        api: str,
        hook: str,
        headers: dict[str, str],
        api_key: str | None,
        request_headers: Mapping[str, str] | None,
        provider_name: str | None,
        source_format: str | None,
        auth_group: str | None,
        auth_entry_id: str | None,
        proxy_mode: str | None,
        proxy: str | None,
        timeout_seconds: int,
        verify_ssl: bool,
    ) -> list[str] | None:
        provider = self._build_hook_provider(
            api=api,
            hook=hook,
            proxy_mode=proxy_mode,
            proxy=proxy,
            timeout_seconds=timeout_seconds,
            verify_ssl=verify_ssl,
        )
        if provider.hook is None:
            return None

        hook_provider_name = clean_optional_string(provider_name) or self._HOOK_PROVIDER_NAME
        hook_source_format = clean_optional_string(source_format) or provider.source_format
        candidate_urls = self._build_model_endpoint_candidates(api)
        payload = {
            "api": api,
            "api_key": api_key,
            "headers": dict(headers),
            "request_headers": dict(request_headers or {}),
            "candidate_urls": list(candidate_urls),
            "provider_name": hook_provider_name,
            "source_format": hook_source_format,
            "auth_group": clean_optional_string(auth_group),
            "auth_entry_id": clean_optional_string(auth_entry_id),
            "proxy_mode": provider.proxy_mode,
            "proxy": provider.proxy,
            "timeout_seconds": timeout_seconds,
            "verify_ssl": verify_ssl,
        }
        ctx = HookContext(
            retry=0,
            root_path=self._root_path,
            logger=self._logger,
            provider_name=hook_provider_name,
            provider_source_format=hook_source_format,
            provider_target_format=provider.primary_target_format,
            transport=provider.transport,
            stream=False,
            auth_group_name=clean_optional_string(auth_group),
            auth_entry_id=clean_optional_string(auth_entry_id),
        )
        hook_payload = provider.apply_fetch_models_hook(ctx, payload)
        if hook_payload is None:
            return None

        models = self._extract_models_from_payload(hook_payload)
        if not models:
            raise ValueError("Hook fetch_models returned no models")

        self._logger.info(
            "Fetched %s models from provider hook: provider_api=%s hook=%s",
            len(models),
            api,
            hook,
        )
        return models

    def _build_hook_provider(
        self,
        *,
        api: str,
        hook: str,
        proxy_mode: str | None,
        proxy: str | None,
        timeout_seconds: int,
        verify_ssl: bool,
    ):
        provider_config = ProviderConfigSchema.from_mapping(
            {
                "name": self._HOOK_PROVIDER_NAME,
                "api": api,
                "model_list": [self._HOOK_PROVIDER_MODEL],
                "hook": hook,
                "proxy_mode": proxy_mode,
                "proxy": proxy,
                "timeout_seconds": timeout_seconds,
                "verify_ssl": verify_ssl,
            }
        )
        return self._runtime_factory.build_provider_from_schema(provider_config)

    def _fetch_models_from_upstream(
        self,
        api: str,
        headers: dict[str, str],
        proxy_mode: str | None,
        proxy: str | None,
        timeout_seconds: int,
        verify_ssl: bool,
    ) -> list[str]:
        proxy_settings = build_requests_proxy_settings(
            proxy_mode,
            proxy,
            proxy_mode_error_message="Provider proxy_mode must be one of: direct, system, custom",
            proxy_url_error_message="Provider proxy must be a valid absolute URL",
        )

        candidates = self._build_model_endpoint_candidates(api)
        candidate_errors: list[str] = []

        with requests.Session() as session:
            apply_requests_proxy_settings(session, proxy_settings)
            for url in candidates:
                response: Any = None
                try:
                    request_options = {
                        "proxies": build_requests_request_proxies(proxy_settings),
                        "verify": verify_ssl,
                    }
                    response = request_with_proxy_warning_retry(
                        lambda: session.get(
                            url,
                            headers=headers,
                            timeout=timeout_seconds,
                            allow_redirects=False,
                            **request_options,
                        ),
                        request_options=request_options,
                        confirm_session=session,
                        logger=self._logger,
                        log_context=f"model_discovery_url={url}",
                    )
                    if response.status_code >= 400:
                        candidate_errors.append(f"{url} returned {response.status_code}")
                        continue

                    payload = response.json()
                    models = self._extract_models_from_payload(payload)
                    if models:
                        self._logger.info(
                            "Fetched %s models from provider endpoint: provider_api=%s endpoint=%s",
                            len(models),
                            api,
                            url,
                        )
                        return models
                    candidate_errors.append(f"{url} returned no models")
                except requests.RequestException as exc:
                    candidate_errors.append(f"{url} request failed: {exc}")
                except ProxyWarningRequired as exc:
                    candidate_errors.append(f"{url} requires proxy confirmation: {exc.confirmation_url}")
                except ValueError as exc:
                    candidate_errors.append(f"{url} returned invalid json: {exc}")
                finally:
                    close_response(response)

        raise ValueError("; ".join(candidate_errors) or "Failed to fetch models")

    @staticmethod
    def _build_model_fetch_headers(
        api_key: str | None,
        request_headers: Mapping[str, str] | None,
    ) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        return merge_http_headers(headers, request_headers)

    @staticmethod
    def _build_model_endpoint_candidates(api: str) -> list[str]:
        cleaned_api = api.strip().rstrip("/")
        parsed = urlparse(cleaned_api)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Provider api must be a valid absolute URL")

        normalized_scheme = parsed.scheme.lower()
        if normalized_scheme not in {"http", "https"}:
            raise ValueError("Provider api must use http:// or https://")

        root = f"{normalized_scheme}://{parsed.netloc}"
        base_path = ModelDiscoveryService._build_model_endpoint_base_path(parsed.path.rstrip("/"))
        base_url = f"{root}{base_path}"
        return [
            f"{base_url}/v1/models",
            f"{base_url}/models",
        ]

    @staticmethod
    def _build_model_endpoint_base_path(path: str) -> str:
        normalized_path = path.rstrip("/")
        if not normalized_path:
            return ""

        lower_path = normalized_path.lower()
        known_suffixes = (
            "/v1/chat/completions",
            "/chat/completions",
            "/v1/completions",
            "/completions",
            "/v1/responses",
            "/responses",
            "/v1/messages",
            "/messages",
            "/v1/models",
            "/models",
            "/v1",
        )
        for suffix in known_suffixes:
            if lower_path.endswith(suffix):
                return normalized_path[: -len(suffix)].rstrip("/")
        return normalized_path

    @staticmethod
    def _extract_models_from_payload(payload: Any) -> list[str]:
        models: list[str] = []
        items: Any = None
        if isinstance(payload, dict):
            items = payload.get("data")
            if items is None and isinstance(payload.get("models"), list):
                items = payload.get("models")
        elif isinstance(payload, list):
            items = payload

        if not isinstance(items, list):
            return models

        seen: set[str] = set()
        for item in items:
            if isinstance(item, dict):
                model = item.get("id") or item.get("name")
            else:
                model = item
            model_name = str(model).strip() if model is not None else ""
            if not model_name or model_name in seen:
                continue
            seen.add(model_name)
            models.append(model_name)
        return models
