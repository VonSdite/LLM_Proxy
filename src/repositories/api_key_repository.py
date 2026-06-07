#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API Key 仓储。"""

from __future__ import annotations

from typing import Any

from ..utils.database import ConnectionFactory
from ..utils.local_time import now_local_datetime_text


class ApiKeyRepository:
    """负责 api_keys 表的数据访问。"""

    MODEL_PERMISSIONS_ALL = "*"
    _SORT_COLUMNS = {
        "id": "k.id",
        "name": "k.name",
        "enabled": "k.enabled",
        "created_at": "k.created_at",
        "updated_at": "k.updated_at",
        "last_used_at": "COALESCE(k.last_used_at, '')",
        "token_limit_k": "COALESCE(k.token_limit_k, 0)",
        "total_request_count": "COALESCE(k.total_request_count, 0)",
        "total_tokens": "COALESCE(k.total_tokens, 0)",
        "prompt_tokens": "COALESCE(k.prompt_tokens, 0)",
        "completion_tokens": "COALESCE(k.completion_tokens, 0)",
    }

    def __init__(self, get_connection: ConnectionFactory):
        self._get_connection = get_connection
        self._ensure_table()

    def _ensure_table(self) -> None:
        """初始化 API Key 表与索引。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    key_prefix TEXT NOT NULL,
                    key_suffix TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    model_permissions TEXT NOT NULL DEFAULT '*',
                    token_limit_k INTEGER,
                    total_request_count INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {str(row["name"]).strip() for row in cursor.execute("PRAGMA table_info(api_keys)").fetchall()}
            if "api_key" not in columns:
                cursor.execute("ALTER TABLE api_keys ADD COLUMN api_key TEXT")
            if "model_permissions" not in columns:
                cursor.execute("ALTER TABLE api_keys ADD COLUMN model_permissions TEXT NOT NULL DEFAULT '*'")
            if "token_limit_k" not in columns:
                cursor.execute("ALTER TABLE api_keys ADD COLUMN token_limit_k INTEGER")
            if "total_request_count" not in columns:
                cursor.execute("ALTER TABLE api_keys ADD COLUMN total_request_count INTEGER NOT NULL DEFAULT 0")
            if "total_tokens" not in columns:
                cursor.execute("ALTER TABLE api_keys ADD COLUMN total_tokens INTEGER NOT NULL DEFAULT 0")
            if "prompt_tokens" not in columns:
                cursor.execute("ALTER TABLE api_keys ADD COLUMN prompt_tokens INTEGER NOT NULL DEFAULT 0")
            if "completion_tokens" not in columns:
                cursor.execute("ALTER TABLE api_keys ADD COLUMN completion_tokens INTEGER NOT NULL DEFAULT 0")
            if "last_used_at" not in columns:
                cursor.execute("ALTER TABLE api_keys ADD COLUMN last_used_at TEXT")
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_plaintext ON api_keys(api_key) WHERE api_key IS NOT NULL"
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_name ON api_keys(name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_enabled ON api_keys(enabled)")

    @staticmethod
    def _select_columns() -> str:
        return """
            k.id,
            k.name,
            k.api_key,
            k.key_prefix,
            k.key_suffix,
            k.enabled,
            k.model_permissions,
            k.token_limit_k,
            k.total_request_count,
            k.total_tokens,
            k.prompt_tokens,
            k.completion_tokens,
            k.last_used_at,
            k.created_at,
            k.updated_at,
            k.key_prefix || '...' || k.key_suffix AS key_preview
        """

    def create(
        self,
        *,
        name: str,
        api_key: str,
        key_hash: str,
        key_prefix: str,
        key_suffix: str,
        model_permissions: str = MODEL_PERMISSIONS_ALL,
        token_limit_k: int | None = None,
        enabled: bool = True,
    ) -> int | None:
        """创建 API Key 记录。"""
        now_text = now_local_datetime_text()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO api_keys (
                    name, api_key, key_hash, key_prefix, key_suffix, enabled, model_permissions,
                    token_limit_k, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    api_key,
                    key_hash,
                    key_prefix,
                    key_suffix,
                    1 if enabled else 0,
                    model_permissions,
                    token_limit_k,
                    now_text,
                    now_text,
                ),
            )
            return cursor.lastrowid

    @staticmethod
    def _build_search_clause(keyword: str | None, table_alias: str = "k") -> tuple[str, list[Any]]:
        """构造 API Key 搜索条件，支持名称和展示片段模糊匹配。"""
        normalized = (keyword or "").strip()
        if not normalized:
            return "", []

        like_keyword = f"%{normalized}%"
        return (
            f"""
            WHERE (
                {table_alias}.name LIKE ?
                OR COALESCE({table_alias}.api_key, '') LIKE ?
                OR {table_alias}.key_prefix LIKE ?
                OR {table_alias}.key_suffix LIKE ?
            )
            """,
            [like_keyword, like_keyword, like_keyword, like_keyword],
        )

    @staticmethod
    def _normalize_sort_direction(
        sort_direction: str | None,
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
        sort_key: str | None,
        sort_direction: str | None,
        default_key: str = "created_at",
    ) -> str:
        """基于白名单字段构造 ORDER BY 子句。"""
        normalized_key = str(sort_key or default_key).strip()
        if normalized_key not in cls._SORT_COLUMNS:
            normalized_key = default_key

        direction = cls._normalize_sort_direction(sort_direction)
        return f"ORDER BY {cls._SORT_COLUMNS[normalized_key]} {direction}, k.id ASC"

    def get(
        self,
        page: int = 1,
        page_size: int = 50,
        keyword: str | None = None,
        sort_key: str | None = None,
        sort_direction: str | None = None,
    ) -> list[dict[str, Any]]:
        """分页查询 API Key 列表。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            offset = (page - 1) * page_size
            where_clause, search_params = self._build_search_clause(keyword, table_alias="k")
            order_clause = self._build_order_clause(sort_key, sort_direction)
            cursor.execute(
                f"""
                SELECT {self._select_columns()}
                FROM api_keys k
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
        keyword: str | None,
        sort_direction: str | None,
        available_model_count: int,
    ) -> list[dict[str, Any]]:
        """按模型权限数量排序后分页查询 API Key。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            offset = (page - 1) * page_size
            where_clause, search_params = self._build_search_clause(keyword, table_alias="k")
            direction = self._normalize_sort_direction(sort_direction)
            allowed_count_expression = """
                CASE
                    WHEN TRIM(COALESCE(k.model_permissions, '')) IN ('', '*') THEN ?
                    WHEN json_valid(k.model_permissions)
                        AND json_type(k.model_permissions) = 'array'
                        THEN json_array_length(k.model_permissions)
                    ELSE 0
                END
            """
            cursor.execute(
                f"""
                SELECT {self._select_columns()}
                FROM api_keys k
                {where_clause}
                ORDER BY {allowed_count_expression} {direction}, k.id ASC
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

    def get_count(self, keyword: str | None = None) -> int:
        """查询 API Key 总数。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            where_clause, search_params = self._build_search_clause(keyword, table_alias="k")
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM api_keys k
                {where_clause}
                """,
                search_params,
            )
            return cursor.fetchone()["count"]

    def get_by_id(self, key_id: int) -> dict[str, Any] | None:
        """按 ID 查询 API Key。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT {self._select_columns()}
                FROM api_keys k
                WHERE k.id = ?
                """,
                (key_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        """按 key hash 查询 API Key。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT {self._select_columns()}
                FROM api_keys k
                WHERE k.key_hash = ?
                """,
                (key_hash,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        """查询全部 API Key，用于权限同步。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT {self._select_columns()}
                FROM api_keys k
                ORDER BY k.id ASC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def update(
        self,
        key_id: int,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        model_permissions: str | None = None,
        token_limit_k: int | None = None,
        token_limit_k_provided: bool = False,
    ) -> bool:
        """更新 API Key 记录。"""
        updates: list[str] = []
        params: list[Any] = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if model_permissions is not None:
            updates.append("model_permissions = ?")
            params.append(model_permissions)
        if token_limit_k_provided:
            updates.append("token_limit_k = ?")
            params.append(token_limit_k)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(now_local_datetime_text())
        params.append(key_id)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE api_keys SET {', '.join(updates)} WHERE id = ?", params)
            return cursor.rowcount > 0

    def delete(self, key_id: int) -> bool:
        """删除 API Key 记录。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
            return cursor.rowcount > 0

    def record_usage(
        self,
        key_id: int,
        *,
        total_tokens: int,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> bool:
        """累加 API Key 用量。"""
        now_text = now_local_datetime_text()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE api_keys
                SET
                    total_request_count = total_request_count + 1,
                    total_tokens = total_tokens + ?,
                    prompt_tokens = prompt_tokens + ?,
                    completion_tokens = completion_tokens + ?,
                    last_used_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    int(total_tokens or 0),
                    int(prompt_tokens or 0),
                    int(completion_tokens or 0),
                    now_text,
                    now_text,
                    key_id,
                ),
            )
            return cursor.rowcount > 0
