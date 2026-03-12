#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 配置管理与模型拉取服务。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import requests
import yaml

from ..application.app_context import AppContext
from ..config import ProviderManager
from ..utils.net import build_requests_proxies, normalize_proxy_url


class ProviderService:
    """负责 provider 配置的增删改查、写回与模型拉取。"""

    def __init__(self, ctx: AppContext, config_path: Path, reload_callback: Callable[[], None]):
        self._ctx = ctx
        self._logger = ctx.logger
        self._config_path = Path(config_path).resolve()
        self._reload_callback = reload_callback

    def list_providers(self) -> List[Dict[str, Any]]:
        config = self._load_config()
        providers = self._extract_providers(config)
        return [self._clone_provider(provider) for provider in providers]

    def get_provider(self, name: str) -> Optional[Dict[str, Any]]:
        provider = self._find_provider(self.list_providers(), name)
        return self._clone_provider(provider) if provider else None

    def create_provider(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._load_config()
        providers = self._extract_providers(config)
        normalized = self._normalize_provider_payload(payload)

        if self._find_provider(providers, normalized["name"]):
            raise ValueError(f"Provider already exists: {normalized['name']}")

        providers.append(normalized)
        self._validate_providers(providers)
        config["providers"] = providers
        self._write_config(config)
        self._reload_callback()
        return self._clone_provider(normalized)

    def update_provider(self, current_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._load_config()
        providers = self._extract_providers(config)
        normalized = self._normalize_provider_payload(payload)

        target = self._find_provider(providers, current_name)
        if target is None:
            raise ValueError(f"Provider not found: {current_name}")

        duplicate = self._find_provider(providers, normalized["name"])
        if duplicate is not None and duplicate is not target:
            raise ValueError(f"Provider already exists: {normalized['name']}")

        target_index = providers.index(target)
        providers[target_index] = normalized
        self._validate_providers(providers)
        config["providers"] = providers
        self._write_config(config)
        self._reload_callback()
        return self._clone_provider(normalized)

    def delete_provider(self, name: str) -> None:
        config = self._load_config()
        providers = self._extract_providers(config)
        target = self._find_provider(providers, name)
        if target is None:
            raise ValueError(f"Provider not found: {name}")

        providers.remove(target)
        self._validate_providers(providers)
        config["providers"] = providers
        self._write_config(config)
        self._reload_callback()

    def fetch_models_preview(
        self,
        api: str,
        api_key: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout_seconds: Optional[Any] = None,
        verify_ssl: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if not api or not str(api).strip():
            raise ValueError("Provider api is required")

        effective_api_key = self._clean_optional_string(api_key)
        effective_proxy = self._parse_optional_proxy(proxy)

        effective_timeout = self._parse_optional_positive_int(timeout_seconds)
        if effective_timeout is None:
            effective_timeout = 30

        effective_verify_ssl = self._parse_optional_bool(verify_ssl)
        if effective_verify_ssl is None:
            effective_verify_ssl = False

        fetched_models = self._fetch_models_from_upstream(
            api=str(api).strip(),
            api_key=effective_api_key,
            proxy=effective_proxy,
            timeout_seconds=effective_timeout,
            verify_ssl=effective_verify_ssl,
        )

        return {
            "fetched_models": fetched_models,
            "fetched_count": len(fetched_models),
        }

    def update_chat_whitelist_enabled(self, enabled: Any) -> bool:
        parsed_enabled = self._parse_optional_bool(enabled)
        if parsed_enabled is None:
            raise ValueError("Whitelist enabled flag is required")

        config = self._load_config()
        chat_config = config.get("chat")
        if chat_config is None:
            chat_config = {}
            config["chat"] = chat_config
        if not isinstance(chat_config, dict):
            raise ValueError("Config field 'chat' must be an object")

        chat_config["whitelist_enabled"] = parsed_enabled
        self._write_config(config)
        self._reload_callback()
        return parsed_enabled

    def _load_config(self) -> Dict[str, Any]:
        with open(self._config_path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)

        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError("Configuration file must contain a top-level mapping")
        return data

    def _write_config(self, config: Dict[str, Any]) -> None:
        temp_file_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._config_path.parent,
                delete=False,
            ) as temp_file:
                yaml.safe_dump(config, temp_file, allow_unicode=True, sort_keys=False)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_file_path = temp_file.name

            os.replace(temp_file_path, self._config_path)
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    @staticmethod
    def _extract_providers(config: Dict[str, Any]) -> List[Dict[str, Any]]:
        providers = config.get("providers", [])
        if providers is None:
            providers = []
        if not isinstance(providers, list):
            raise ValueError("Config field 'providers' must be a list")
        for index, provider in enumerate(providers):
            if not isinstance(provider, dict):
                raise ValueError(f"Provider entry at index {index} must be an object")
        return list(providers)

    @staticmethod
    def _find_provider(providers: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
        normalized_name = str(name).strip()
        for provider in providers:
            if str(provider.get("name", "")).strip() == normalized_name:
                return provider
        return None

    def _validate_providers(self, providers: List[Dict[str, Any]]) -> None:
        manager = ProviderManager(self._ctx)
        manager.load_providers([self._clone_provider(provider) for provider in providers])

    def _normalize_provider_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        api = str(payload.get("api", "")).strip()
        if not name:
            raise ValueError("Provider name is required")
        if not api:
            raise ValueError("Provider api is required")

        normalized: Dict[str, Any] = {
            "name": name,
            "api": api,
        }

        api_key = self._clean_optional_string(payload.get("api_key"))
        if api_key is not None:
            normalized["api_key"] = api_key

        proxy = self._parse_optional_proxy(payload.get("proxy"))
        if proxy is not None:
            normalized["proxy"] = proxy

        timeout_seconds = self._parse_optional_positive_int(payload.get("timeout_seconds"))
        if timeout_seconds is not None:
            normalized["timeout_seconds"] = timeout_seconds

        max_retries = self._parse_optional_positive_int(payload.get("max_retries"))
        if max_retries is not None:
            normalized["max_retries"] = max_retries

        verify_ssl = self._parse_optional_bool(payload.get("verify_ssl"))
        if verify_ssl is not None:
            normalized["verify_ssl"] = verify_ssl

        model_list = self._normalize_model_list(payload.get("model_list"))
        if model_list:
            normalized["model_list"] = model_list

        hook = self._clean_optional_string(payload.get("hook"))
        if hook is not None:
            normalized["hook"] = hook

        return normalized

    @staticmethod
    def _clean_optional_string(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _parse_optional_positive_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Expected a positive integer") from exc
        if parsed <= 0:
            raise ValueError("Expected a positive integer")
        return parsed

    @staticmethod
    def _parse_optional_proxy(value: Any) -> Optional[str]:
        if value is None:
            return None
        return normalize_proxy_url(value)

    @staticmethod
    def _parse_optional_bool(value: Any) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered == "":
                return None
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
            raise ValueError("Expected a boolean value")
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        raise ValueError("Expected a boolean value")

    def _normalize_model_list(self, value: Any) -> List[str]:
        models: List[str] = []
        if value is None:
            return models

        if isinstance(value, str):
            raw_items = value.replace(",", "\n").splitlines()
        elif isinstance(value, list):
            raw_items = value
        else:
            raise ValueError("model_list must be a list or newline-separated string")

        seen: set[str] = set()
        for item in raw_items:
            model = str(item).strip()
            if not model:
                continue
            if model in seen:
                continue
            seen.add(model)
            models.append(model)
        return models

    @staticmethod
    def _merge_models(existing: List[str], fetched: List[str]) -> List[str]:
        merged = list(existing)
        seen = set(existing)
        for model in fetched:
            if model in seen:
                continue
            seen.add(model)
            merged.append(model)
        return merged

    def _fetch_models_from_upstream(
        self,
        api: str,
        api_key: Optional[str],
        proxy: Optional[str],
        timeout_seconds: int,
        verify_ssl: bool,
    ) -> List[str]:
        headers = {"accept": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
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
                        last_error = f"{url} returned {response.status_code}"
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
                    last_error = f"{url} returned no models"
                except requests.RequestException as exc:
                    last_error = f"{url} request failed: {exc}"
                except ValueError as exc:
                    last_error = f"{url} returned invalid json: {exc}"

        raise ValueError(last_error or "Failed to fetch models")

    @staticmethod
    def _build_model_endpoint_candidates(api: str) -> List[str]:
        cleaned_api = api.strip().rstrip("/")
        parsed = urlparse(cleaned_api)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Provider api must be a valid absolute URL")

        root = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")
        path_prefixes = [""]

        if path:
            path_parts = [segment for segment in path.split("/") if segment]
            for cut in range(len(path_parts), 0, -1):
                current = "/" + "/".join(path_parts[:cut])
                lower_current = current.lower()
                if lower_current.endswith("/chat/completions"):
                    current = current[: -len("/chat/completions")]
                elif lower_current.endswith("/completions"):
                    current = current[: -len("/completions")]
                path_prefixes.append(current.rstrip("/"))
            path_prefixes.append(path)

        candidates: List[str] = []
        seen: set[str] = set()
        for prefix in path_prefixes:
            normalized_prefix = prefix.rstrip("/")
            for suffix in ("/v1/models", "/models"):
                candidate = f"{root}{normalized_prefix}{suffix}"
                if candidate in seen:
                    continue
                seen.add(candidate)
                candidates.append(candidate)
        return candidates

    @staticmethod
    def _extract_models_from_payload(payload: Any) -> List[str]:
        models: List[str] = []
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

    @staticmethod
    def _clone_provider(provider: Dict[str, Any]) -> Dict[str, Any]:
        cloned: Dict[str, Any] = {}
        for key, value in provider.items():
            if isinstance(value, list):
                cloned[key] = list(value)
            elif isinstance(value, dict):
                cloned[key] = dict(value)
            else:
                cloned[key] = value
        return cloned
