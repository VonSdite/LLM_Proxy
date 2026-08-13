#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型映射管理与目标故障切换服务。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from ..application.app_context import AppContext
from ..config.model_mapping_config import (
    MODEL_MAPPING_ID_MAX_LENGTH,
    MODEL_MAPPING_STRATEGY_STICKY_FAILOVER,
    ModelMappingSchema,
    normalize_model_mapping_id,
)
from ..repositories.model_mapping_repository import ModelMappingRepository
from ..utils.local_time import format_local_datetime, now_local_datetime, parse_local_datetime


class ModelListProvider(Protocol):
    """提供当前可路由模型目录。"""

    def list_model_names(self) -> Iterable[str]: ...


@dataclass(frozen=True)
class SelectedModelMappingTarget:
    """一次模型映射选择结果。"""

    mapping_id: str
    target_model_id: str
    is_fallback: bool = False


class ModelMappingService:
    """管理模型映射定义、运行状态与目标选择。"""

    EXPORT_KIND = "llm_proxy.model_mappings"
    EXPORT_VERSION = 1
    _AUTO_DISABLE_STATUS_CODES = frozenset({404, 405, 410})
    _COOLDOWN_STATUS_CODES = frozenset({401, 403, 408, 425, 429})

    def __init__(
        self,
        ctx: AppContext,
        repository: ModelMappingRepository,
        *,
        provider_manager: ModelListProvider,
        codex_oauth_service: Any | None = None,
        claude_oauth_service: Any | None = None,
    ) -> None:
        self._config_manager = ctx.config_manager
        self._logger = ctx.logger
        self._repository = repository
        self._provider_manager = provider_manager
        self._codex_oauth_service = codex_oauth_service
        self._claude_oauth_service = claude_oauth_service

    def is_enabled(self) -> bool:
        """返回模型映射总开关状态。"""
        read_enabled = getattr(self._config_manager, "is_model_mapping_enabled", None)
        return bool(read_enabled()) if callable(read_enabled) else False

    def list_mapping_ids(self) -> tuple[str, ...]:
        """返回当前生效的映射 ID；映射 ID 可以覆盖同名 OAuth 模型。"""
        if not self.is_enabled():
            return ()
        return tuple(sorted(mapping["id"] for mapping in self._repository.list_mappings() if mapping["enabled"]))

    def list_defined_mapping_ids(self) -> tuple[str, ...]:
        """返回全部已定义映射 ID，供权限持久化目录使用。"""
        return tuple(sorted(mapping["id"] for mapping in self._repository.list_mappings()))

    def list_model_names(self) -> tuple[str, ...]:
        """按模型目录协议返回当前生效映射 ID。"""
        return self.list_mapping_ids()

    def list_image_mapping_ids(self) -> tuple[str, ...]:
        """返回包含 Codex 图片目标的当前生效映射 ID。"""
        if not self.is_enabled():
            return ()
        image_model_ids = set(self._read_oauth_catalog_ids(self._codex_oauth_service, "list_image_models"))
        return tuple(
            sorted(
                mapping["id"]
                for mapping in self._repository.list_mappings()
                if mapping["enabled"] and any(target["model_id"] in image_model_ids for target in mapping["targets"])
            )
        )

    def has_mapping(self, mapping_id: str) -> bool:
        """判断映射 ID 是否可用于路由。"""
        normalized_id = str(mapping_id or "").strip()
        return normalized_id in set(self.list_mapping_ids())

    def list_available_target_model_ids(self) -> tuple[str, ...]:
        """返回可在编辑器中选择的 Provider、OAuth 文本模型与图片模型。"""
        provider_models = tuple(self._provider_manager.list_model_names())
        codex_models = self._read_oauth_catalog_ids(self._codex_oauth_service, "list_models")
        codex_image_models = self._read_oauth_catalog_ids(self._codex_oauth_service, "list_image_models")
        claude_models = self._read_oauth_catalog_ids(self._claude_oauth_service, "list_models")
        return tuple(sorted(dict.fromkeys([*provider_models, *codex_models, *codex_image_models, *claude_models])))

    def list_mappings(self) -> list[dict[str, Any]]:
        """返回包含运行状态的模型映射列表。"""
        available_targets = set(self._list_runtime_target_model_ids())
        conflicts = self._list_oauth_catalog_conflicts()
        return [
            self._enrich_mapping(mapping, available_targets, conflicts) for mapping in self._repository.list_mappings()
        ]

    def get_mapping(self, mapping_id: str) -> dict[str, Any] | None:
        """按 ID 返回包含运行状态的模型映射。"""
        normalized_id = str(mapping_id or "").strip()
        return next((item for item in self.list_mappings() if item["id"] == normalized_id), None)

    def create_mapping(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """创建模型映射。"""
        mapping = self._build_mapping(payload)
        self._validate_target_ids(mapping)
        self._repository.create_mapping(mapping)
        self._group_mapping_order()
        return self.get_mapping(mapping.id) or mapping.to_mapping()

    def update_mapping(self, current_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """更新模型映射并重置当前目标。"""
        normalized_current_id = normalize_model_mapping_id(current_id)
        current_mapping = self._repository.get_mapping(normalized_current_id)
        if current_mapping is None:
            raise ValueError(f"模型映射不存在: {normalized_current_id}")
        normalized_payload = dict(payload)
        normalized_payload.setdefault("enabled", bool(current_mapping["enabled"]))
        mapping = self._build_mapping(normalized_payload)
        self._validate_unavailable_target_changes(current_mapping, mapping)
        self._validate_target_ids(
            mapping,
            additionally_allowed={target["model_id"] for target in current_mapping["targets"]},
        )
        self._repository.update_mapping(normalized_current_id, mapping)
        return self.get_mapping(mapping.id) or mapping.to_mapping()

    def delete_mapping(self, mapping_id: str) -> None:
        """删除模型映射及其运行状态。"""
        self._repository.delete_mapping(normalize_model_mapping_id(mapping_id))

    def copy_mapping(self, mapping_id: str) -> dict[str, Any]:
        """复制模型映射定义，并把副本插入到源映射下方。"""
        normalized_mapping_id = normalize_model_mapping_id(mapping_id)
        source = self._repository.get_mapping(normalized_mapping_id)
        if source is None:
            raise ValueError(f"模型映射不存在: {normalized_mapping_id}")
        existing_ids = {mapping["id"] for mapping in self._repository.list_mappings()}
        copied_id = self._build_unique_mapping_id(normalized_mapping_id, existing_ids)
        copied = self._build_mapping(
            {
                "id": copied_id,
                "enabled": bool(source["enabled"]),
                "strategy": source["strategy"],
                "cooldown_seconds_on_429": source["cooldown_seconds_on_429"],
                "targets": source["targets"],
            }
        )
        self._validate_target_ids(
            copied,
            additionally_allowed={target["model_id"] for target in source["targets"]},
        )
        self._repository.create_mapping_after(normalized_mapping_id, copied)
        return self.get_mapping(copied_id) or copied.to_mapping()

    def set_mapping_enabled(self, mapping_id: str, *, enabled: bool) -> dict[str, Any]:
        """更新映射级启用状态。"""
        normalized_mapping_id = normalize_model_mapping_id(mapping_id)
        if self._repository.get_mapping(normalized_mapping_id) is None:
            raise ValueError(f"模型映射不存在: {normalized_mapping_id}")
        self._repository.set_mapping_enabled(normalized_mapping_id, enabled=enabled)
        self._group_mapping_order()
        mapping = self.get_mapping(normalized_mapping_id)
        if mapping is None:
            raise ValueError(f"模型映射不存在: {normalized_mapping_id}")
        return mapping

    def reorder_mappings(self, mapping_ids: list[str]) -> dict[str, Any]:
        """更新映射顺序，并保持已启用映射位于已禁用映射之前。"""
        normalized_ids = [normalize_model_mapping_id(mapping_id) for mapping_id in mapping_ids]
        if len(normalized_ids) != len(set(normalized_ids)):
            raise ValueError("模型映射顺序不能包含重复 ID")
        mappings = self._repository.list_mappings()
        mapping_by_id = {mapping["id"]: mapping for mapping in mappings}
        if len(normalized_ids) != len(mappings) or set(normalized_ids) != set(mapping_by_id):
            raise ValueError("模型映射顺序必须完整包含每个映射 ID")
        seen_disabled = False
        for mapping_id in normalized_ids:
            if mapping_by_id[mapping_id]["enabled"]:
                if seen_disabled:
                    raise ValueError("已启用模型映射必须排在已禁用模型映射之前")
            else:
                seen_disabled = True
        self._repository.reorder_mappings(normalized_ids)
        return {"count": len(normalized_ids), "ids": normalized_ids}

    def set_target_enabled(self, mapping_id: str, target_model_id: str, *, enabled: bool) -> dict[str, Any]:
        """更新目标配置启用状态；启用时同步清除运行故障状态。"""
        normalized_mapping_id = normalize_model_mapping_id(mapping_id)
        mapping = self._repository.get_mapping(normalized_mapping_id)
        if mapping is None:
            raise ValueError(f"模型映射不存在: {mapping_id}")
        target = next((item for item in mapping["targets"] if item["model_id"] == target_model_id), None)
        if target is None:
            raise ValueError(f"模型映射目标不存在: {target_model_id}")
        if target_model_id not in set(self._list_runtime_target_model_ids()):
            raise ValueError(f"目标模型当前不可用: {target_model_id}")
        self._repository.set_target_enabled(normalized_mapping_id, target_model_id, enabled=enabled)
        if enabled:
            self._repository.restore_target(normalized_mapping_id, target_model_id)
        return self.get_mapping(normalized_mapping_id) or mapping

    def export_mappings(self, mapping_ids: Iterable[str] | None = None) -> dict[str, Any]:
        """导出模型映射定义，不包含运行状态。"""
        requested_ids = [str(item or "").strip() for item in (mapping_ids or ()) if str(item or "").strip()]
        mappings = self._repository.list_mappings()
        if requested_ids:
            mapping_by_id = {mapping["id"]: mapping for mapping in mappings}
            missing_ids = [mapping_id for mapping_id in requested_ids if mapping_id not in mapping_by_id]
            if missing_ids:
                raise ValueError(f"模型映射不存在: {', '.join(missing_ids)}")
            mappings = [mapping_by_id[mapping_id] for mapping_id in requested_ids]
        definitions = [ModelMappingSchema.from_mapping(mapping).to_mapping() for mapping in mappings]
        return {
            "version": self.EXPORT_VERSION,
            "kind": self.EXPORT_KIND,
            "model_mappings": definitions,
        }

    def import_mappings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """事务导入模型映射定义；同名映射拒绝导入。"""
        if not isinstance(payload, Mapping):
            raise ValueError("模型映射导入内容必须是 JSON 对象")
        if payload.get("kind") != self.EXPORT_KIND:
            raise ValueError("模型映射导入文件类型不正确")
        if payload.get("version") != self.EXPORT_VERSION:
            raise ValueError("模型映射导入版本不受支持")
        raw_mappings = payload.get("model_mappings")
        if not isinstance(raw_mappings, list) or not raw_mappings:
            raise ValueError("模型映射导入内容不能为空")
        mappings = [self._build_mapping(item) for item in raw_mappings]
        mapping_ids = [mapping.id for mapping in mappings]
        if len(mapping_ids) != len(set(mapping_ids)):
            raise ValueError("模型映射导入内容包含重复 ID")
        for mapping in mappings:
            self._validate_target_ids(mapping)
        self._repository.import_mappings(mappings)
        self._group_mapping_order()
        return {"count": len(mappings), "ids": mapping_ids}

    def acquire_target(self, mapping_id: str, excluded_target_ids: Iterable[str] = ()) -> SelectedModelMappingTarget:
        """按映射策略选择目标，正常候选为空时始终选择最高优先级运行时目标。"""
        normalized_id = normalize_model_mapping_id(mapping_id)
        if not self.has_mapping(normalized_id):
            raise ValueError(f"模型映射不可用: {normalized_id}")
        mapping = self._repository.get_mapping(normalized_id)
        if mapping is None:
            raise ValueError(f"模型映射不存在: {normalized_id}")
        excluded = set(excluded_target_ids)
        available_runtime_ids = set(self._list_runtime_target_model_ids())
        runtime_states = self._repository.list_target_runtime_states(normalized_id)
        now = now_local_datetime()
        eligible_targets = [
            target
            for target in mapping["targets"]
            if target["model_id"] not in excluded and target["model_id"] in available_runtime_ids
        ]
        candidates = [
            target
            for target in eligible_targets
            if self._is_target_available(target, runtime_states.get(target["model_id"], {}), now)
        ]
        using_failure_fallback = not candidates
        if using_failure_fallback:
            candidates = [target for target in mapping["targets"] if target["model_id"] in available_runtime_ids]
        if not candidates:
            raise ValueError(f"模型映射没有可用目标: {normalized_id}")
        current_target_id = self._repository.get_current_target(normalized_id)
        selected = None
        if mapping["strategy"] == MODEL_MAPPING_STRATEGY_STICKY_FAILOVER and not using_failure_fallback:
            selected = next((target for target in candidates if target["model_id"] == current_target_id), None)
        if selected is None:
            selected = min(candidates, key=lambda item: (-int(item["priority"]), int(item["sort_order"])))
        if selected["model_id"] != current_target_id:
            self._repository.set_current_target(normalized_id, selected["model_id"])
        return SelectedModelMappingTarget(normalized_id, selected["model_id"], is_fallback=using_failure_fallback)

    def record_success(self, selection: SelectedModelMappingTarget) -> None:
        """清理成功目标的运行故障状态并记录当前目标。"""
        mapping = self._repository.get_mapping(selection.mapping_id)
        if mapping is None or not any(target["model_id"] == selection.target_model_id for target in mapping["targets"]):
            return
        self._repository.restore_target(selection.mapping_id, selection.target_model_id)
        self._repository.set_current_target(selection.mapping_id, selection.target_model_id)

    def record_failure(
        self,
        selection: SelectedModelMappingTarget,
        *,
        status_code: int | None,
        error_type: str | None,
        error_message: str | None,
        response_headers: Mapping[str, Any] | None = None,
    ) -> None:
        """按失败类型更新目标状态，并让下一次选择推进目标。"""
        mapping = self._repository.get_mapping(selection.mapping_id)
        if mapping is None:
            return
        if not any(target["model_id"] == selection.target_model_id for target in mapping["targets"]):
            return
        runtime = self._repository.list_target_runtime_states(selection.mapping_id).get(selection.target_model_id, {})
        failure_action = self._classify_failure_action(status_code)
        failure_reason = error_type or (f"http_{status_code}" if status_code is not None else "upstream_error")
        auto_disabled = bool(runtime.get("auto_disabled"))
        disabled_reason = runtime.get("disabled_reason")
        cooldown_until = runtime.get("cooldown_until")
        if failure_action == "auto_disable":
            auto_disabled = True
            disabled_reason = failure_reason
            cooldown_until = None
        elif failure_action == "cooldown" and not auto_disabled:
            cooldown_seconds = self._parse_retry_after_seconds(response_headers)
            if cooldown_seconds is None:
                cooldown_seconds = int(mapping["cooldown_seconds_on_429"])
            cooldown_until = format_local_datetime(now_local_datetime() + timedelta(seconds=cooldown_seconds))
            disabled_reason = failure_reason
        elif not auto_disabled and not cooldown_until:
            disabled_reason = None
        self._repository.save_target_runtime_state(
            selection.mapping_id,
            selection.target_model_id,
            auto_disabled=auto_disabled,
            disabled_reason=disabled_reason,
            cooldown_until=cooldown_until,
            last_status_code=status_code,
            last_error_type=error_type,
            last_error_message=error_message,
        )
        self._repository.set_current_target(selection.mapping_id, None)

    @classmethod
    def _classify_failure_action(cls, status_code: int | None) -> str:
        """区分永久故障、可恢复故障和请求级失败。"""
        if status_code is None or status_code >= 500:
            return "cooldown"
        if 300 <= status_code < 400 or status_code in cls._AUTO_DISABLE_STATUS_CODES:
            return "auto_disable"
        if status_code in cls._COOLDOWN_STATUS_CODES:
            return "cooldown"
        return "record_only"

    def _build_mapping(self, payload: Mapping[str, Any]) -> ModelMappingSchema:
        return ModelMappingSchema.from_mapping(payload)

    @staticmethod
    def _build_unique_mapping_id(base_id: str, existing_ids: set[str]) -> str:
        for index in range(1, 10_000):
            suffix = f"_{index}"
            prefix = base_id[: MODEL_MAPPING_ID_MAX_LENGTH - len(suffix)]
            candidate = f"{prefix}{suffix}"
            if candidate not in existing_ids:
                return candidate
        raise ValueError("无法生成唯一的模型映射 ID")

    def _group_mapping_order(self) -> None:
        mappings = self._repository.list_mappings()
        grouped_ids = [mapping["id"] for mapping in mappings if mapping["enabled"]]
        grouped_ids.extend(mapping["id"] for mapping in mappings if not mapping["enabled"])
        self._repository.reorder_mappings(grouped_ids)

    def _validate_target_ids(
        self,
        mapping: ModelMappingSchema,
        *,
        additionally_allowed: set[str] | None = None,
    ) -> None:
        available_ids = set(self.list_available_target_model_ids()) | (additionally_allowed or set())
        missing_ids = [target.model_id for target in mapping.targets if target.model_id not in available_ids]
        if missing_ids:
            raise ValueError(f"目标模型 ID 不存在: {', '.join(missing_ids)}")

    def _validate_unavailable_target_changes(
        self,
        current_mapping: Mapping[str, Any],
        updated_mapping: ModelMappingSchema,
    ) -> None:
        """不可用目标保留时维持原配置，只允许从映射中删除。"""
        runtime_ids = set(self._list_runtime_target_model_ids())
        current_targets = {target["model_id"]: target for target in current_mapping["targets"]}
        for target in updated_mapping.targets:
            current_target = current_targets.get(target.model_id)
            if (
                current_target is not None
                and target.model_id not in runtime_ids
                and (
                    bool(current_target["enabled"]) != target.enabled
                    or int(current_target["priority"]) != target.priority
                )
            ):
                raise ValueError(f"目标模型当前不可用，只能删除: {target.model_id}")

    def _enrich_mapping(
        self,
        mapping: dict[str, Any],
        available_targets: set[str],
        conflicts: dict[str, str],
    ) -> dict[str, Any]:
        runtime_states = self._repository.list_target_runtime_states(mapping["id"])
        current_target_id = self._repository.get_current_target(mapping["id"])
        now = now_local_datetime()
        targets = []
        for target in mapping["targets"]:
            runtime = runtime_states.get(target["model_id"], {})
            cooldown_until = parse_local_datetime(runtime.get("cooldown_until"))
            is_cooling_down = cooldown_until is not None and cooldown_until > now
            is_available_model = target["model_id"] in available_targets
            if not is_available_model:
                status = "unavailable"
            elif not target["enabled"]:
                status = "disabled"
            elif runtime.get("auto_disabled"):
                status = "auto_disabled"
            elif is_cooling_down:
                status = "cooldown"
            else:
                status = "available"
            targets.append(
                {
                    **target,
                    **runtime,
                    "available_model": is_available_model,
                    "status": status,
                    "current": target["model_id"] == current_target_id,
                }
            )
        conflict_source = conflicts.get(mapping["id"])
        return {
            **mapping,
            "effective": self.is_enabled() and bool(mapping["enabled"]),
            "conflict_source": conflict_source,
            "current_target_model_id": current_target_id,
            "targets": targets,
        }

    @staticmethod
    def _is_target_available(target: Mapping[str, Any], runtime: Mapping[str, Any], now: datetime) -> bool:
        if not target.get("enabled") or runtime.get("auto_disabled"):
            return False
        cooldown_until = parse_local_datetime(runtime.get("cooldown_until"))
        return cooldown_until is None or cooldown_until <= now

    def _list_runtime_target_model_ids(self) -> tuple[str, ...]:
        provider_ids = tuple(self._provider_manager.list_model_names())
        codex_ids = self._safe_list_runtime_ids(self._codex_oauth_service, "list_model_names")
        codex_image_ids = self._safe_list_runtime_ids(self._codex_oauth_service, "list_image_model_names")
        claude_ids = self._safe_list_runtime_ids(self._claude_oauth_service, "list_model_names")
        return tuple(sorted(dict.fromkeys([*provider_ids, *codex_ids, *codex_image_ids, *claude_ids])))

    def _list_oauth_catalog_conflicts(self) -> dict[str, str]:
        conflicts: dict[str, str] = {}
        for model_id in self._read_oauth_catalog_ids(self._codex_oauth_service, "list_models"):
            conflicts.setdefault(model_id, "Codex OAuth")
        for model_id in self._read_oauth_catalog_ids(self._codex_oauth_service, "list_image_models"):
            conflicts.setdefault(model_id, "Codex OAuth 图片")
        for model_id in self._read_oauth_catalog_ids(self._claude_oauth_service, "list_models"):
            conflicts.setdefault(model_id, "Claude OAuth")
        return conflicts

    @staticmethod
    def _read_oauth_catalog_ids(service: Any | None, method_name: str) -> tuple[str, ...]:
        method = getattr(service, method_name, None)
        if not callable(method):
            return ()
        payload = method()
        raw_models = payload.get("models", []) if isinstance(payload, dict) else []
        return tuple(
            str(item.get("id") or "").strip()
            for item in raw_models
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        )

    @staticmethod
    def _safe_list_runtime_ids(service: Any | None, method_name: str) -> tuple[str, ...]:
        method = getattr(service, method_name, None)
        if not callable(method):
            return ()
        try:
            return tuple(str(item).strip() for item in method() if str(item).strip())
        except Exception:
            return ()

    @staticmethod
    def _parse_retry_after_seconds(response_headers: Mapping[str, Any] | None) -> int | None:
        if not response_headers:
            return None
        retry_after = next(
            (value for key, value in response_headers.items() if str(key).lower() == "retry-after"),
            None,
        )
        if retry_after in (None, ""):
            return None
        try:
            seconds = int(str(retry_after).strip())
            return seconds if seconds > 0 else None
        except (TypeError, ValueError):
            pass
        try:
            retry_at = parsedate_to_datetime(str(retry_after).strip())
        except (TypeError, ValueError, IndexError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        seconds = int((retry_at - datetime.now(timezone.utc)).total_seconds())
        return seconds if seconds > 0 else None
