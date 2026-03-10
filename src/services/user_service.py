#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户服务。"""

import threading
from typing import Any, Dict, List, Optional

from ..application.app_context import AppContext
from ..repositories import UserRepository
from ..utils import is_valid_ip


class UserService:
    """封装用户管理业务逻辑。"""

    def __init__(self, ctx: AppContext, repository: UserRepository):
        self._ctx = ctx
        self._logger = ctx.logger
        self._repository = repository
        self._cache_lock = threading.RLock()
        self._user_by_ip_cache: Dict[str, Optional[Dict[str, Any]]] = {}

    def _get_cached_user_by_ip(self, ip_address: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        """读取 IP 缓存，返回是否命中与缓存值。"""
        with self._cache_lock:
            if ip_address not in self._user_by_ip_cache:
                return False, None
            return True, self._user_by_ip_cache[ip_address]

    def _set_cached_user_by_ip(self, ip_address: str, user: Optional[Dict[str, Any]]) -> None:
        """写入 IP 对应缓存。"""
        with self._cache_lock:
            self._user_by_ip_cache[ip_address] = user

    def _invalidate_ip_cache(self, *ip_addresses: Optional[str]) -> None:
        """按 IP 失效缓存。"""
        with self._cache_lock:
            for ip_address in ip_addresses:
                if ip_address:
                    self._user_by_ip_cache.pop(ip_address, None)

    def create_user(self, username: str, ip_address: str) -> Optional[int]:
        """创建用户。"""
        try:
            if not is_valid_ip(ip_address):
                self._logger.error(f'Invalid IP address: {ip_address}')
                return None

            existing_ip = self._repository.get_by_ip(ip_address)
            if existing_ip:
                self._logger.error(f'IP address already in use: {ip_address}')
                return None

            user_id = self._repository.create(username, ip_address)
            self._invalidate_ip_cache(ip_address)
            self._logger.info(f"User created: user_id={user_id}, username={username!r}, ip={ip_address}")
            return user_id
        except Exception as e:
            self._logger.error(f'Failed to create user: {e}')
            return None

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """按 ID 查询用户。"""
        try:
            return self._repository.get_by_id(user_id)
        except Exception as e:
            self._logger.error(f'Failed to get user: {e}')
            return None

    def get_users(self, page: int = 1, page_size: int = 50) -> List[Dict[str, Any]]:
        """分页查询用户列表。"""
        try:
            return self._repository.get(page=page, page_size=page_size)
        except Exception as e:
            self._logger.error(f'Failed to get users: {e}')
            return []

    def get_total_users_count(self) -> int:
        """查询用户总数。"""
        try:
            return self._repository.get_count()
        except Exception as e:
            self._logger.error(f'Failed to get users count: {e}')
            return 0

    def update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        ip_address: Optional[str] = None,
        whitelist_access_enabled: Optional[bool] = None,
    ) -> bool:
        """更新用户信息。"""
        try:
            existing_user = self._repository.get_by_id(user_id)
            if not existing_user:
                return False

            if ip_address:
                existing = self._repository.get_by_ip(ip_address)
                if existing and existing['id'] != user_id:
                    self._logger.error(f'IP address already in use: {ip_address}')
                    return False

            updated = self._repository.update(
                user_id,
                username,
                ip_address,
                whitelist_access_enabled,
            )
            if updated:
                self._invalidate_ip_cache(existing_user.get('ip_address'), ip_address)
                self._logger.info(f"User updated: user_id={user_id}")
            return updated
        except Exception as e:
            self._logger.error(f'Failed to update user: {e}')
            return False

    def delete_user(self, user_id: int) -> bool:
        """删除用户。"""
        try:
            existing_user = self._repository.get_by_id(user_id)
            deleted = self._repository.delete(user_id)
            if deleted and existing_user:
                self._invalidate_ip_cache(existing_user.get('ip_address'))
                self._logger.info(f"User deleted: user_id={user_id}")
            return deleted
        except Exception as e:
            self._logger.error(f'Failed to delete user: {e}')
            return False

    def toggle_user_status(self, user_id: int) -> bool:
        """切换用户白名单状态。"""
        try:
            user = self._repository.get_by_id(user_id)
            if not user:
                return False
            updated = self._repository.update(
                user_id,
                whitelist_access_enabled=not bool(user['whitelist_access_enabled']),
            )
            if updated:
                self._invalidate_ip_cache(user.get('ip_address'))
                self._logger.info(
                    f"User whitelist toggled: user_id={user_id}, enabled={not bool(user['whitelist_access_enabled'])}"
                )
            return updated
        except Exception as e:
            self._logger.error(f'Failed to toggle user status: {e}')
            return False

    def get_user_by_ip(
        self,
        ip_address: str,
        require_whitelist_access: bool = False,
    ) -> Optional[Dict[str, Any]]:
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
            if require_whitelist_access and not bool(cached_user.get('whitelist_access_enabled')):
                return None
            return cached_user
        except Exception as e:
            self._logger.error(f'Failed to get user by IP: {e}')
            return None
