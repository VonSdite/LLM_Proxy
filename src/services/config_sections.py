#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务层共享的配置分段读取辅助。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..config.provider_config import AuthGroupSchema


def merge_auth_group(current: AuthGroupSchema, incoming: AuthGroupSchema) -> dict[str, Any]:
    """保留同名认证组的现有设置，只补入缺少的凭据条目。"""
    merged = current.to_mapping()
    existing_ids = {entry.id for entry in current.entries}
    merged["entries"].extend(entry.to_mapping() for entry in incoming.entries if entry.id not in existing_ids)
    return AuthGroupSchema.from_mapping(merged).to_mapping()


def read_provider_entries(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """读取原始配置中的 provider 列表。"""
    return _read_object_list(
        config,
        section_name="providers",
        item_label="Provider entry",
    )


def read_auth_group_entries(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """读取原始配置中的 Auth Group 列表。"""
    return _read_object_list(
        config,
        section_name="auth_groups",
        item_label="Auth group entry",
    )


def _read_object_list(
    config: Mapping[str, Any],
    *,
    section_name: str,
    item_label: str,
) -> list[dict[str, Any]]:
    raw_items = config.get(section_name, [])
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise ValueError(f"Config field '{section_name}' must be a list")

    normalized_items: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ValueError(f"{item_label} at index {index} must be an object")
        normalized_items.append(item)
    return normalized_items
