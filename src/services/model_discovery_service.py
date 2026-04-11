#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游模型发现服务。"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse

import requests

from ..application.app_context import AppContext
from ..config.provider_config import clean_optional_string, parse_optional_bool, parse_optional_positive_int
from ..utils.net import build_requests_proxies, normalize_proxy_url


class ModelDiscoveryService:
    """负责探测上游 provider 的模型列表。"""

    def __init__(self, ctx: AppContext):
        self._logger = ctx.logger

    def fetch_models_preview(
        self,
        api: str,
        api_key: Optional[str] = None,
        request_headers: Optional[Mapping[str, str]] = None,
        proxy: Optional[str] = None,
        timeout_seconds: Optional[Any] = None,
        verify_ssl: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if not api or not str(api).strip():
            raise ValueError('Provider api is required')

        fetched_models = self._fetch_models_from_upstream(
            api=str(api).strip(),
            api_key=clean_optional_string(api_key),
            request_headers=request_headers,
            proxy=normalize_proxy_url(proxy),
            timeout_seconds=parse_optional_positive_int(timeout_seconds, default=30) or 30,
            verify_ssl=parse_optional_bool(verify_ssl, default=False) or False,
        )

        return {
            'fetched_models': fetched_models,
            'fetched_count': len(fetched_models),
        }

    def _fetch_models_from_upstream(
        self,
        api: str,
        api_key: Optional[str],
        request_headers: Optional[Mapping[str, str]],
        proxy: Optional[str],
        timeout_seconds: int,
        verify_ssl: bool,
    ) -> List[str]:
        headers = {'accept': 'application/json'}
        if api_key:
            headers['authorization'] = f'Bearer {api_key}'
        if request_headers:
            for raw_key, raw_value in request_headers.items():
                header_name = str(raw_key or '').strip()
                if not header_name:
                    continue
                duplicated_keys = [
                    existing_key
                    for existing_key in headers
                    if existing_key.lower() == header_name.lower() and existing_key != header_name
                ]
                for duplicated_key in duplicated_keys:
                    headers.pop(duplicated_key, None)
                headers[header_name] = '' if raw_value is None else str(raw_value).strip()
        proxies = build_requests_proxies(proxy)

        candidates = self._build_model_endpoint_candidates(api)
        last_error: Optional[str] = None

        with requests.Session() as session:
            for url in candidates:
                try:
                    response = session.get(
                        url,
                        headers=headers,
                        proxies=proxies,
                        timeout=timeout_seconds,
                        verify=verify_ssl,
                    )
                    if response.status_code >= 400:
                        last_error = f'{url} returned {response.status_code}'
                        continue

                    payload = response.json()
                    models = self._extract_models_from_payload(payload)
                    if models:
                        self._logger.info(
                            'Fetched %s models from provider endpoint: provider_api=%s endpoint=%s',
                            len(models),
                            api,
                            url,
                        )
                        return models
                    last_error = f'{url} returned no models'
                except requests.RequestException as exc:
                    last_error = f'{url} request failed: {exc}'
                except ValueError as exc:
                    last_error = f'{url} returned invalid json: {exc}'

        raise ValueError(last_error or 'Failed to fetch models')

    @staticmethod
    def _build_model_endpoint_candidates(api: str) -> List[str]:
        cleaned_api = api.strip().rstrip('/')
        parsed = urlparse(cleaned_api)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError('Provider api must be a valid absolute URL')

        normalized_scheme = parsed.scheme.lower()
        if normalized_scheme == 'ws':
            normalized_scheme = 'http'
        elif normalized_scheme == 'wss':
            normalized_scheme = 'https'

        root = f'{normalized_scheme}://{parsed.netloc}'
        base_path = ModelDiscoveryService._build_model_endpoint_base_path(parsed.path.rstrip('/'))
        base_url = f'{root}{base_path}'
        return [
            f'{base_url}/v1/models',
            f'{base_url}/models',
        ]

    @staticmethod
    def _build_model_endpoint_base_path(path: str) -> str:
        normalized_path = path.rstrip('/')
        if not normalized_path:
            return ''

        lower_path = normalized_path.lower()
        known_suffixes = (
            '/v1/chat/completions',
            '/chat/completions',
            '/v1/completions',
            '/completions',
            '/v1/responses',
            '/responses',
            '/v1/models',
            '/models',
            '/v1',
        )
        for suffix in known_suffixes:
            if lower_path.endswith(suffix):
                return normalized_path[: -len(suffix)].rstrip('/')
        return normalized_path

    @staticmethod
    def _extract_models_from_payload(payload: Any) -> List[str]:
        models: List[str] = []
        items: Any = None
        if isinstance(payload, dict):
            items = payload.get('data')
            if items is None and isinstance(payload.get('models'), list):
                items = payload.get('models')
        elif isinstance(payload, list):
            items = payload

        if not isinstance(items, list):
            return models

        seen: set[str] = set()
        for item in items:
            if isinstance(item, dict):
                model = item.get('id') or item.get('name')
            else:
                model = item
            model_name = str(model).strip() if model is not None else ''
            if not model_name or model_name in seen:
                continue
            seen.add(model_name)
            models.append(model_name)
        return models
