#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auth group runtime state repository."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

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
    def _default_runtime_state(auth_group_name: str, entry_id: str) -> dict[str, Any]:
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

    @staticmethod
    def _empty_usage() -> dict[str, int]:
        return {
            "minute_request_count": 0,
            "day_request_count": 0,
            "minute_prompt_tokens": 0,
            "day_prompt_tokens": 0,
            "minute_completion_tokens": 0,
            "day_completion_tokens": 0,
            "minute_total_tokens": 0,
            "day_total_tokens": 0,
        }

    def get_entry_runtime_state(self, auth_group_name: str, entry_id: str) -> dict[str, Any]:
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

    def list_group_runtime_states(self, auth_group_name: str) -> dict[str, dict[str, Any]]:
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

        result: dict[str, dict[str, Any]] = {}
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

    def export_runtime_states(self, auth_group_names: Iterable[str]) -> list[dict[str, Any]]:
        """按 Auth Group 名称导出运行态表项。"""
        normalized_names = self._normalize_auth_group_names(auth_group_names)
        if not normalized_names:
            return []

        placeholders = ", ".join("?" for _ in normalized_names)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT auth_group_name, entry_id, disabled, disabled_reason, cooldown_until,
                       last_status_code, last_error_type, last_error_message, updated_at
                FROM auth_entry_runtime_state
                WHERE auth_group_name IN ({placeholders})
                ORDER BY auth_group_name ASC, entry_id ASC
                """,
                normalized_names,
            )
            rows = cursor.fetchall()

        return [
            {
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
            for row in rows
        ]

    def export_usage_buckets(self, auth_group_names: Iterable[str]) -> list[dict[str, Any]]:
        """按 Auth Group 名称导出用量桶表项。"""
        normalized_names = self._normalize_auth_group_names(auth_group_names)
        if not normalized_names:
            return []

        placeholders = ", ".join("?" for _ in normalized_names)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT auth_group_name, entry_id, bucket_type, bucket_start,
                       request_count, prompt_tokens, completion_tokens, total_tokens, updated_at
                FROM auth_entry_usage_buckets
                WHERE auth_group_name IN ({placeholders})
                ORDER BY auth_group_name ASC, entry_id ASC, bucket_type ASC, bucket_start ASC
                """,
                normalized_names,
            )
            rows = cursor.fetchall()

        return [
            {
                "auth_group_name": row["auth_group_name"],
                "entry_id": row["entry_id"],
                "bucket_type": row["bucket_type"],
                "bucket_start": row["bucket_start"],
                "request_count": int(row["request_count"] or 0),
                "prompt_tokens": int(row["prompt_tokens"] or 0),
                "completion_tokens": int(row["completion_tokens"] or 0),
                "total_tokens": int(row["total_tokens"] or 0),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def purge_legacy_provider_runtime_state(
        self,
        active_auth_group_names: Iterable[str] = (),
    ) -> tuple[int, int]:
        normalized_active_names = {
            str(name or "").strip() for name in active_auth_group_names if str(name or "").strip()
        }

        with self._get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                """
                SELECT DISTINCT auth_group_name
                FROM auth_entry_runtime_state
                WHERE auth_group_name GLOB '__legacy_provider__/*'
                UNION
                SELECT DISTINCT auth_group_name
                FROM auth_entry_usage_buckets
                WHERE auth_group_name GLOB '__legacy_provider__/*'
                ORDER BY auth_group_name
                """
            ).fetchall()

            target_group_names = [str(row[0]) for row in rows if str(row[0]) not in normalized_active_names]
            if not target_group_names:
                return 0, 0

            placeholders = ", ".join("?" for _ in target_group_names)
            runtime_row_count = int(
                cursor.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM auth_entry_runtime_state
                    WHERE auth_group_name IN ({placeholders})
                    """,
                    target_group_names,
                ).fetchone()[0]
            )
            usage_row_count = int(
                cursor.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM auth_entry_usage_buckets
                    WHERE auth_group_name IN ({placeholders})
                    """,
                    target_group_names,
                ).fetchone()[0]
            )
            cursor.execute(
                f"""
                DELETE FROM auth_entry_runtime_state
                WHERE auth_group_name IN ({placeholders})
                """,
                target_group_names,
            )
            cursor.execute(
                f"""
                DELETE FROM auth_entry_usage_buckets
                WHERE auth_group_name IN ({placeholders})
                """,
                target_group_names,
            )
        return runtime_row_count, usage_row_count

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

    def import_runtime_states(self, rows: Iterable[dict[str, Any]]) -> dict[str, int]:
        """导入运行态表项，主键相同的表项使用导入值覆盖。"""
        now_text = now_local_datetime_text()
        normalized_rows = [self._normalize_runtime_state_row(row, default_updated_at=now_text) for row in rows]
        inserted_count = 0
        updated_count = 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for row in normalized_rows:
                if self._runtime_state_exists(cursor, row):
                    updated_count += 1
                else:
                    inserted_count += 1
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
                        row["auth_group_name"],
                        row["entry_id"],
                        1 if row["disabled"] else 0,
                        row["disabled_reason"],
                        row["cooldown_until"],
                        row["last_status_code"],
                        row["last_error_type"],
                        row["last_error_message"],
                        row["updated_at"],
                    ),
                )

        return {
            "count": len(normalized_rows),
            "inserted_count": inserted_count,
            "updated_count": updated_count,
        }

    def import_usage_buckets(self, rows: Iterable[dict[str, Any]]) -> dict[str, int]:
        """导入用量桶表项，主键相同的表项使用导入值覆盖。"""
        now_text = now_local_datetime_text()
        normalized_rows = [self._normalize_usage_bucket_row(row, default_updated_at=now_text) for row in rows]
        inserted_count = 0
        updated_count = 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for row in normalized_rows:
                if self._usage_bucket_exists(cursor, row):
                    updated_count += 1
                else:
                    inserted_count += 1
                cursor.execute(
                    """
                    INSERT INTO auth_entry_usage_buckets (
                        auth_group_name, entry_id, bucket_type, bucket_start,
                        request_count, prompt_tokens, completion_tokens, total_tokens, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(auth_group_name, entry_id, bucket_type, bucket_start)
                    DO UPDATE SET
                        request_count = excluded.request_count,
                        prompt_tokens = excluded.prompt_tokens,
                        completion_tokens = excluded.completion_tokens,
                        total_tokens = excluded.total_tokens,
                        updated_at = excluded.updated_at
                    """,
                    (
                        row["auth_group_name"],
                        row["entry_id"],
                        row["bucket_type"],
                        row["bucket_start"],
                        row["request_count"],
                        row["prompt_tokens"],
                        row["completion_tokens"],
                        row["total_tokens"],
                        row["updated_at"],
                    ),
                )

        return {
            "count": len(normalized_rows),
            "inserted_count": inserted_count,
            "updated_count": updated_count,
        }

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

    def get_current_usage(self, auth_group_name: str, entry_id: str, when: datetime) -> dict[str, int]:
        minute_bucket = self._minute_bucket_start(when)
        day_bucket = self._day_bucket_start(when)

        usage = self._empty_usage()
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
    ) -> dict[str, dict[str, int]]:
        normalized_entry_ids = [str(entry_id).strip() for entry_id in entry_ids if str(entry_id).strip()]
        if not normalized_entry_ids:
            return {}

        minute_bucket = self._minute_bucket_start(when)
        day_bucket = self._day_bucket_start(when)
        usage_by_entry: dict[str, dict[str, int]] = {entry_id: self._empty_usage() for entry_id in normalized_entry_ids}

        placeholders = ", ".join("?" for _ in normalized_entry_ids)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT entry_id, bucket_type, request_count, prompt_tokens, completion_tokens, total_tokens
                FROM auth_entry_usage_buckets
                WHERE auth_group_name = ?
                  AND entry_id IN ({placeholders})
                  AND ((bucket_type = 'minute' AND bucket_start = ?)
                    OR (bucket_type = 'day' AND bucket_start = ?))
                """,
                (
                    auth_group_name,
                    *normalized_entry_ids,
                    minute_bucket,
                    day_bucket,
                ),
            )
            rows = cursor.fetchall()

        for row in rows:
            entry_usage = usage_by_entry.get(str(row["entry_id"]))
            if entry_usage is None:
                continue
            prefix = "minute" if row["bucket_type"] == "minute" else "day"
            entry_usage[f"{prefix}_request_count"] = int(row["request_count"] or 0)
            entry_usage[f"{prefix}_prompt_tokens"] = int(row["prompt_tokens"] or 0)
            entry_usage[f"{prefix}_completion_tokens"] = int(row["completion_tokens"] or 0)
            entry_usage[f"{prefix}_total_tokens"] = int(row["total_tokens"] or 0)
        return usage_by_entry

    @staticmethod
    def _normalize_auth_group_names(auth_group_names: Iterable[str]) -> list[str]:
        seen_names: set[str] = set()
        normalized_names: list[str] = []
        for raw_name in auth_group_names:
            name = str(raw_name or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            normalized_names.append(name)
        return normalized_names

    @staticmethod
    def _normalize_required_text(value: Any, *, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"Auth group runtime field {field_name} is required")
        return text

    @staticmethod
    def _normalize_optional_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _normalize_optional_int(cls, value: Any, *, field_name: str) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        return cls._normalize_non_negative_int(value, field_name=field_name)

    @staticmethod
    def _normalize_non_negative_int(value: Any, *, field_name: str) -> int:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Auth group runtime field {field_name} must be an integer") from exc
        if parsed < 0:
            raise ValueError(f"Auth group runtime field {field_name} must be greater than or equal to 0")
        return parsed

    @staticmethod
    def _normalize_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value or "").strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"", "0", "false", "no", "off", "disabled"}:
            return False
        return bool(value)

    @classmethod
    def _normalize_runtime_state_row(cls, row: dict[str, Any], *, default_updated_at: str) -> dict[str, Any]:
        if not isinstance(row, dict):
            raise ValueError("Each auth_entry_runtime_state row must be an object")
        return {
            "auth_group_name": cls._normalize_required_text(row.get("auth_group_name"), field_name="auth_group_name"),
            "entry_id": cls._normalize_required_text(row.get("entry_id"), field_name="entry_id"),
            "disabled": cls._normalize_bool(row.get("disabled")),
            "disabled_reason": cls._normalize_optional_text(row.get("disabled_reason")),
            "cooldown_until": cls._normalize_optional_text(row.get("cooldown_until")),
            "last_status_code": cls._normalize_optional_int(row.get("last_status_code"), field_name="last_status_code"),
            "last_error_type": cls._normalize_optional_text(row.get("last_error_type")),
            "last_error_message": cls._normalize_optional_text(row.get("last_error_message")),
            "updated_at": cls._normalize_optional_text(row.get("updated_at")) or default_updated_at,
        }

    @classmethod
    def _normalize_usage_bucket_row(cls, row: dict[str, Any], *, default_updated_at: str) -> dict[str, Any]:
        if not isinstance(row, dict):
            raise ValueError("Each auth_entry_usage_buckets row must be an object")
        return {
            "auth_group_name": cls._normalize_required_text(row.get("auth_group_name"), field_name="auth_group_name"),
            "entry_id": cls._normalize_required_text(row.get("entry_id"), field_name="entry_id"),
            "bucket_type": cls._normalize_required_text(row.get("bucket_type"), field_name="bucket_type"),
            "bucket_start": cls._normalize_required_text(row.get("bucket_start"), field_name="bucket_start"),
            "request_count": cls._normalize_non_negative_int(row.get("request_count"), field_name="request_count"),
            "prompt_tokens": cls._normalize_non_negative_int(row.get("prompt_tokens"), field_name="prompt_tokens"),
            "completion_tokens": cls._normalize_non_negative_int(
                row.get("completion_tokens"),
                field_name="completion_tokens",
            ),
            "total_tokens": cls._normalize_non_negative_int(row.get("total_tokens"), field_name="total_tokens"),
            "updated_at": cls._normalize_optional_text(row.get("updated_at")) or default_updated_at,
        }

    @staticmethod
    def _runtime_state_exists(cursor: Any, row: dict[str, Any]) -> bool:
        cursor.execute(
            """
            SELECT 1
            FROM auth_entry_runtime_state
            WHERE auth_group_name = ? AND entry_id = ?
            LIMIT 1
            """,
            (row["auth_group_name"], row["entry_id"]),
        )
        return cursor.fetchone() is not None

    @staticmethod
    def _usage_bucket_exists(cursor: Any, row: dict[str, Any]) -> bool:
        cursor.execute(
            """
            SELECT 1
            FROM auth_entry_usage_buckets
            WHERE auth_group_name = ? AND entry_id = ? AND bucket_type = ? AND bucket_start = ?
            LIMIT 1
            """,
            (
                row["auth_group_name"],
                row["entry_id"],
                row["bucket_type"],
                row["bucket_start"],
            ),
        )
        return cursor.fetchone() is not None
