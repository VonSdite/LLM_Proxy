#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API Key 服务。"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Iterable, Sequence
from typing import Any

from ..application.app_context import AppContext
from ..repositories import ApiKeyRepository
from ..utils.local_time import normalize_local_datetime_text
from .model_catalog_service import ModelCatalogService


class ApiKeyService:
    """封装 API Key 管理与鉴权逻辑。"""

    MODEL_PERMISSIONS_ALL = ApiKeyRepository.MODEL_PERMISSIONS_ALL
    KEY_PREFIX = "sk-"

    def __init__(
        self,
        ctx: AppContext,
        repository: ApiKeyRepository,
        model_catalog_service: ModelCatalogService | None = None,
    ):
        self._logger = ctx.logger
        self._model_catalog_service = model_catalog_service or ModelCatalogService(ctx)
        self._repository = repository

    @staticmethod
    def _normalize_key_timestamps(api_key: dict[str, Any] | None) -> dict[str, Any] | None:
        """统一 API Key 时间字段格式。"""
        if not api_key:
            return api_key

        normalized = dict(api_key)
        normalized["created_at"] = normalize_local_datetime_text(normalized.get("created_at"))
        normalized["updated_at"] = normalize_local_datetime_text(normalized.get("updated_at"))
        normalized["last_used_at"] = normalize_local_datetime_text(normalized.get("last_used_at"))
        return normalized

    @staticmethod
    def _dedupe_models(model_names: Iterable[Any]) -> list[str]:
        seen_models: set[str] = set()
        normalized_models: list[str] = []
        for item in model_names:
            model_name = str(item or "").strip()
            if not model_name or model_name in seen_models:
                continue
            seen_models.add(model_name)
            normalized_models.append(model_name)
        return normalized_models

    def _get_available_model_names(self) -> tuple[str, ...]:
        """读取模型权限可选目录，包含 Provider 与 OAuth 模型。"""
        return self._model_catalog_service.list_permission_model_names()

    @classmethod
    def _deserialize_model_permissions(cls, raw_value: Any) -> tuple[str, ...] | None:
        """反序列化模型权限；返回 None 表示通配全模型。"""
        normalized_text = str(raw_value or "").strip()
        if not normalized_text or normalized_text == cls.MODEL_PERMISSIONS_ALL:
            return None

        try:
            payload = json.loads(normalized_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = normalized_text.replace(",", "\n").splitlines()

        if isinstance(payload, str):
            payload = [payload]
        if not isinstance(payload, list):
            return ()

        normalized_models = cls._dedupe_models(payload)
        if cls.MODEL_PERMISSIONS_ALL in normalized_models:
            return None
        return tuple(normalized_models)

    def _serialize_model_permissions(self, value: Any) -> str:
        """标准化模型权限存储格式。"""
        if isinstance(value, str):
            normalized_text = value.strip()
            if normalized_text == self.MODEL_PERMISSIONS_ALL:
                return self.MODEL_PERMISSIONS_ALL
            raw_items: Sequence[Any] = normalized_text.replace(",", "\n").splitlines()
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raise ValueError('model_permissions must be "*" or a list of model names')

        normalized_models = self._dedupe_models(raw_items)
        if self.MODEL_PERMISSIONS_ALL in normalized_models:
            return self.MODEL_PERMISSIONS_ALL

        available_models = set(self._get_available_model_names())
        unknown_models = [model for model in normalized_models if model not in available_models]
        if unknown_models:
            raise ValueError(f"Unknown model permission(s): {', '.join(unknown_models)}")

        return json.dumps(normalized_models, ensure_ascii=True)

    def _decorate_api_key(
        self,
        api_key: dict[str, Any] | None,
        *,
        available_models: Sequence[str] | None = None,
    ) -> dict[str, Any] | None:
        """补充模型权限字段并标准化时间格式。"""
        normalized = self._normalize_key_timestamps(api_key)
        if not normalized:
            return normalized

        resolved_available_models = tuple(
            self._get_available_model_names() if available_models is None else available_models
        )
        available_model_set = set(resolved_available_models)
        parsed_permissions = self._deserialize_model_permissions(normalized.get("model_permissions"))

        if parsed_permissions is None:
            normalized["model_permissions"] = self.MODEL_PERMISSIONS_ALL
            normalized["model_permissions_mode"] = "all"
            normalized["allowed_models_count"] = len(resolved_available_models)
        else:
            filtered_permissions = [
                model_name
                for model_name in parsed_permissions
                if not available_model_set or model_name in available_model_set
            ]
            normalized["model_permissions"] = filtered_permissions
            normalized["model_permissions_mode"] = "selected"
            normalized["allowed_models_count"] = len(filtered_permissions)

        normalized["available_models_count"] = len(resolved_available_models)
        normalized["enabled"] = bool(normalized.get("enabled"))
        normalized["token_limit_k"] = self._normalize_token_limit_k(
            normalized.get("token_limit_k"),
            required=False,
        )
        if normalized["token_limit_k"] is None:
            normalized["token_limit_tokens"] = None
            normalized["token_limit_remaining"] = None
        else:
            normalized["token_limit_tokens"] = normalized["token_limit_k"] * 1000
            normalized["token_limit_remaining"] = max(
                normalized["token_limit_tokens"] - int(normalized.get("total_tokens") or 0),
                0,
            )
        for field_name in ("total_request_count", "total_tokens", "prompt_tokens", "completion_tokens"):
            normalized[field_name] = int(normalized.get(field_name) or 0)
        return normalized

    @classmethod
    def hash_api_key(cls, api_key: str) -> str:
        """计算 API Key 的稳定 hash。"""
        return hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()

    @classmethod
    def generate_api_key(cls) -> str:
        """生成以 sk- 开头的 API Key。"""
        return f"{cls.KEY_PREFIX}{secrets.token_urlsafe(36)}"

    @staticmethod
    def _normalize_name(name: Any) -> str:
        normalized = str(name or "").strip()
        return normalized or "API Key"

    @staticmethod
    def _normalize_token_limit_k(value: Any, *, required: bool) -> int | None:
        """标准化 token 使用上限，单位为 k。"""
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None

        try:
            normalized_value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("token_limit_k must be an integer") from exc
        if normalized_value < 1:
            if required:
                raise ValueError("token_limit_k must be at least 1")
            return None
        return normalized_value

    @classmethod
    def _build_key_preview_parts(cls, api_key: str) -> tuple[str, str]:
        key_text = str(api_key or "").strip()
        return key_text[:10], key_text[-6:]

    def get_available_models(self) -> list[str]:
        """返回当前配置中可选的模型列表。"""
        return list(self._get_available_model_names())

    def create_api_key(
        self,
        name: Any = None,
        model_permissions: Any = MODEL_PERMISSIONS_ALL,
        token_limit_k: Any = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """创建 API Key，并持久化明文用于管理端复制和显示。"""
        normalized_name = self._normalize_name(name)
        serialized_model_permissions = self._serialize_model_permissions(model_permissions)
        normalized_token_limit_k = self._normalize_token_limit_k(token_limit_k, required=True)
        for _ in range(5):
            plaintext_key = self.generate_api_key()
            key_hash = self.hash_api_key(plaintext_key)
            key_prefix, key_suffix = self._build_key_preview_parts(plaintext_key)
            key_id = self._repository.create(
                name=normalized_name,
                api_key=plaintext_key,
                key_hash=key_hash,
                key_prefix=key_prefix,
                key_suffix=key_suffix,
                model_permissions=serialized_model_permissions,
                token_limit_k=normalized_token_limit_k,
                enabled=enabled,
            )
            if key_id:
                created = self._repository.get_by_id(int(key_id))
                self._logger.info("API key created: key_id=%s name=%r", key_id, normalized_name)
                decorated = self._decorate_api_key(created)
                if decorated is None:
                    raise RuntimeError("Failed to load created API key")
                return decorated

        raise RuntimeError("Failed to create API key")

    def get_api_key_by_id(self, key_id: int) -> dict[str, Any] | None:
        """按 ID 查询 API Key。"""
        try:
            return self._decorate_api_key(self._repository.get_by_id(key_id))
        except Exception as exc:
            self._logger.error("Failed to get API key: %s", exc)
            return None

    def authenticate_api_key(self, raw_api_key: str) -> dict[str, Any] | None:
        """校验下游请求携带的 API Key。"""
        normalized_key = str(raw_api_key or "").strip()
        if not normalized_key:
            return None

        try:
            api_key = self._repository.get_by_hash(self.hash_api_key(normalized_key))
        except Exception as exc:
            self._logger.error("Failed to authenticate API key: %s", exc)
            return None
        if not api_key or not bool(api_key.get("enabled")):
            return None
        decorated = self._decorate_api_key(api_key)
        if decorated is not None:
            decorated.pop("api_key", None)
        return decorated

    @staticmethod
    def extract_api_key_from_headers(headers: Any) -> str:
        """从下游请求 Header 中提取 API Key。"""
        authorization = str(headers.get("Authorization") or "").strip()
        if authorization:
            parts = authorization.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1].strip()

        return str(headers.get("X-API-Key") or headers.get("X-Api-Key") or "").strip()

    def _get_api_keys_sorted_by_allowed_model_count(
        self,
        page: int,
        page_size: int,
        keyword: str | None,
        sort_direction: str | None,
    ) -> list[dict[str, Any]]:
        """按派生的模型权限数量排序后分页。"""
        available_models = self._get_available_model_names()
        api_keys = self._repository.get_sorted_by_allowed_model_count(
            page=page,
            page_size=page_size,
            keyword=keyword,
            sort_direction=sort_direction,
            available_model_count=len(available_models),
        )
        decorated_keys = [self._decorate_api_key(api_key, available_models=available_models) for api_key in api_keys]
        return [api_key for api_key in decorated_keys if api_key is not None]

    def get_api_keys(
        self,
        page: int = 1,
        page_size: int = 50,
        keyword: str | None = None,
        sort_key: str | None = "created_at",
        sort_direction: str | None = "desc",
    ) -> list[dict[str, Any]]:
        """分页查询 API Key 列表。"""
        try:
            if sort_key == "allowed_models_count":
                return self._get_api_keys_sorted_by_allowed_model_count(
                    page,
                    page_size,
                    keyword,
                    sort_direction,
                )

            available_models = self._get_available_model_names()
            api_keys = self._repository.get(
                page=page,
                page_size=page_size,
                keyword=keyword,
                sort_key=sort_key,
                sort_direction=sort_direction,
            )
            decorated_keys = [self._decorate_api_key(api_key, available_models=available_models) for api_key in api_keys]
            return [api_key for api_key in decorated_keys if api_key is not None]
        except Exception as exc:
            self._logger.error("Failed to get API keys: %s", exc)
            return []

    def get_total_api_keys_count(self, keyword: str | None = None) -> int:
        """查询 API Key 总数。"""
        try:
            return self._repository.get_count(keyword=keyword)
        except Exception as exc:
            self._logger.error("Failed to get API key count: %s", exc)
            return 0

    def update_api_key(
        self,
        key_id: int,
        *,
        name: Any = None,
        enabled: bool | None = None,
        model_permissions_provided: bool = False,
        model_permissions: Any = None,
        token_limit_k_provided: bool = False,
        token_limit_k: Any = None,
    ) -> bool:
        """更新 API Key 信息。"""
        try:
            existing_key = self._repository.get_by_id(key_id)
            if not existing_key:
                return False

            normalized_name = None if name is None else self._normalize_name(name)
            serialized_model_permissions: str | None = None
            if model_permissions_provided:
                serialized_model_permissions = self._serialize_model_permissions(model_permissions)
            normalized_token_limit_k: int | None = None
            if token_limit_k_provided:
                normalized_token_limit_k = self._normalize_token_limit_k(token_limit_k, required=True)

            updated = self._repository.update(
                key_id,
                name=normalized_name,
                enabled=enabled,
                model_permissions=serialized_model_permissions,
                token_limit_k=normalized_token_limit_k,
                token_limit_k_provided=token_limit_k_provided,
            )
            if updated:
                self._logger.info("API key updated: key_id=%s", key_id)
            return updated
        except ValueError:
            raise
        except Exception as exc:
            self._logger.error("Failed to update API key: %s", exc)
            return False

    def delete_api_key(self, key_id: int) -> bool:
        """删除 API Key。"""
        try:
            deleted = self._repository.delete(key_id)
            if deleted:
                self._logger.info("API key deleted: key_id=%s", key_id)
            return deleted
        except Exception as exc:
            self._logger.error("Failed to delete API key: %s", exc)
            return False

    def toggle_api_key_status(self, key_id: int) -> bool:
        """切换 API Key 启用状态。"""
        try:
            api_key = self._repository.get_by_id(key_id)
            if not api_key:
                return False
            updated = self._repository.update(key_id, enabled=not bool(api_key["enabled"]))
            if updated:
                self._logger.info("API key status toggled: key_id=%s enabled=%s", key_id, not bool(api_key["enabled"]))
            return updated
        except Exception as exc:
            self._logger.error("Failed to toggle API key status: %s", exc)
            return False

    def get_accessible_models_for_api_key(
        self,
        api_key: dict[str, Any] | None,
        available_models: Sequence[str] | None = None,
    ) -> list[str]:
        """返回 API Key 在给定模型集合内可访问的模型。"""
        if not api_key:
            return []

        resolved_available_models = list(
            self._get_available_model_names() if available_models is None else available_models
        )
        permissions = api_key.get("model_permissions")
        if permissions == self.MODEL_PERMISSIONS_ALL or api_key.get("model_permissions_mode") == "all":
            return resolved_available_models

        if isinstance(permissions, list):
            explicit_models = self._dedupe_models(permissions)
        else:
            parsed_permissions = self._deserialize_model_permissions(permissions)
            if parsed_permissions is None:
                return resolved_available_models
            explicit_models = list(parsed_permissions)

        if not resolved_available_models:
            return explicit_models

        available_model_set = set(resolved_available_models)
        return [model_name for model_name in explicit_models if model_name in available_model_set]

    def can_api_key_access_model(
        self,
        api_key: dict[str, Any] | None,
        model_name: str,
        available_models: Sequence[str] | None = None,
    ) -> bool:
        """判断 API Key 是否可访问指定模型。"""
        normalized_model_name = str(model_name or "").strip()
        if not normalized_model_name or not api_key:
            return False

        permissions = api_key.get("model_permissions")
        if (
            permissions == self.MODEL_PERMISSIONS_ALL or api_key.get("model_permissions_mode") == "all"
        ) and available_models is None:
            return True

        return normalized_model_name in set(
            self.get_accessible_models_for_api_key(api_key, available_models=available_models)
        )

    def is_token_limit_exceeded(self, api_key: dict[str, Any] | None) -> bool:
        """判断 API Key 当前总 token 用量是否达到上限。"""
        if not api_key:
            return False
        token_limit_k = self._normalize_token_limit_k(api_key.get("token_limit_k"), required=False)
        if token_limit_k is None:
            return False
        return int(api_key.get("total_tokens") or 0) >= token_limit_k * 1000

    def sync_model_permissions(self) -> int:
        """同步并清理已删除模型对应的显式授权。"""
        try:
            available_models = set(self._get_available_model_names())
            updated_count = 0
            for api_key in self._repository.list_all():
                current_raw = str(api_key.get("model_permissions") or "").strip() or self.MODEL_PERMISSIONS_ALL
                parsed_permissions = self._deserialize_model_permissions(current_raw)
                if parsed_permissions is None:
                    expected_raw = self.MODEL_PERMISSIONS_ALL
                else:
                    filtered_permissions = [
                        model_name for model_name in parsed_permissions if model_name in available_models
                    ]
                    expected_raw = json.dumps(filtered_permissions, ensure_ascii=True)

                if current_raw == expected_raw:
                    continue

                if self._repository.update(int(api_key["id"]), model_permissions=expected_raw):
                    updated_count += 1

            if updated_count:
                self._logger.info("API key model permissions synced: updated=%s", updated_count)
            return updated_count
        except Exception as exc:
            self._logger.error("Failed to sync API key model permissions: %s", exc)
            return 0
