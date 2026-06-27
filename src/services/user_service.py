#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户服务。"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Sequence
from typing import Any

from ..application.app_context import AppContext
from ..repositories import UserRepository
from ..utils import is_valid_ip
from ..utils.local_time import normalize_local_datetime_text
from .model_catalog_service import ModelCatalogService


class UserService:
    """封装用户管理业务逻辑。"""

    MODEL_PERMISSIONS_ALL = UserRepository.MODEL_PERMISSIONS_ALL

    def __init__(
        self,
        ctx: AppContext,
        repository: UserRepository,
        model_catalog_service: ModelCatalogService | None = None,
    ):
        self._logger = ctx.logger
        self._model_catalog_service = model_catalog_service or ModelCatalogService(ctx)
        self._repository = repository
        self._cache_lock = threading.RLock()
        self._user_by_ip_cache: dict[str, dict[str, Any] | None] = {}

    def _get_cached_user_by_ip(self, ip_address: str) -> tuple[bool, dict[str, Any] | None]:
        """读取 IP 缓存，返回是否命中与缓存值。"""
        with self._cache_lock:
            if ip_address not in self._user_by_ip_cache:
                return False, None
            return True, self._user_by_ip_cache[ip_address]

    def _set_cached_user_by_ip(self, ip_address: str, user: dict[str, Any] | None) -> None:
        """写入 IP 对应缓存。"""
        with self._cache_lock:
            self._user_by_ip_cache[ip_address] = user

    def _invalidate_ip_cache(self, *ip_addresses: str | None) -> None:
        """按 IP 失效缓存。"""
        with self._cache_lock:
            for ip_address in ip_addresses:
                if ip_address:
                    self._user_by_ip_cache.pop(ip_address, None)

    @staticmethod
    def _normalize_user_timestamps(user: dict[str, Any] | None) -> dict[str, Any] | None:
        """统一用户时间字段格式。"""
        if not user:
            return user

        normalized = dict(user)
        normalized["created_at"] = normalize_local_datetime_text(normalized.get("created_at"))
        normalized["updated_at"] = normalize_local_datetime_text(normalized.get("updated_at"))
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

    def _decorate_user(
        self,
        user: dict[str, Any] | None,
        *,
        available_models: Sequence[str] | None = None,
    ) -> dict[str, Any] | None:
        """补充模型权限字段并标准化时间格式。"""
        normalized = self._normalize_user_timestamps(user)
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
        return normalized

    def get_available_models(self) -> list[str]:
        """返回当前配置中可选的模型列表。"""
        return list(self._get_available_model_names())

    def create_user(self, username: str, ip_address: str) -> int | None:
        """创建用户。"""
        try:
            if not is_valid_ip(ip_address):
                self._logger.error(f"Invalid IP address: {ip_address}")
                return None

            existing_ip = self._repository.get_by_ip(ip_address)
            if existing_ip:
                self._logger.error(f"IP address already in use: {ip_address}")
                return None

            user_id = self._repository.create(
                username,
                ip_address,
                model_permissions=self.MODEL_PERMISSIONS_ALL,
            )
            self._invalidate_ip_cache(ip_address)
            self._logger.info(f"User created: user_id={user_id}, username={username!r}, ip={ip_address}")
            return user_id
        except Exception as exc:
            self._logger.error(f"Failed to create user: {exc}")
            return None

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        """按 ID 查询用户。"""
        try:
            return self._decorate_user(self._repository.get_by_id(user_id))
        except Exception as exc:
            self._logger.error(f"Failed to get user: {exc}")
            return None

    def _get_users_sorted_by_allowed_model_count(
        self,
        page: int,
        page_size: int,
        keyword: str | None,
        sort_direction: str | None,
    ) -> list[dict[str, Any]]:
        """按派生的模型权限数量排序后分页。"""
        available_models = self._get_available_model_names()
        users = self._repository.get_sorted_by_allowed_model_count(
            page=page,
            page_size=page_size,
            keyword=keyword,
            sort_direction=sort_direction,
            available_model_count=len(available_models),
        )
        decorated_users = [self._decorate_user(user, available_models=available_models) for user in users]
        return [user for user in decorated_users if user is not None]

    def get_users(
        self,
        page: int = 1,
        page_size: int = 50,
        keyword: str | None = None,
        sort_key: str | None = "total_tokens",
        sort_direction: str | None = "desc",
    ) -> list[dict[str, Any]]:
        """分页查询用户列表。"""
        try:
            if sort_key == "allowed_models_count":
                return self._get_users_sorted_by_allowed_model_count(
                    page,
                    page_size,
                    keyword,
                    sort_direction,
                )

            available_models = self._get_available_model_names()
            users = self._repository.get(
                page=page,
                page_size=page_size,
                keyword=keyword,
                sort_key=sort_key,
                sort_direction=sort_direction,
            )
            decorated_users = [self._decorate_user(user, available_models=available_models) for user in users]
            return [user for user in decorated_users if user is not None]
        except Exception as exc:
            self._logger.error(f"Failed to get users: {exc}")
            return []

    def get_total_users_count(self, keyword: str | None = None) -> int:
        """查询用户总数。"""
        try:
            return self._repository.get_count(keyword=keyword)
        except Exception as exc:
            self._logger.error(f"Failed to get users count: {exc}")
            return 0

    def update_user(
        self,
        user_id: int,
        username: str | None = None,
        ip_address: str | None = None,
        whitelist_access_enabled: bool | None = None,
        *,
        model_permissions_provided: bool = False,
        model_permissions: Any = None,
    ) -> bool:
        """更新用户信息。"""
        try:
            existing_user = self._repository.get_by_id(user_id)
            if not existing_user:
                return False

            if ip_address:
                existing = self._repository.get_by_ip(ip_address)
                if existing and existing["id"] != user_id:
                    self._logger.error(f"IP address already in use: {ip_address}")
                    return False

            serialized_model_permissions: str | None = None
            if model_permissions_provided:
                serialized_model_permissions = self._serialize_model_permissions(model_permissions)

            updated = self._repository.update(
                user_id,
                username,
                ip_address,
                whitelist_access_enabled,
                serialized_model_permissions,
            )
            if updated:
                self._invalidate_ip_cache(existing_user.get("ip_address"), ip_address)
                self._logger.info(f"User updated: user_id={user_id}")
            return updated
        except ValueError:
            raise
        except Exception as exc:
            self._logger.error(f"Failed to update user: {exc}")
            return False

    def batch_update_model_permissions(self, user_ids: Any, model_permissions: Any) -> dict[str, Any]:
        """批量统一设置用户模型权限。"""
        normalized_user_ids = self._normalize_user_ids(user_ids)
        existing_users = self._repository.get_by_ids(normalized_user_ids)
        existing_users_by_id = {int(user["id"]): user for user in existing_users}
        missing_user_ids = [user_id for user_id in normalized_user_ids if user_id not in existing_users_by_id]
        if missing_user_ids:
            if len(missing_user_ids) == 1:
                raise ValueError(f"User not found: {missing_user_ids[0]}")
            raise ValueError(f"Users not found: {', '.join(str(user_id) for user_id in missing_user_ids)}")

        serialized_model_permissions = self._serialize_model_permissions(model_permissions)
        self._repository.batch_update_model_permissions(normalized_user_ids, serialized_model_permissions)
        self._invalidate_ip_cache(*(user.get("ip_address") for user in existing_users))

        payload: Any = self.MODEL_PERMISSIONS_ALL
        if serialized_model_permissions != self.MODEL_PERMISSIONS_ALL:
            payload = json.loads(serialized_model_permissions)

        self._logger.info(
            "User model permissions batch updated: count=%s mode=%s",
            len(normalized_user_ids),
            "all" if payload == self.MODEL_PERMISSIONS_ALL else "selected",
        )
        return {
            "count": len(normalized_user_ids),
            "user_ids": normalized_user_ids,
            "model_permissions": payload,
        }

    def delete_user(self, user_id: int) -> bool:
        """删除用户。"""
        try:
            existing_user = self._repository.get_by_id(user_id)
            deleted = self._repository.delete(user_id)
            if deleted and existing_user:
                self._invalidate_ip_cache(existing_user.get("ip_address"))
                self._logger.info(f"User deleted: user_id={user_id}")
            return deleted
        except Exception as exc:
            self._logger.error(f"Failed to delete user: {exc}")
            return False

    def toggle_user_status(self, user_id: int) -> bool:
        """切换用户白名单状态。"""
        try:
            user = self._repository.get_by_id(user_id)
            if not user:
                return False
            updated = self._repository.update(
                user_id,
                whitelist_access_enabled=not bool(user["whitelist_access_enabled"]),
            )
            if updated:
                self._invalidate_ip_cache(user.get("ip_address"))
                self._logger.info(
                    f"User whitelist toggled: user_id={user_id}, enabled={not bool(user['whitelist_access_enabled'])}"
                )
            return updated
        except Exception as exc:
            self._logger.error(f"Failed to toggle user status: {exc}")
            return False

    def get_user_by_ip(
        self,
        ip_address: str,
        require_whitelist_access: bool = False,
    ) -> dict[str, Any] | None:
        """按 IP 查询用户，可选要求白名单开关为启用。"""
        try:
            if not ip_address:
                return None

            hit, cached_user = self._get_cached_user_by_ip(ip_address)
            if not hit:
                cached_user = self._repository.get_by_ip(ip_address)
                self._set_cached_user_by_ip(ip_address, cached_user)

            if not cached_user:
                return None
            if require_whitelist_access and not bool(cached_user.get("whitelist_access_enabled")):
                return None
            return self._decorate_user(cached_user)
        except Exception as exc:
            self._logger.error(f"Failed to get user by IP: {exc}")
            return None

    def get_accessible_models_for_user(
        self,
        user: dict[str, Any] | None,
        available_models: Sequence[str] | None = None,
    ) -> list[str]:
        """返回用户在给定模型集合内可访问的模型。"""
        if not user:
            return []

        resolved_available_models = list(
            self._get_available_model_names() if available_models is None else available_models
        )
        permissions = user.get("model_permissions")
        if permissions == self.MODEL_PERMISSIONS_ALL or user.get("model_permissions_mode") == "all":
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

    def can_user_access_model(
        self,
        user: dict[str, Any] | None,
        model_name: str,
        available_models: Sequence[str] | None = None,
    ) -> bool:
        """判断用户是否可访问指定模型。"""
        normalized_model_name = str(model_name or "").strip()
        if not normalized_model_name or not user:
            return False

        permissions = user.get("model_permissions")
        if (
            permissions == self.MODEL_PERMISSIONS_ALL or user.get("model_permissions_mode") == "all"
        ) and available_models is None:
            return True

        return normalized_model_name in set(
            self.get_accessible_models_for_user(user, available_models=available_models)
        )

    def sync_model_permissions(self) -> int:
        """同步并清理已删除模型对应的显式授权。"""
        try:
            available_models = set(self._get_available_model_names())
            updated_count = 0
            for user in self._repository.list_all():
                current_raw = str(user.get("model_permissions") or "").strip() or self.MODEL_PERMISSIONS_ALL
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

                if self._repository.update(int(user["id"]), model_permissions=expected_raw):
                    updated_count += 1
                    self._invalidate_ip_cache(user.get("ip_address"))

            if updated_count:
                self._logger.info("User model permissions synced: updated=%s", updated_count)
            return updated_count
        except Exception as exc:
            self._logger.error("Failed to sync user model permissions: %s", exc)
            return 0

    @staticmethod
    def _normalize_user_ids(user_ids: Any) -> list[int]:
        """标准化批量操作中的用户 ID 列表。"""
        if not isinstance(user_ids, list):
            raise ValueError("User ids must be a non-empty list")

        normalized_user_ids: list[int] = []
        seen_user_ids: set[int] = set()
        for raw_user_id in user_ids:
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("User ids must be integers") from exc
            if user_id <= 0 or user_id in seen_user_ids:
                continue
            seen_user_ids.add(user_id)
            normalized_user_ids.append(user_id)

        if not normalized_user_ids:
            raise ValueError("User ids must be a non-empty list")
        return normalized_user_ids
