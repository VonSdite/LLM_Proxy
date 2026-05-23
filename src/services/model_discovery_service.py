#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游模型发现服务。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import requests

from ..application.app_context import AppContext
from ..config.provider_config import clean_optional_string, parse_optional_bool, parse_optional_positive_int
from ..utils.http_headers import merge_http_headers
from ..utils.net import apply_requests_proxy_settings, build_requests_proxy_settings
from ..utils.proxy_warning import (
    ProxyWarningRequired,
    close_response,
    request_with_proxy_warning_retry,
)


class ModelDiscoveryService:
    """负责探测上游 provider 的模型列表。"""

    def __init__(self, ctx: AppContext):
        self._logger = ctx.logger

    def fetch_models_preview(
        self,
        api: str,
        api_key: str | None = None,
        request_headers: Mapping[str, str] | None = None,
        proxy_mode: str | None = None,
        proxy: str | None = None,
        timeout_seconds: Any | None = None,
        verify_ssl: Any | None = None,
    ) -> dict[str, Any]:
        if not api or not str(api).strip():
            raise ValueError("Provider api is required")

        fetched_models = self._fetch_models_from_upstream(
            api=str(api).strip(),
            api_key=clean_optional_string(api_key),
            request_headers=request_headers,
            proxy_mode=proxy_mode,
            proxy=proxy,
            timeout_seconds=parse_optional_positive_int(timeout_seconds, default=10) or 10,
            verify_ssl=parse_optional_bool(verify_ssl, default=False) or False,
        )

        return {
            "fetched_models": fetched_models,
            "fetched_count": len(fetched_models),
        }

    def _fetch_models_from_upstream(
        self,
        api: str,
        api_key: str | None,
        request_headers: Mapping[str, str] | None,
        proxy_mode: str | None,
        proxy: str | None,
        timeout_seconds: int,
        verify_ssl: bool,
    ) -> list[str]:
        headers = {"accept": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        headers = merge_http_headers(headers, request_headers)
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
                        "proxies": proxy_settings.proxies,
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
