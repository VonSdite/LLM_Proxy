#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 配置管理与模型拉取服务。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..application.app_context import AppContext
from ..config.provider_config import (
    ProviderConfigSchema,
    validate_provider_definitions,
)


class ProviderService:
    """负责 provider 配置的增删改查与模型拉取。"""

    def __init__(self, ctx: AppContext, reload_callback: Callable[[], None]):
        self._config_manager = ctx.config_manager
        self._logger = ctx.logger
        self._reload_callback = reload_callback

    def list_providers(self) -> List[Dict[str, Any]]:
        config = self._config_manager.get_raw_config()
        return self._extract_providers(config)

    def get_provider(self, name: str) -> Optional[Dict[str, Any]]:
        config = self._config_manager.get_raw_config()
        providers = self._extract_providers(config)
        return self._find_provider(providers, name)

    def create_provider(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._config_manager.get_raw_config()
        providers = self._extract_providers(config)
        provider_config = ProviderConfigSchema.from_payload(payload)
        normalized = provider_config.to_mapping()

        if self._find_provider(providers, provider_config.name):
            raise ValueError(f"Provider already exists: {provider_config.name}")

        providers.append(normalized)
        self._save_providers(config, providers)
        return normalized

    def update_provider(self, current_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._config_manager.get_raw_config()
        providers = self._extract_providers(config)
        provider_config = ProviderConfigSchema.from_payload(payload)
        normalized = provider_config.to_mapping()

        target = self._find_provider(providers, current_name)
        if target is None:
            raise ValueError(f'Provider not found: {current_name}')

        duplicate = self._find_provider(providers, provider_config.name)
        if duplicate is not None and duplicate is not target:
            raise ValueError(f"Provider already exists: {provider_config.name}")

        providers[providers.index(target)] = normalized
        self._save_providers(config, providers)
        return normalized

    def delete_provider(self, name: str) -> None:
        config = self._config_manager.get_raw_config()
        providers = self._extract_providers(config)
        target = self._find_provider(providers, name)
        if target is None:
            raise ValueError(f'Provider not found: {name}')

        providers.remove(target)
        self._save_providers(config, providers)

    def _save_providers(self, config: Dict[str, Any], providers: List[Dict[str, Any]]) -> None:
        self._validate_providers(providers)
        config['providers'] = providers
        self._config_manager.write_raw_config(config)
        self._reload_callback()

    @staticmethod
    def _extract_providers(config: Dict[str, Any]) -> List[Dict[str, Any]]:
        providers = config.get('providers', [])
        if providers is None:
            providers = []
        if not isinstance(providers, list):
            raise ValueError("Config field 'providers' must be a list")
        for index, provider in enumerate(providers):
            if not isinstance(provider, dict):
                raise ValueError(f'Provider entry at index {index} must be an object')
        return list(providers)

    @staticmethod
    def _find_provider(providers: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
        normalized_name = str(name).strip()
        for provider in providers:
            if str(provider.get('name', '')).strip() == normalized_name:
                return provider
        return None

    @staticmethod
    def _validate_providers(providers: List[Dict[str, Any]]) -> None:
        validate_provider_definitions(providers)
