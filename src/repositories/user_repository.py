#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户仓储。"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..utils.database import ConnectionFactory


class UserRepository:
    """负责 users 表的数据访问。"""

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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_ip ON users(ip_address)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")

    def create(self, username: str, ip_address: str) -> Optional[int]:
        """创建用户记录。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users (username, ip_address, whitelist_access_enabled, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (username, ip_address, 1, datetime.now()),
            )
            return cursor.lastrowid

    def get_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """按 ID 查询用户。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, username, ip_address, whitelist_access_enabled, created_at, updated_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get(self, page: int = 1, page_size: int = 50) -> List[Dict[str, Any]]:
        """分页查询用户及其聚合统计。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            offset = (page - 1) * page_size
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.ip_address,
                    u.whitelist_access_enabled,
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
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_count(self) -> int:
        """查询用户总数。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS count FROM users")
            return cursor.fetchone()["count"]

    def update(
        self,
        user_id: int,
        username: Optional[str] = None,
        ip_address: Optional[str] = None,
        whitelist_access_enabled: Optional[bool] = None,
    ) -> bool:
        """更新用户记录。"""
        updates = []
        params = []

        if username is not None:
            updates.append("username = ?")
            params.append(username)
        if ip_address is not None:
            updates.append("ip_address = ?")
            params.append(ip_address)
        if whitelist_access_enabled is not None:
            updates.append("whitelist_access_enabled = ?")
            params.append(1 if whitelist_access_enabled else 0)

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(datetime.now())
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
                SELECT id, username, ip_address, whitelist_access_enabled, created_at, updated_at
                FROM users
                WHERE ip_address = ?
                """,
                (ip_address,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
