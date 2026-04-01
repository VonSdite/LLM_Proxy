#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auth group runtime state repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable

from ..utils.database import ConnectionFactory
from ..utils.local_time import format_local_date, format_local_datetime, now_local_datetime_text


class AuthGroupRepository:
    """Persist auth entry runtime state and quota buckets."""

    def __init__(self, get_connection: ConnectionFactory):
        self._get_connection = get_connection
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_entry_runtime_state (
                    auth_group_name TEXT NOT NULL,
                    entry_id TEXT NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    disabled_reason TEXT,
                    cooldown_until TEXT,
                    last_status_code INTEGER,
                    last_error_type TEXT,
                    last_error_message TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (auth_group_name, entry_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_entry_usage_buckets (
                    auth_group_name TEXT NOT NULL,
                    entry_id TEXT NOT NULL,
                    bucket_type TEXT NOT NULL,
                    bucket_start TEXT NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (auth_group_name, entry_id, bucket_type, bucket_start)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_entry_runtime_group ON auth_entry_runtime_state(auth_group_name)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_entry_usage_group ON auth_entry_usage_buckets(auth_group_name, bucket_type, bucket_start)"
            )

    @staticmethod
    def _default_runtime_state(auth_group_name: str, entry_id: str) -> Dict[str, Any]:
        return {
            "auth_group_name": auth_group_name,
            "entry_id": entry_id,
            "disabled": False,
            "disabled_reason": None,
            "cooldown_until": None,
            "last_status_code": None,
            "last_error_type": None,
            "last_error_message": None,
            "updated_at": None,
        }

    def get_entry_runtime_state(self, auth_group_name: str, entry_id: str) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT auth_group_name, entry_id, disabled, disabled_reason, cooldown_until,
                       last_status_code, last_error_type, last_error_message, updated_at
                FROM auth_entry_runtime_state
                WHERE auth_group_name = ? AND entry_id = ?
                """,
                (auth_group_name, entry_id),
            )
            row = cursor.fetchone()
        if row is None:
            return self._default_runtime_state(auth_group_name, entry_id)
        return {
            "auth_group_name": row["auth_group_name"],
            "entry_id": row["entry_id"],
            "disabled": bool(row["disabled"]),
            "disabled_reason": row["disabled_reason"],
            "cooldown_until": row["cooldown_until"],
            "last_status_code": row["last_status_code"],
            "last_error_type": row["last_error_type"],
            "last_error_message": row["last_error_message"],
            "updated_at": row["updated_at"],
        }

    def list_group_runtime_states(self, auth_group_name: str) -> Dict[str, Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT auth_group_name, entry_id, disabled, disabled_reason, cooldown_until,
                       last_status_code, last_error_type, last_error_message, updated_at
                FROM auth_entry_runtime_state
                WHERE auth_group_name = ?
                """,
                (auth_group_name,),
            )
            rows = cursor.fetchall()

        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            result[row["entry_id"]] = {
                "auth_group_name": row["auth_group_name"],
                "entry_id": row["entry_id"],
                "disabled": bool(row["disabled"]),
                "disabled_reason": row["disabled_reason"],
                "cooldown_until": row["cooldown_until"],
                "last_status_code": row["last_status_code"],
                "last_error_type": row["last_error_type"],
                "last_error_message": row["last_error_message"],
                "updated_at": row["updated_at"],
            }
        return result

    def save_entry_runtime_state(
        self,
        auth_group_name: str,
        entry_id: str,
        *,
        disabled: bool,
        disabled_reason: str | None,
        cooldown_until: str | None,
        last_status_code: int | None,
        last_error_type: str | None,
        last_error_message: str | None,
    ) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO auth_entry_runtime_state (
                    auth_group_name, entry_id, disabled, disabled_reason, cooldown_until,
                    last_status_code, last_error_type, last_error_message, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(auth_group_name, entry_id)
                DO UPDATE SET
                    disabled = excluded.disabled,
                    disabled_reason = excluded.disabled_reason,
                    cooldown_until = excluded.cooldown_until,
                    last_status_code = excluded.last_status_code,
                    last_error_type = excluded.last_error_type,
                    last_error_message = excluded.last_error_message,
                    updated_at = excluded.updated_at
                """,
                (
                    auth_group_name,
                    entry_id,
                    1 if disabled else 0,
                    disabled_reason,
                    cooldown_until,
                    last_status_code,
                    last_error_type,
                    last_error_message,
                    now_local_datetime_text(),
                ),
            )

    def restore_entry(self, auth_group_name: str, entry_id: str) -> None:
        self.save_entry_runtime_state(
            auth_group_name,
            entry_id,
            disabled=False,
            disabled_reason=None,
            cooldown_until=None,
            last_status_code=None,
            last_error_type=None,
            last_error_message=None,
        )

    def reset_current_minute_usage(self, auth_group_name: str, entry_id: str, when: datetime) -> None:
        minute_bucket = self._minute_bucket_start(when)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM auth_entry_usage_buckets
                WHERE auth_group_name = ? AND entry_id = ?
                  AND bucket_type = 'minute' AND bucket_start = ?
                """,
                (auth_group_name, entry_id, minute_bucket),
            )

    def reset_current_day_usage(self, auth_group_name: str, entry_id: str, when: datetime) -> None:
        day_bucket = self._day_bucket_start(when)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM auth_entry_usage_buckets
                WHERE auth_group_name = ? AND entry_id = ?
                  AND bucket_type = 'day' AND bucket_start = ?
                """,
                (auth_group_name, entry_id, day_bucket),
            )

    @staticmethod
    def _minute_bucket_start(value: datetime) -> str:
        return format_local_datetime(value.replace(second=0, microsecond=0))

    @staticmethod
    def _day_bucket_start(value: datetime) -> str:
        return format_local_date(value)

    def _increment_usage_bucket(
        self,
        auth_group_name: str,
        entry_id: str,
        *,
        bucket_type: str,
        bucket_start: str,
        request_count: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO auth_entry_usage_buckets (
                    auth_group_name, entry_id, bucket_type, bucket_start,
                    request_count, prompt_tokens, completion_tokens, total_tokens, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(auth_group_name, entry_id, bucket_type, bucket_start)
                DO UPDATE SET
                    request_count = request_count + excluded.request_count,
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens,
                    total_tokens = total_tokens + excluded.total_tokens,
                    updated_at = excluded.updated_at
                """,
                (
                    auth_group_name,
                    entry_id,
                    bucket_type,
                    bucket_start,
                    int(request_count or 0),
                    int(prompt_tokens or 0),
                    int(completion_tokens or 0),
                    int(total_tokens or 0),
                    now_local_datetime_text(),
                ),
            )

    def increment_request_usage(self, auth_group_name: str, entry_id: str, when: datetime) -> None:
        minute_bucket = self._minute_bucket_start(when)
        day_bucket = self._day_bucket_start(when)
        self._increment_usage_bucket(
            auth_group_name,
            entry_id,
            bucket_type="minute",
            bucket_start=minute_bucket,
            request_count=1,
        )
        self._increment_usage_bucket(
            auth_group_name,
            entry_id,
            bucket_type="day",
            bucket_start=day_bucket,
            request_count=1,
        )

    def increment_token_usage(
        self,
        auth_group_name: str,
        entry_id: str,
        when: datetime,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        minute_bucket = self._minute_bucket_start(when)
        day_bucket = self._day_bucket_start(when)
        for bucket_type, bucket_start in (("minute", minute_bucket), ("day", day_bucket)):
            self._increment_usage_bucket(
                auth_group_name,
                entry_id,
                bucket_type=bucket_type,
                bucket_start=bucket_start,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )

    def get_current_usage(self, auth_group_name: str, entry_id: str, when: datetime) -> Dict[str, int]:
        minute_bucket = self._minute_bucket_start(when)
        day_bucket = self._day_bucket_start(when)

        usage = {
            "minute_request_count": 0,
            "day_request_count": 0,
            "minute_prompt_tokens": 0,
            "day_prompt_tokens": 0,
            "minute_completion_tokens": 0,
            "day_completion_tokens": 0,
            "minute_total_tokens": 0,
            "day_total_tokens": 0,
        }
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT bucket_type, bucket_start, request_count, prompt_tokens, completion_tokens, total_tokens
                FROM auth_entry_usage_buckets
                WHERE auth_group_name = ? AND entry_id = ?
                  AND ((bucket_type = 'minute' AND bucket_start = ?)
                    OR (bucket_type = 'day' AND bucket_start = ?))
                """,
                (auth_group_name, entry_id, minute_bucket, day_bucket),
            )
            rows = cursor.fetchall()

        for row in rows:
            prefix = "minute" if row["bucket_type"] == "minute" else "day"
            usage[f"{prefix}_request_count"] = int(row["request_count"] or 0)
            usage[f"{prefix}_prompt_tokens"] = int(row["prompt_tokens"] or 0)
            usage[f"{prefix}_completion_tokens"] = int(row["completion_tokens"] or 0)
            usage[f"{prefix}_total_tokens"] = int(row["total_tokens"] or 0)
        return usage

    def list_current_usage(
        self,
        auth_group_name: str,
        entry_ids: Iterable[str],
        when: datetime,
    ) -> Dict[str, Dict[str, int]]:
        usage_by_entry: Dict[str, Dict[str, int]] = {}
        for entry_id in entry_ids:
            usage_by_entry[str(entry_id)] = self.get_current_usage(auth_group_name, str(entry_id), when)
        return usage_by_entry
