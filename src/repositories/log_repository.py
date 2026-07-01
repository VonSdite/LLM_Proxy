#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""请求日志仓储。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ..utils.database import ConnectionFactory
from ..utils.local_time import (
    ensure_local_datetime,
    format_local_date,
    format_local_datetime,
    now_local_datetime_text,
)


class LogRepository:
    """负责 request_logs 与 daily_request_stats 的数据访问。"""

    _STATISTICS_SORT_COLUMNS = {
        "ip_address": "COALESCE(d.ip_address, '')",
        "username": "COALESCE(u.username, '-')",
        "request_model": "d.request_model",
        "response_model": "NULLIF(d.response_model, '')",
        "request_count": "COALESCE(SUM(d.request_count), 0)",
        "total_tokens": "COALESCE(SUM(d.total_tokens), 0)",
        "prompt_tokens": "COALESCE(SUM(d.prompt_tokens), 0)",
        "completion_tokens": "COALESCE(SUM(d.completion_tokens), 0)",
    }

    _USER_USAGE_SORT_COLUMNS = {
        "username": "COALESCE(NULLIF(u.username, ''), d.ip_address, '-')",
        "request_count": "COALESCE(SUM(d.request_count), 0)",
        "total_tokens": "COALESCE(SUM(d.total_tokens), 0)",
        "prompt_tokens": "COALESCE(SUM(d.prompt_tokens), 0)",
        "completion_tokens": "COALESCE(SUM(d.completion_tokens), 0)",
        "ip_count": "COUNT(DISTINCT d.ip_address)",
        "last_request_date": "MAX(d.stat_date)",
    }

    _LOG_SORT_COLUMNS = {
        "ip_address": "COALESCE(l.ip_address, '')",
        "username": "COALESCE(u.username, '-')",
        "request_model": "l.request_model",
        "response_model": "COALESCE(l.response_model, '')",
        "total_tokens": "COALESCE(l.total_tokens, 0)",
        "prompt_tokens": "COALESCE(l.prompt_tokens, 0)",
        "completion_tokens": "COALESCE(l.completion_tokens, 0)",
        "start_time": "l.start_time",
        "end_time": "COALESCE(l.end_time, '')",
        "duration": "MAX(COALESCE((julianday(l.end_time) - julianday(l.start_time)) * 86400.0, 0), 0)",
    }

    def __init__(self, get_connection: ConnectionFactory):
        self._get_connection = get_connection
        self._ensure_table()

    def _ensure_table(self) -> None:
        """初始化日志相关数据表与索引。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS request_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_key_id INTEGER,
                    ip_address TEXT,
                    request_model TEXT NOT NULL,
                    response_model TEXT,
                    total_tokens INTEGER,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            request_log_columns = {
                str(row["name"]).strip() for row in cursor.execute("PRAGMA table_info(request_logs)").fetchall()
            }
            if "api_key_id" not in request_log_columns:
                cursor.execute("ALTER TABLE request_logs ADD COLUMN api_key_id INTEGER")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_start_time ON request_logs(start_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ip_address ON request_logs(ip_address)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_api_key ON request_logs(api_key_id)")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_request_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stat_date TEXT NOT NULL,
                    ip_address TEXT,
                    request_model TEXT NOT NULL,
                    response_model TEXT NOT NULL DEFAULT '',
                    request_count INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(stat_date, ip_address, request_model, response_model)
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_request_stats(stat_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_stats_ip ON daily_request_stats(ip_address)")

    def insert(
        self,
        request_model: str,
        response_model: str | None,
        total_tokens: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        start_time: object | None = None,
        end_time: object | None = None,
        ip_address: str | None = None,
        api_key_id: int | None = None,
    ) -> int | None:
        """写入单条请求日志，并同步更新日聚合统计。"""
        start_time_value = ensure_local_datetime(start_time)
        end_time_value = ensure_local_datetime(end_time)
        now_text = now_local_datetime_text()
        safe_total_tokens = int(total_tokens or 0)
        safe_prompt_tokens = int(prompt_tokens or 0)
        safe_completion_tokens = int(completion_tokens or 0)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            response_model_key = response_model or ""
            cursor.execute(
                """
                INSERT INTO request_logs
                (api_key_id, ip_address, request_model, response_model, total_tokens,
                 prompt_tokens, completion_tokens, start_time, end_time, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    api_key_id,
                    ip_address,
                    request_model,
                    response_model,
                    safe_total_tokens,
                    safe_prompt_tokens,
                    safe_completion_tokens,
                    format_local_datetime(start_time_value),
                    format_local_datetime(end_time_value),
                    now_text,
                ),
            )
            log_id = cursor.lastrowid
            stat_date = format_local_date(start_time_value)
            cursor.execute(
                """
                INSERT INTO daily_request_stats
                (
                    stat_date, ip_address, request_model, response_model,
                    request_count, total_tokens, prompt_tokens, completion_tokens,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(stat_date, ip_address, request_model, response_model)
                DO UPDATE SET
                    request_count = request_count + 1,
                    total_tokens = total_tokens + excluded.total_tokens,
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens,
                    updated_at = excluded.updated_at
                """,
                (
                    stat_date,
                    ip_address,
                    request_model,
                    response_model_key,
                    safe_total_tokens,
                    safe_prompt_tokens,
                    safe_completion_tokens,
                    now_text,
                    now_text,
                ),
            )
            if api_key_id is not None:
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
                        safe_total_tokens,
                        safe_prompt_tokens,
                        safe_completion_tokens,
                        now_text,
                        now_text,
                        int(api_key_id),
                    ),
                )
            return log_id

    @staticmethod
    def _normalize_filter_values(values: str | Sequence[str] | None) -> list[str]:
        if values is None:
            return []

        candidates = [values] if isinstance(values, str) else list(values)
        normalized: list[str] = []
        for value in candidates:
            text = str(value or "").strip()
            if text:
                normalized.append(text)
        return normalized

    @classmethod
    def _append_text_filter(
        cls,
        conditions: list[str],
        params: list[Any],
        column_name: str,
        values: str | Sequence[str] | None,
    ) -> None:
        normalized_values = cls._normalize_filter_values(values)
        if not normalized_values:
            return

        if len(normalized_values) == 1:
            conditions.append(f"{column_name} = ?")
            params.append(normalized_values[0])
            return

        placeholders = ", ".join("?" for _ in normalized_values)
        conditions.append(f"{column_name} IN ({placeholders})")
        params.extend(normalized_values)

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
        sort_columns: dict[str, str],
        sort_key: str | None,
        sort_direction: str | None,
        default_key: str,
        tie_breaker: str,
    ) -> str:
        """基于白名单字段构造 ORDER BY 子句。"""
        normalized_key = str(sort_key or default_key).strip()
        if normalized_key not in sort_columns:
            normalized_key = default_key

        direction = cls._normalize_sort_direction(sort_direction)
        return f"ORDER BY {sort_columns[normalized_key]} {direction}, {tie_breaker}"

    def get_statistics(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        username: str | Sequence[str] | None = None,
        request_model: str | Sequence[str] | None = None,
        sort_key: str | None = None,
        sort_direction: str | None = None,
    ) -> list[sqlite3.Row]:
        """按条件查询聚合统计。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            conditions = []
            params = []

            if start_date:
                conditions.append("d.stat_date >= ?")
                params.append(start_date)
            if end_date:
                conditions.append("d.stat_date <= ?")
                params.append(end_date)
            self._append_text_filter(conditions, params, "u.username", username)
            self._append_text_filter(conditions, params, "d.request_model", request_model)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            order_clause = self._build_order_clause(
                self._STATISTICS_SORT_COLUMNS,
                sort_key,
                sort_direction,
                "total_tokens",
                "COALESCE(d.ip_address, '') ASC, d.request_model ASC, NULLIF(d.response_model, '') ASC",
            )
            query = f"""
                SELECT
                    d.ip_address,
                    COALESCE(u.username, '-') as username,
                    d.request_model,
                    NULLIF(d.response_model, '') as response_model,
                    COALESCE(SUM(d.request_count), 0) as request_count,
                    COALESCE(SUM(d.total_tokens), 0) as total_tokens,
                    COALESCE(SUM(d.prompt_tokens), 0) as prompt_tokens,
                    COALESCE(SUM(d.completion_tokens), 0) as completion_tokens
                FROM daily_request_stats d
                LEFT JOIN users u ON d.ip_address = u.ip_address
                WHERE {where_clause}
                GROUP BY d.ip_address, u.username, d.request_model, d.response_model
                {order_clause}
            """
            cursor.execute(query, params)
            return cursor.fetchall()

    def get_user_usage_summary(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        username: str | Sequence[str] | None = None,
        request_model: str | Sequence[str] | None = None,
        sort_key: str | None = None,
        sort_direction: str | None = None,
    ) -> list[sqlite3.Row]:
        """按用户名查询用量汇总。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            conditions = []
            params = []

            if start_date:
                conditions.append("d.stat_date >= ?")
                params.append(start_date)
            if end_date:
                conditions.append("d.stat_date <= ?")
                params.append(end_date)
            self._append_text_filter(conditions, params, "u.username", username)
            self._append_text_filter(conditions, params, "d.request_model", request_model)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            order_clause = self._build_order_clause(
                self._USER_USAGE_SORT_COLUMNS,
                sort_key,
                sort_direction,
                "total_tokens",
                "COALESCE(NULLIF(u.username, ''), d.ip_address, '-') ASC",
            )
            query = f"""
                SELECT
                    COALESCE(NULLIF(u.username, ''), d.ip_address, '-') as username,
                    COALESCE(SUM(d.request_count), 0) as request_count,
                    COALESCE(SUM(d.total_tokens), 0) as total_tokens,
                    COALESCE(SUM(d.prompt_tokens), 0) as prompt_tokens,
                    COALESCE(SUM(d.completion_tokens), 0) as completion_tokens,
                    COUNT(DISTINCT d.ip_address) as ip_count,
                    MAX(d.stat_date) as last_request_date
                FROM daily_request_stats d
                LEFT JOIN users u ON d.ip_address = u.ip_address
                WHERE {where_clause}
                GROUP BY COALESCE(NULLIF(u.username, ''), d.ip_address, '-')
                {order_clause}
            """
            cursor.execute(query, params)
            return cursor.fetchall()

    def get_logs(
        self,
        page: int = 1,
        page_size: int = 50,
        start_date: str | None = None,
        end_date: str | None = None,
        username: str | Sequence[str] | None = None,
        request_model: str | Sequence[str] | None = None,
        sort_key: str | None = None,
        sort_direction: str | None = None,
    ) -> dict[str, Any]:
        """按条件分页查询原始请求日志。"""
        offset = (page - 1) * page_size

        with self._get_connection() as conn:
            cursor = conn.cursor()
            conditions = []
            params = []

            if start_date:
                conditions.append("l.start_time >= ?")
                params.append(f"{start_date} 00:00:00.000000")
            if end_date:
                conditions.append('l.start_time < DATE(?, "+1 day")')
                params.append(end_date)
            self._append_text_filter(conditions, params, "u.username", username)
            self._append_text_filter(conditions, params, "l.request_model", request_model)

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            count_query = (
                "SELECT COUNT(*) as total FROM request_logs l "
                "LEFT JOIN users u ON l.ip_address = u.ip_address "
                f"WHERE {where_clause}"
            )
            cursor.execute(count_query, params)
            total = cursor.fetchone()["total"]

            order_clause = self._build_order_clause(
                self._LOG_SORT_COLUMNS,
                sort_key,
                sort_direction,
                "start_time",
                "l.id DESC",
            )
            data_query = f"""
                SELECT l.id, l.ip_address, COALESCE(u.username, '-') as username, l.request_model, l.response_model,
                       l.total_tokens, l.prompt_tokens, l.completion_tokens,
                       l.start_time, l.end_time, l.created_at
                FROM request_logs l
                LEFT JOIN users u ON l.ip_address = u.ip_address
                WHERE {where_clause}
                {order_clause}
                LIMIT ? OFFSET ?
            """
            cursor.execute(data_query, params + [page_size, offset])
            logs = cursor.fetchall()

            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size,
                "logs": [dict(log) for log in logs],
            }

    def get_all_logs(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        username: str | Sequence[str] | None = None,
        request_model: str | Sequence[str] | None = None,
        sort_key: str | None = None,
        sort_direction: str | None = None,
    ) -> list[sqlite3.Row]:
        """按条件查询完整请求日志列表。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            conditions = []
            params = []

            if start_date:
                conditions.append("l.start_time >= ?")
                params.append(f"{start_date} 00:00:00.000000")
            if end_date:
                conditions.append('l.start_time < DATE(?, "+1 day")')
                params.append(end_date)
            self._append_text_filter(conditions, params, "u.username", username)
            self._append_text_filter(conditions, params, "l.request_model", request_model)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            order_clause = self._build_order_clause(
                self._LOG_SORT_COLUMNS,
                sort_key,
                sort_direction,
                "start_time",
                "l.id DESC",
            )
            query = f"""
                SELECT l.id, l.ip_address, COALESCE(u.username, '-') as username, l.request_model, l.response_model,
                       l.total_tokens, l.prompt_tokens, l.completion_tokens,
                       l.start_time, l.end_time, l.created_at
                FROM request_logs l
                LEFT JOIN users u ON l.ip_address = u.ip_address
                WHERE {where_clause}
                {order_clause}
            """
            cursor.execute(query, params)
            return cursor.fetchall()

    def export_request_logs(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        username: str | Sequence[str] | None = None,
        request_model: str | Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按条件导出请求明细原始行。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            conditions = []
            params: list[Any] = []

            if start_date:
                conditions.append("l.start_time >= ?")
                params.append(f"{start_date} 00:00:00.000000")
            if end_date:
                conditions.append('l.start_time < DATE(?, "+1 day")')
                params.append(end_date)
            self._append_text_filter(conditions, params, "u.username", username)
            self._append_text_filter(conditions, params, "l.request_model", request_model)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            cursor.execute(
                f"""
                SELECT
                    l.id,
                    l.api_key_id,
                    l.ip_address,
                    l.request_model,
                    l.response_model,
                    l.total_tokens,
                    l.prompt_tokens,
                    l.completion_tokens,
                    l.start_time,
                    l.end_time,
                    l.created_at
                FROM request_logs l
                LEFT JOIN users u ON l.ip_address = u.ip_address
                WHERE {where_clause}
                ORDER BY l.start_time ASC, l.id ASC
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

    def export_daily_stats(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        username: str | Sequence[str] | None = None,
        request_model: str | Sequence[str] | None = None,
        ip_address: str | Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按条件导出日聚合统计原始行。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            conditions = []
            params: list[Any] = []

            if start_date:
                conditions.append("d.stat_date >= ?")
                params.append(start_date)
            if end_date:
                conditions.append("d.stat_date <= ?")
                params.append(end_date)
            self._append_text_filter(conditions, params, "u.username", username)
            self._append_text_filter(conditions, params, "d.request_model", request_model)
            self._append_text_filter(conditions, params, "d.ip_address", ip_address)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            cursor.execute(
                f"""
                SELECT
                    d.stat_date,
                    d.ip_address,
                    d.request_model,
                    d.response_model,
                    d.request_count,
                    d.total_tokens,
                    d.prompt_tokens,
                    d.completion_tokens
                FROM daily_request_stats d
                LEFT JOIN users u ON d.ip_address = u.ip_address
                WHERE {where_clause}
                ORDER BY d.stat_date ASC, COALESCE(d.ip_address, '') ASC,
                    d.request_model ASC, d.response_model ASC
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

    def import_daily_stats(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """导入日聚合统计，遇到相同唯一维度时累加数值。"""
        now_text = now_local_datetime_text()
        normalized_rows = [self._normalize_daily_stat_row(row) for row in rows]
        inserted_count = 0
        updated_count = 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for row in normalized_rows:
                if self._daily_stat_exists(cursor, row):
                    updated_count += 1
                    self._update_daily_stat(cursor, row, now_text)
                    continue
                else:
                    inserted_count += 1

                cursor.execute(
                    """
                    INSERT INTO daily_request_stats
                    (
                        stat_date, ip_address, request_model, response_model,
                        request_count, total_tokens, prompt_tokens, completion_tokens,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stat_date, ip_address, request_model, response_model)
                    DO UPDATE SET
                        request_count = request_count + excluded.request_count,
                        total_tokens = total_tokens + excluded.total_tokens,
                        prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                        completion_tokens = completion_tokens + excluded.completion_tokens,
                        updated_at = excluded.updated_at
                    """,
                    (
                        row["stat_date"],
                        row["ip_address"],
                        row["request_model"],
                        row["response_model"],
                        row["request_count"],
                        row["total_tokens"],
                        row["prompt_tokens"],
                        row["completion_tokens"],
                        now_text,
                        now_text,
                    ),
                )

        return {
            "count": len(normalized_rows),
            "inserted_count": inserted_count,
            "updated_count": updated_count,
            "merged_count": updated_count,
        }

    def import_request_logs(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """导入请求明细，遇到相同明细数据时跳过。"""
        now_text = now_local_datetime_text()
        normalized_rows = [self._normalize_request_log_row(row, default_created_at=now_text) for row in rows]
        inserted_count = 0
        skipped_count = 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for row in normalized_rows:
                if self._request_log_exists(cursor, row):
                    skipped_count += 1
                    continue

                cursor.execute(
                    """
                    INSERT INTO request_logs
                    (
                        api_key_id, ip_address, request_model, response_model, total_tokens,
                        prompt_tokens, completion_tokens, start_time, end_time, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["api_key_id"],
                        row["ip_address"],
                        row["request_model"],
                        row["response_model"],
                        row["total_tokens"],
                        row["prompt_tokens"],
                        row["completion_tokens"],
                        row["start_time"],
                        row["end_time"],
                        row["created_at"],
                    ),
                )
                inserted_count += 1

        return {
            "count": len(normalized_rows),
            "inserted_count": inserted_count,
            "skipped_count": skipped_count,
            "duplicate_count": skipped_count,
        }

    def get_unique_request_models(self) -> list[str]:
        """查询有日志记录的请求模型列表。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT request_model
                FROM request_logs
                WHERE request_model IS NOT NULL AND request_model != ''
                ORDER BY request_model
                """
            )
            return [row["request_model"] for row in cursor.fetchall()]

    def get_unique_usernames(self) -> list[str]:
        """查询有日志记录的用户名列表。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT u.username
                FROM users u
                INNER JOIN request_logs l ON l.ip_address = u.ip_address
                ORDER BY u.username
                """
            )
            return [row["username"] for row in cursor.fetchall()]

    @staticmethod
    def _normalize_stat_date(value: Any) -> str:
        text = str(value or "").strip()
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("daily stat stat_date must use YYYY-MM-DD") from exc
        return text

    @staticmethod
    def _normalize_stat_text(value: Any, *, field_name: str, required: bool) -> str | None:
        if value is None:
            if required:
                raise ValueError(f"daily stat {field_name} is required")
            return None
        text = str(value).strip()
        if not text and required:
            raise ValueError(f"daily stat {field_name} is required")
        return text or None

    @staticmethod
    def _normalize_stat_int(value: Any, *, field_name: str) -> int:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"daily stat {field_name} must be an integer") from exc
        if parsed < 0:
            raise ValueError(f"daily stat {field_name} must be greater than or equal to 0")
        return parsed

    @classmethod
    def _normalize_daily_stat_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(row, dict):
            raise ValueError("Each daily stat row must be an object")
        response_model = cls._normalize_stat_text(
            row.get("response_model"), field_name="response_model", required=False
        )
        return {
            "stat_date": cls._normalize_stat_date(row.get("stat_date")),
            "ip_address": cls._normalize_stat_text(row.get("ip_address"), field_name="ip_address", required=False),
            "request_model": cls._normalize_stat_text(
                row.get("request_model"), field_name="request_model", required=True
            ),
            "response_model": response_model or "",
            "request_count": cls._normalize_stat_int(row.get("request_count"), field_name="request_count"),
            "total_tokens": cls._normalize_stat_int(row.get("total_tokens"), field_name="total_tokens"),
            "prompt_tokens": cls._normalize_stat_int(row.get("prompt_tokens"), field_name="prompt_tokens"),
            "completion_tokens": cls._normalize_stat_int(row.get("completion_tokens"), field_name="completion_tokens"),
        }

    @classmethod
    def _normalize_request_log_row(cls, row: dict[str, Any], *, default_created_at: str) -> dict[str, Any]:
        if not isinstance(row, dict):
            raise ValueError("Each request log row must be an object")
        return {
            "api_key_id": cls._normalize_optional_positive_int(row.get("api_key_id"), field_name="api_key_id"),
            "ip_address": cls._normalize_stat_text(row.get("ip_address"), field_name="ip_address", required=False),
            "request_model": cls._normalize_stat_text(
                row.get("request_model"), field_name="request_model", required=True
            ),
            "response_model": cls._normalize_stat_text(
                row.get("response_model"), field_name="response_model", required=False
            ),
            "total_tokens": cls._normalize_optional_stat_int(row.get("total_tokens"), field_name="total_tokens"),
            "prompt_tokens": cls._normalize_optional_stat_int(row.get("prompt_tokens"), field_name="prompt_tokens"),
            "completion_tokens": cls._normalize_optional_stat_int(
                row.get("completion_tokens"), field_name="completion_tokens"
            ),
            "start_time": cls._normalize_stat_text(row.get("start_time"), field_name="start_time", required=True),
            "end_time": cls._normalize_stat_text(row.get("end_time"), field_name="end_time", required=False),
            "created_at": cls._normalize_stat_text(row.get("created_at"), field_name="created_at", required=False)
            or default_created_at,
        }

    @classmethod
    def _normalize_optional_stat_int(cls, value: Any, *, field_name: str) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        return cls._normalize_stat_int(value, field_name=field_name)

    @classmethod
    def _normalize_optional_positive_int(cls, value: Any, *, field_name: str) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        return cls._normalize_positive_int(value, field_name=field_name)

    @staticmethod
    def _normalize_positive_int(value: Any, *, field_name: str) -> int:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"request log {field_name} must be an integer") from exc
        if parsed <= 0:
            raise ValueError(f"request log {field_name} must be greater than 0")
        return parsed

    @staticmethod
    def _daily_stat_exists(cursor: sqlite3.Cursor, row: dict[str, Any]) -> bool:
        if row["ip_address"] is None:
            cursor.execute(
                """
                SELECT 1 FROM daily_request_stats
                WHERE stat_date = ? AND ip_address IS NULL AND request_model = ? AND response_model = ?
                LIMIT 1
                """,
                (row["stat_date"], row["request_model"], row["response_model"]),
            )
        else:
            cursor.execute(
                """
                SELECT 1 FROM daily_request_stats
                WHERE stat_date = ? AND ip_address = ? AND request_model = ? AND response_model = ?
                LIMIT 1
                """,
                (row["stat_date"], row["ip_address"], row["request_model"], row["response_model"]),
            )
        return cursor.fetchone() is not None

    @staticmethod
    def _request_log_exists(cursor: sqlite3.Cursor, row: dict[str, Any]) -> bool:
        cursor.execute(
            """
            SELECT 1 FROM request_logs
            WHERE ip_address IS ?
              AND request_model IS ?
              AND response_model IS ?
              AND total_tokens IS ?
              AND prompt_tokens IS ?
              AND completion_tokens IS ?
              AND start_time IS ?
              AND end_time IS ?
            LIMIT 1
            """,
            (
                row["ip_address"],
                row["request_model"],
                row["response_model"],
                row["total_tokens"],
                row["prompt_tokens"],
                row["completion_tokens"],
                row["start_time"],
                row["end_time"],
            ),
        )
        return cursor.fetchone() is not None

    @staticmethod
    def _update_daily_stat(cursor: sqlite3.Cursor, row: dict[str, Any], now_text: str) -> None:
        params: tuple[Any, ...]
        if row["ip_address"] is None:
            where_clause = "stat_date = ? AND ip_address IS NULL AND request_model = ? AND response_model = ?"
            params = (
                row["request_count"],
                row["total_tokens"],
                row["prompt_tokens"],
                row["completion_tokens"],
                now_text,
                row["stat_date"],
                row["request_model"],
                row["response_model"],
            )
        else:
            where_clause = "stat_date = ? AND ip_address = ? AND request_model = ? AND response_model = ?"
            params = (
                row["request_count"],
                row["total_tokens"],
                row["prompt_tokens"],
                row["completion_tokens"],
                now_text,
                row["stat_date"],
                row["ip_address"],
                row["request_model"],
                row["response_model"],
            )

        cursor.execute(
            f"""
            UPDATE daily_request_stats
            SET
                request_count = request_count + ?,
                total_tokens = total_tokens + ?,
                prompt_tokens = prompt_tokens + ?,
                completion_tokens = completion_tokens + ?,
                updated_at = ?
            WHERE {where_clause}
            """,
            params,
        )
