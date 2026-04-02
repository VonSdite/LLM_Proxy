#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 配置管理与模型拉取服务。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..application.app_context import AppContext
from ..config.provider_config import (
    ProviderConfigSchema,
    validate_auth_group_provider_definitions,
)


class ProviderService:
    """负责 provider 配置的增删改查与模型拉取。"""

    def __init__(self, ctx: AppContext, reload_callback: Callable[[], None]):
        self._config_manager = ctx.config_manager
        self._logger = ctx.logger
        self._reload_callback = reload_callback

    def list_providers(self) -> List[Dict[str, Any]]:
        config = self._config_manager.get_raw_config()
        return [
            ProviderConfigSchema.from_mapping(provider).to_mapping()
            for provider in self._extract_providers(config)
        ]

    def get_provider(self, name: str) -> Optional[Dict[str, Any]]:
        config = self._config_manager.get_raw_config()
        providers = self._extract_providers(config)
        provider = self._find_provider(providers, name)
        if provider is None:
            return None
        return ProviderConfigSchema.from_mapping(provider).to_mapping()

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
        target = self._find_provider(providers, current_name)
        if target is None:
            raise ValueError(f'Provider not found: {current_name}')

        normalized_payload = dict(payload)
        if 'enabled' not in normalized_payload:
            normalized_payload['enabled'] = ProviderConfigSchema.from_mapping(target).enabled

        provider_config = ProviderConfigSchema.from_payload(normalized_payload)
        normalized = provider_config.to_mapping()

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

    def set_provider_enabled(self, name: str, enabled: bool) -> Dict[str, Any]:
        config = self._config_manager.get_raw_config()
        providers = self._extract_providers(config)
        target = self._find_provider(providers, name)
        if target is None:
            raise ValueError(f'Provider not found: {name}')

        target_index = providers.index(target)
        normalized = ProviderConfigSchema.from_mapping(target).to_mapping()
        normalized['enabled'] = bool(enabled)
        providers[target_index] = ProviderConfigSchema.from_payload(normalized).to_mapping()
        self._save_providers(config, providers)
        return providers[target_index]

    def batch_set_provider_enabled(self, names: List[str], enabled: bool) -> Dict[str, Any]:
        normalized_names = self._normalize_provider_names(names)
        config = self._config_manager.get_raw_config()
        providers = self._extract_providers(config)
        target_indexes = self._find_provider_indexes(providers, normalized_names)

        for target_index in target_indexes:
            normalized = ProviderConfigSchema.from_mapping(providers[target_index]).to_mapping()
            normalized['enabled'] = bool(enabled)
            providers[target_index] = ProviderConfigSchema.from_payload(normalized).to_mapping()

        self._save_providers(config, providers)
        return {
            'count': len(normalized_names),
            'names': normalized_names,
            'enabled': bool(enabled),
        }

    def batch_delete_providers(self, names: List[str]) -> Dict[str, Any]:
        normalized_names = self._normalize_provider_names(names)
        config = self._config_manager.get_raw_config()
        providers = self._extract_providers(config)
        self._find_provider_indexes(providers, normalized_names)

        name_set = set(normalized_names)
        providers = [
            provider
            for provider in providers
            if str(provider.get('name', '')).strip() not in name_set
        ]
        self._save_providers(config, providers)
        return {
            'count': len(normalized_names),
            'names': normalized_names,
        }

    def _save_providers(self, config: Dict[str, Any], providers: List[Dict[str, Any]]) -> None:
        self._validate_providers(config, providers)
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
    def _extract_auth_groups(config: Dict[str, Any]) -> List[Dict[str, Any]]:
        auth_groups = config.get('auth_groups', [])
        if auth_groups is None:
            auth_groups = []
        if not isinstance(auth_groups, list):
            raise ValueError("Config field 'auth_groups' must be a list")
        for index, auth_group in enumerate(auth_groups):
            if not isinstance(auth_group, dict):
                raise ValueError(f'Auth group entry at index {index} must be an object')
        return list(auth_groups)

    @staticmethod
    def _validate_providers(config: Dict[str, Any], providers: List[Dict[str, Any]]) -> None:
        validate_auth_group_provider_definitions(
            ProviderService._extract_auth_groups(config),
            providers,
        )

    @staticmethod
    def _normalize_provider_names(names: Any) -> List[str]:
        if not isinstance(names, list):
            raise ValueError('Provider names must be a non-empty list')

        normalized_names: List[str] = []
        seen_names = set()
        for raw_name in names:
            name = str(raw_name or '').strip()
            if not name:
                raise ValueError('Provider names must not be empty')
            if name in seen_names:
                continue
            seen_names.add(name)
            normalized_names.append(name)

        if not normalized_names:
            raise ValueError('Provider names must be a non-empty list')
        return normalized_names

    @staticmethod
    def _find_provider_indexes(providers: List[Dict[str, Any]], names: List[str]) -> List[int]:
        indexes_by_name = {
            str(provider.get('name', '')).strip(): index
            for index, provider in enumerate(providers)
        }
        missing_names = [name for name in names if name not in indexes_by_name]
        if missing_names:
            if len(missing_names) == 1:
                raise ValueError(f'Provider not found: {missing_names[0]}')
            raise ValueError(f"Providers not found: {', '.join(missing_names)}")
        return [indexes_by_name[name] for name in names]
