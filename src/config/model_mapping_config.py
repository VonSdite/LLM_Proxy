#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型映射定义与校验。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MODEL_MAPPING_ID_MAX_LENGTH = 64
MODEL_MAPPING_ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
DEFAULT_MODEL_MAPPING_COOLDOWN_SECONDS_ON_429 = 60
DEFAULT_MODEL_MAPPING_TARGET_PRIORITY = 1


def normalize_model_mapping_id(value: Any) -> str:
    """规范化并校验模型映射 ID。"""
    mapping_id = str(value or "").strip()
    if not mapping_id:
        raise ValueError("模型映射 ID 不能为空")
    if len(mapping_id) > MODEL_MAPPING_ID_MAX_LENGTH:
        raise ValueError(f"模型映射 ID 最多 {MODEL_MAPPING_ID_MAX_LENGTH} 个字符")
    if not MODEL_MAPPING_ID_PATTERN.fullmatch(mapping_id):
        raise ValueError("模型映射 ID 只能由英文字母、数字、下划线、点号和连字符组成，且只能以字母或下划线开头")
    return mapping_id


def _parse_positive_int(value: Any, *, default: int, field_label: str) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field_label}必须是正整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_label}必须是正整数") from exc
    if parsed <= 0:
        raise ValueError(f"{field_label}必须是正整数")
    return parsed


@dataclass(frozen=True)
class ModelMappingTargetSchema:
    """一个模型映射目标。"""

    model_id: str
    priority: int = DEFAULT_MODEL_MAPPING_TARGET_PRIORITY
    enabled: bool = True
    sort_order: int = 0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, sort_order: int) -> ModelMappingTargetSchema:
        if not isinstance(payload, Mapping):
            raise ValueError("模型映射目标必须是对象")
        model_id = str(payload.get("model_id") or payload.get("id") or "").strip()
        if not model_id:
            raise ValueError("目标模型 ID 不能为空")
        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("目标启用状态必须是布尔值")
        return cls(
            model_id=model_id,
            priority=_parse_positive_int(
                payload.get("priority"),
                default=DEFAULT_MODEL_MAPPING_TARGET_PRIORITY,
                field_label="目标优先级",
            ),
            enabled=enabled,
            sort_order=sort_order,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "priority": self.priority,
            "enabled": self.enabled,
            "sort_order": self.sort_order,
        }


@dataclass(frozen=True)
class ModelMappingSchema:
    """一个对外模型 ID 及其粘滞故障切换目标。"""

    id: str
    cooldown_seconds_on_429: int
    targets: tuple[ModelMappingTargetSchema, ...]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ModelMappingSchema:
        if not isinstance(payload, Mapping):
            raise ValueError("模型映射必须是对象")
        mapping_id = normalize_model_mapping_id(payload.get("id"))
        raw_targets = payload.get("targets")
        if not isinstance(raw_targets, Sequence) or isinstance(raw_targets, (str, bytes)) or not raw_targets:
            raise ValueError("模型映射至少需要一个目标模型")
        targets = tuple(
            ModelMappingTargetSchema.from_mapping(target, sort_order=index) for index, target in enumerate(raw_targets)
        )
        target_ids = [target.model_id for target in targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("同一个模型映射不能重复选择目标模型")
        return cls(
            id=mapping_id,
            cooldown_seconds_on_429=_parse_positive_int(
                payload.get("cooldown_seconds_on_429"),
                default=DEFAULT_MODEL_MAPPING_COOLDOWN_SECONDS_ON_429,
                field_label="429 冷却时间",
            ),
            targets=targets,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "strategy": "sticky_failover",
            "cooldown_seconds_on_429": self.cooldown_seconds_on_429,
            "targets": [target.to_mapping() for target in self.targets],
        }
