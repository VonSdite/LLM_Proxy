#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户仓储。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..utils.database import ConnectionFactory
from ..utils.local_time import now_local_datetime_text


class UserRepository:
    """负责 users 表的数据访问。"""

    MODEL_PERMISSIONS_ALL = "*"
    _SORT_COLUMNS = {
        "id": "u.id",
        "username": "u.username",
        "ip_address": "u.ip_address",
        "whitelist_access_enabled": "u.whitelist_access_enabled",
        "created_at": "u.created_at",
        "total_request_count": "COALESCE(s.total_request_count, 0)",
        "total_tokens": "COALESCE(s.total_tokens, 0)",
        "prompt_tokens": "COALESCE(s.prompt_tokens, 0)",
        "completion_tokens": "COALESCE(s.completion_tokens, 0)",
    }

    def __init__(self, get_connection: ConnectionFactory):
        self._get_connection = get_connection
        self._ensure_table()

    def _ensure_table(self) -> None:
        """初始化用户表与索引。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    ip_address TEXT NOT NULL UNIQUE,
                    whitelist_access_enabled INTEGER DEFAULT 1,
                    model_permissions TEXT NOT NULL DEFAULT '*',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {str(row["name"]).strip() for row in cursor.execute("PRAGMA table_info(users)").fetchall()}
            if "model_permissions" not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN model_permissions TEXT NOT NULL DEFAULT '*'")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_ip ON users(ip_address)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")

    def create(
        self,
        username: str,
        ip_address: str,
        model_permissions: str = MODEL_PERMISSIONS_ALL,
    ) -> Optional[int]:
        """创建用户记录。"""
        now_text = now_local_datetime_text()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users (
                    username, ip_address, whitelist_access_enabled, model_permissions, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (username, ip_address, 1, model_permissions, now_text, now_text),
            )
            return cursor.lastrowid

    def get_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """按 ID 查询用户。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, username, ip_address, whitelist_access_enabled, model_permissions, created_at, updated_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def _build_search_clause(self, keyword: Optional[str], table_alias: str = "u") -> tuple[str, list[Any]]:
        """构造用户搜索条件，支持用户名和 IP 模糊匹配。"""
        normalized = (keyword or "").strip()
        if not normalized:
            return "", []

        like_keyword = f"%{normalized}%"
        return (
            f"WHERE ({table_alias}.username LIKE ? OR {table_alias}.ip_address LIKE ?)",
            [like_keyword, like_keyword],
        )

    @staticmethod
    def _normalize_sort_direction(
        sort_direction: Optional[str],
        default_direction: str = "desc",
    ) -> str:
        """标准化排序方向，仅允许 asc/desc。"""
        normalized = str(sort_direction or default_direction).strip().lower()
        if normalized not in {"asc", "desc"}:
            normalized = default_direction
        return normalized.upper()

    @classmethod
    def _build_order_clause(
        cls,
        sort_key: Optional[str],
        sort_direction: Optional[str],
        default_key: str = "created_at",
    ) -> str:
        """基于白名单字段构造 ORDER BY 子句。"""
        normalized_key = str(sort_key or default_key).strip()
        if normalized_key not in cls._SORT_COLUMNS:
            normalized_key = default_key

        direction = cls._normalize_sort_direction(sort_direction)
        return f"ORDER BY {cls._SORT_COLUMNS[normalized_key]} {direction}, u.id ASC"

    def get(
        self,
        page: int = 1,
        page_size: int = 50,
        keyword: Optional[str] = None,
        sort_key: Optional[str] = None,
        sort_direction: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """分页查询用户及其聚合统计。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            offset = (page - 1) * page_size
            where_clause, search_params = self._build_search_clause(keyword, table_alias="u")
            order_clause = self._build_order_clause(sort_key, sort_direction)
            cursor.execute(
                f"""
                SELECT
                    u.id,
                    u.username,
                    u.ip_address,
                    u.whitelist_access_enabled,
                    u.model_permissions,
                    u.created_at,
                    u.updated_at,
                    COALESCE(s.total_request_count, 0) AS total_request_count,
                    COALESCE(s.total_tokens, 0) AS total_tokens,
                    COALESCE(s.prompt_tokens, 0) AS prompt_tokens,
                    COALESCE(s.completion_tokens, 0) AS completion_tokens
                FROM users u
                LEFT JOIN (
                    SELECT
                        ip_address,
                        SUM(request_count) AS total_request_count,
                        SUM(total_tokens) AS total_tokens,
                        SUM(prompt_tokens) AS prompt_tokens,
                        SUM(completion_tokens) AS completion_tokens
                    FROM daily_request_stats
                    GROUP BY ip_address
                ) s ON u.ip_address = s.ip_address
                {where_clause}
                {order_clause}
                LIMIT ? OFFSET ?
                """,
                (*search_params, page_size, offset),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_sorted_by_allowed_model_count(
        self,
        page: int,
        page_size: int,
        keyword: Optional[str],
        sort_direction: Optional[str],
        available_model_count: int,
    ) -> List[Dict[str, Any]]:
        """按模型权限数量排序后分页查询用户。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            offset = (page - 1) * page_size
            where_clause, search_params = self._build_search_clause(keyword, table_alias="u")
            direction = self._normalize_sort_direction(sort_direction)
            allowed_count_expression = """
                CASE
                    WHEN TRIM(COALESCE(u.model_permissions, '')) IN ('', '*') THEN ?
                    WHEN json_valid(u.model_permissions)
                        AND json_type(u.model_permissions) = 'array'
                        THEN json_array_length(u.model_permissions)
                    ELSE 0
                END
            """
            cursor.execute(
                f"""
                SELECT
                    u.id,
                    u.username,
                    u.ip_address,
                    u.whitelist_access_enabled,
                    u.model_permissions,
                    u.created_at,
                    u.updated_at,
                    COALESCE(s.total_request_count, 0) AS total_request_count,
                    COALESCE(s.total_tokens, 0) AS total_tokens,
                    COALESCE(s.prompt_tokens, 0) AS prompt_tokens,
                    COALESCE(s.completion_tokens, 0) AS completion_tokens
                FROM users u
                LEFT JOIN (
                    SELECT
                        ip_address,
                        SUM(request_count) AS total_request_count,
                        SUM(total_tokens) AS total_tokens,
                        SUM(prompt_tokens) AS prompt_tokens,
                        SUM(completion_tokens) AS completion_tokens
                    FROM daily_request_stats
                    GROUP BY ip_address
                ) s ON u.ip_address = s.ip_address
                {where_clause}
                ORDER BY {allowed_count_expression} {direction}, u.id ASC
                LIMIT ? OFFSET ?
                """,
                (
                    *search_params,
                    int(available_model_count),
                    page_size,
                    offset,
                ),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_count(self, keyword: Optional[str] = None) -> int:
        """查询用户总数。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            where_clause, search_params = self._build_search_clause(keyword, table_alias="u")
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM users u
                {where_clause}
                """,
                search_params,
            )
            return cursor.fetchone()["count"]

    def update(
        self,
        user_id: int,
        username: Optional[str] = None,
        ip_address: Optional[str] = None,
        whitelist_access_enabled: Optional[bool] = None,
        model_permissions: Optional[str] = None,
    ) -> bool:
        """更新用户记录。"""
        updates: list[str] = []
        params: list[Any] = []

        if username is not None:
            updates.append("username = ?")
            params.append(username)
        if ip_address is not None:
            updates.append("ip_address = ?")
            params.append(ip_address)
        if whitelist_access_enabled is not None:
            updates.append("whitelist_access_enabled = ?")
            params.append(1 if whitelist_access_enabled else 0)
        if model_permissions is not None:
            updates.append("model_permissions = ?")
            params.append(model_permissions)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(now_local_datetime_text())
        params.append(user_id)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            return cursor.rowcount > 0

    def delete(self, user_id: int) -> bool:
        """删除用户记录。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0

    def get_by_ip(self, ip_address: str) -> Optional[Dict[str, Any]]:
        """按 IP 查询用户。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, username, ip_address, whitelist_access_enabled, model_permissions, created_at, updated_at
                FROM users
                WHERE ip_address = ?
                """,
                (ip_address,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_by_ids(self, user_ids: List[int]) -> List[Dict[str, Any]]:
        """按 ID 列表查询用户。"""
        normalized_user_ids = [int(user_id) for user_id in user_ids]
        if not normalized_user_ids:
            return []

        placeholders = ", ".join("?" for _ in normalized_user_ids)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, username, ip_address, whitelist_access_enabled, model_permissions, created_at, updated_at
                FROM users
                WHERE id IN ({placeholders})
                """,
                normalized_user_ids,
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_all(self) -> List[Dict[str, Any]]:
        """查询全部用户，用于权限同步与批量设置。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, username, ip_address, whitelist_access_enabled, model_permissions, created_at, updated_at
                FROM users
                ORDER BY id ASC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def batch_update_model_permissions(self, user_ids: List[int], model_permissions: str) -> int:
        """批量更新用户模型权限。"""
        normalized_user_ids = [int(user_id) for user_id in user_ids]
        if not normalized_user_ids:
            return 0

        placeholders = ", ".join("?" for _ in normalized_user_ids)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE users
                SET model_permissions = ?, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [model_permissions, now_local_datetime_text(), *normalized_user_ids],
            )
            return cursor.rowcount
