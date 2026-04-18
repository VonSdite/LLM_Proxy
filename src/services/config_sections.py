#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务层共享的配置分段读取辅助。"""

from __future__ import annotations

from typing import Any, Mapping


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
