#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auth group configuration and runtime management."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

import yaml

from ..application.app_context import AppContext
from ..config import AuthGroupManager
from ..config.provider_config import (
    AuthEntrySchema,
    AuthGroupSchema,
    validate_auth_group_provider_definitions,
)


class AuthGroupService:
    """Manage auth group config and expose runtime state."""

    def __init__(
        self,
        ctx: AppContext,
        reload_callback: Callable[[], None],
        auth_group_manager: AuthGroupManager,
    ):
        self._config_manager = ctx.config_manager
        self._logger = ctx.logger
        self._reload_callback = reload_callback
        self._auth_group_manager = auth_group_manager

    def list_auth_groups(self) -> List[Dict[str, Any]]:
        config = self._config_manager.get_raw_config()
        auth_groups = self._extract_auth_groups(config)
        providers = self._extract_providers(config)
        summaries = {
            item["name"]: item
            for item in self._auth_group_manager.list_auth_group_summaries()
        }
        provider_counts = self._count_providers_by_auth_group(providers)
        result: List[Dict[str, Any]] = []
        for auth_group in auth_groups:
            normalized = AuthGroupSchema.from_mapping(auth_group).to_mapping()
            normalized["summary"] = summaries.get(normalized["name"], {}).get("summary", {})
            normalized["provider_count"] = provider_counts.get(normalized["name"], 0)
            result.append(normalized)
        return result

    def get_auth_group(self, name: str) -> Optional[Dict[str, Any]]:
        config = self._config_manager.get_raw_config()
        auth_groups = self._extract_auth_groups(config)
        providers = self._extract_providers(config)
        target = self._find_auth_group(auth_groups, name)
        if target is None:
            return None
        normalized = AuthGroupSchema.from_mapping(target).to_mapping()
        summaries = {
            item["name"]: item
            for item in self._auth_group_manager.list_auth_group_summaries()
        }
        normalized["summary"] = summaries.get(normalized["name"], {}).get("summary", {})
        normalized["provider_count"] = self._count_providers_by_auth_group(providers).get(normalized["name"], 0)
        return normalized

    def create_auth_group(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._config_manager.get_raw_config()
        auth_groups = self._extract_auth_groups(config)
        providers = self._extract_providers(config)
        auth_group = AuthGroupSchema.from_mapping(payload)
        normalized = auth_group.to_mapping()

        if self._find_auth_group(auth_groups, auth_group.name):
            raise ValueError(f"Auth group already exists: {auth_group.name}")

        auth_groups.append(normalized)
        self._save_auth_groups(config, auth_groups, providers)
        return normalized

    def update_auth_group(self, current_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        config = self._config_manager.get_raw_config()
        auth_groups = self._extract_auth_groups(config)
        providers = self._extract_providers(config)
        auth_group = AuthGroupSchema.from_mapping(payload)
        normalized = auth_group.to_mapping()

        target = self._find_auth_group(auth_groups, current_name)
        if target is None:
            raise ValueError(f"Auth group not found: {current_name}")

        duplicate = self._find_auth_group(auth_groups, auth_group.name)
        if duplicate is not None and duplicate is not target:
            raise ValueError(f"Auth group already exists: {auth_group.name}")

        if auth_group.name != str(current_name).strip():
            for provider in providers:
                if str(provider.get("auth_group") or "").strip() == str(current_name).strip():
                    provider["auth_group"] = auth_group.name

        auth_groups[auth_groups.index(target)] = normalized
        self._save_auth_groups(config, auth_groups, providers)
        return normalized

    def delete_auth_group(self, name: str) -> None:
        config = self._config_manager.get_raw_config()
        auth_groups = self._extract_auth_groups(config)
        providers = self._extract_providers(config)
        target = self._find_auth_group(auth_groups, name)
        if target is None:
            raise ValueError(f"Auth group not found: {name}")

        if any(str(provider.get("auth_group") or "").strip() == str(name).strip() for provider in providers):
            raise ValueError(f"Auth group is still referenced by providers: {name}")

        auth_groups.remove(target)
        self._save_auth_groups(config, auth_groups, providers)

    def get_auth_group_runtime(self, name: str) -> Dict[str, Any]:
        runtime = self._auth_group_manager.get_auth_group_runtime(name)
        config = self._config_manager.get_raw_config()
        providers = self._extract_providers(config)
        runtime["provider_count"] = self._count_providers_by_auth_group(providers).get(runtime["name"], 0)
        return runtime

    def restore_entry(self, auth_group_name: str, entry_id: str) -> None:
        self._auth_group_manager.restore_entry(auth_group_name, entry_id)

    def clear_entry_cooldown(self, auth_group_name: str, entry_id: str) -> None:
        self._auth_group_manager.clear_entry_cooldown(auth_group_name, entry_id)

    def set_entry_disabled(self, auth_group_name: str, entry_id: str, *, disabled: bool) -> None:
        self._auth_group_manager.set_entry_disabled(auth_group_name, entry_id, disabled=disabled)

    def reset_entry_minute_usage(self, auth_group_name: str, entry_id: str) -> None:
        self._auth_group_manager.reset_entry_minute_usage(auth_group_name, entry_id)

    def reset_entry_runtime(self, auth_group_name: str, entry_id: str) -> None:
        self._auth_group_manager.reset_entry_runtime(auth_group_name, entry_id)

    def import_auth_entries(self, yaml_text: str) -> List[Dict[str, Any]]:
        raw_text = str(yaml_text or "").strip()
        if not raw_text:
            raise ValueError("YAML 内容不能为空")

        try:
            payload = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise ValueError(f"YAML 解析失败: {exc}") from exc

        entries_payload = self._normalize_import_payload(payload)
        if not entries_payload:
            raise ValueError("请至少导入一个 Auth Entry")

        seen_entry_ids: set[str] = set()
        normalized_entries: List[Dict[str, Any]] = []
        for item in entries_payload:
            entry = AuthEntrySchema.from_mapping(item)
            if entry.id in seen_entry_ids:
                raise ValueError(f"检测到重复的 Auth Entry ID: {entry.id}")
            seen_entry_ids.add(entry.id)
            normalized_entries.append(entry.to_mapping())
        return normalized_entries

    def _save_auth_groups(
        self,
        config: Dict[str, Any],
        auth_groups: List[Dict[str, Any]],
        providers: List[Dict[str, Any]],
    ) -> None:
        validate_auth_group_provider_definitions(auth_groups, providers)
        config["auth_groups"] = auth_groups
        config["providers"] = providers
        self._config_manager.write_raw_config(config)
        self._reload_callback()

    @staticmethod
    def _extract_auth_groups(config: Dict[str, Any]) -> List[Dict[str, Any]]:
        auth_groups = config.get("auth_groups", [])
        if auth_groups is None:
            auth_groups = []
        if not isinstance(auth_groups, list):
            raise ValueError("Config field 'auth_groups' must be a list")
        for index, auth_group in enumerate(auth_groups):
            if not isinstance(auth_group, dict):
                raise ValueError(f"Auth group entry at index {index} must be an object")
        return list(auth_groups)

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
    def _find_auth_group(auth_groups: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
        normalized_name = str(name).strip()
        for auth_group in auth_groups:
            if str(auth_group.get("name", "")).strip() == normalized_name:
                return auth_group
        return None

    @staticmethod
    def _count_providers_by_auth_group(providers: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for provider in providers:
            auth_group = str(provider.get("auth_group") or "").strip()
            if not auth_group:
                continue
            counts[auth_group] = counts.get(auth_group, 0) + 1
        return counts

    @staticmethod
    def _normalize_import_payload(payload: Any) -> List[Mapping[str, Any]]:
        entries: Any
        if isinstance(payload, list):
            entries = payload
        elif isinstance(payload, Mapping):
            if "entries" in payload:
                entries = payload.get("entries")
            else:
                entries = [payload]
        else:
            raise ValueError("YAML 必须是 Auth Entry 列表，或包含 entries 字段的对象")

        if not isinstance(entries, list):
            raise ValueError("YAML 中的 entries 必须是列表")

        normalized_entries: List[Mapping[str, Any]] = []
        for index, item in enumerate(entries):
            if not isinstance(item, Mapping):
                raise ValueError(f"第 {index + 1} 个 Auth Entry 必须是对象")
            normalized_entries.append(item)
        return normalized_entries
