#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型映射定义与运行状态仓储。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..config.model_mapping_config import DEFAULT_MODEL_MAPPING_STRATEGY, ModelMappingSchema
from ..utils.database import ConnectionFactory
from ..utils.local_time import now_local_datetime_text


class ModelMappingRepository:
    """使用 SQLite 持久化模型映射及其运行状态。"""

    def __init__(self, get_connection: ConnectionFactory):
        self._get_connection = get_connection
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_mappings (
                    id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    strategy TEXT NOT NULL DEFAULT 'highest_priority',
                    cooldown_seconds_on_429 INTEGER NOT NULL DEFAULT 60,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_mapping_targets (
                    mapping_id TEXT NOT NULL,
                    target_model_id TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (mapping_id, target_model_id)
                );

                CREATE TABLE IF NOT EXISTS model_mapping_target_runtime (
                    mapping_id TEXT NOT NULL,
                    target_model_id TEXT NOT NULL,
                    auto_disabled INTEGER NOT NULL DEFAULT 0,
                    disabled_reason TEXT,
                    cooldown_until TEXT,
                    last_status_code INTEGER,
                    last_error_type TEXT,
                    last_error_message TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (mapping_id, target_model_id)
                );

                CREATE TABLE IF NOT EXISTS model_mapping_runtime (
                    mapping_id TEXT PRIMARY KEY,
                    current_target_model_id TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_model_mapping_targets_mapping
                    ON model_mapping_targets(mapping_id, sort_order);
                CREATE INDEX IF NOT EXISTS idx_model_mapping_target_runtime_mapping
                    ON model_mapping_target_runtime(mapping_id);
                """
            )
            mapping_columns = {row["name"] for row in conn.execute("PRAGMA table_info(model_mappings)").fetchall()}
            if "enabled" not in mapping_columns:
                conn.execute("ALTER TABLE model_mappings ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
            if "sort_order" not in mapping_columns:
                conn.execute("ALTER TABLE model_mappings ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
                existing_rows = conn.execute("SELECT id FROM model_mappings ORDER BY created_at, id").fetchall()
                conn.executemany(
                    "UPDATE model_mappings SET sort_order = ? WHERE id = ?",
                    [(index, row["id"]) for index, row in enumerate(existing_rows)],
                )
            if "strategy" not in mapping_columns:
                conn.execute(
                    "ALTER TABLE model_mappings ADD COLUMN strategy TEXT NOT NULL "
                    f"DEFAULT '{DEFAULT_MODEL_MAPPING_STRATEGY}'"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_mappings_order ON model_mappings(sort_order, created_at)"
            )

    def list_mappings(self) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            mappings = conn.execute(
                """
                SELECT id, enabled, sort_order, strategy, cooldown_seconds_on_429, created_at, updated_at
                FROM model_mappings
                ORDER BY sort_order, created_at, id
                """
            ).fetchall()
            targets = conn.execute(
                """
                SELECT mapping_id, target_model_id, priority, enabled, sort_order, created_at, updated_at
                FROM model_mapping_targets
                ORDER BY mapping_id, sort_order, target_model_id
                """
            ).fetchall()
        targets_by_mapping: dict[str, list[dict[str, Any]]] = {}
        for row in targets:
            targets_by_mapping.setdefault(row["mapping_id"], []).append(
                {
                    "model_id": row["target_model_id"],
                    "priority": int(row["priority"]),
                    "enabled": bool(row["enabled"]),
                    "sort_order": int(row["sort_order"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return [
            {
                "id": row["id"],
                "enabled": bool(row["enabled"]),
                "sort_order": int(row["sort_order"]),
                "strategy": str(row["strategy"] or DEFAULT_MODEL_MAPPING_STRATEGY),
                "cooldown_seconds_on_429": int(row["cooldown_seconds_on_429"]),
                "targets": targets_by_mapping.get(row["id"], []),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in mappings
        ]

    def get_mapping(self, mapping_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_mappings() if item["id"] == mapping_id), None)

    def create_mapping(self, mapping: ModelMappingSchema) -> None:
        now_text = now_local_datetime_text()
        with self._get_connection() as conn:
            if conn.execute("SELECT 1 FROM model_mappings WHERE id = ?", (mapping.id,)).fetchone():
                raise ValueError(f"模型映射已存在: {mapping.id}")
            self._insert_mapping(conn, mapping, now_text, sort_order=self._next_mapping_sort_order(conn))

    def create_mapping_after(self, source_id: str, mapping: ModelMappingSchema) -> None:
        """创建模型映射，并插入到源映射下方。"""
        now_text = now_local_datetime_text()
        with self._get_connection() as conn:
            source = conn.execute("SELECT sort_order FROM model_mappings WHERE id = ?", (source_id,)).fetchone()
            if source is None:
                raise ValueError(f"模型映射不存在: {source_id}")
            if conn.execute("SELECT 1 FROM model_mappings WHERE id = ?", (mapping.id,)).fetchone():
                raise ValueError(f"模型映射已存在: {mapping.id}")
            source_order = int(source["sort_order"])
            conn.execute(
                "UPDATE model_mappings SET sort_order = sort_order + 1, updated_at = ? WHERE sort_order > ?",
                (now_text, source_order),
            )
            self._insert_mapping(conn, mapping, now_text, sort_order=source_order + 1)

    def update_mapping(self, current_id: str, mapping: ModelMappingSchema) -> None:
        now_text = now_local_datetime_text()
        with self._get_connection() as conn:
            current = conn.execute(
                "SELECT created_at, sort_order FROM model_mappings WHERE id = ?", (current_id,)
            ).fetchone()
            if current is None:
                raise ValueError(f"模型映射不存在: {current_id}")
            if (
                mapping.id != current_id
                and conn.execute("SELECT 1 FROM model_mappings WHERE id = ?", (mapping.id,)).fetchone()
            ):
                raise ValueError(f"模型映射已存在: {mapping.id}")
            self._delete_mapping_rows(conn, current_id)
            self._insert_mapping(
                conn,
                mapping,
                now_text,
                created_at=current["created_at"],
                sort_order=int(current["sort_order"]),
            )

    def delete_mapping(self, mapping_id: str) -> None:
        with self._get_connection() as conn:
            if not conn.execute("SELECT 1 FROM model_mappings WHERE id = ?", (mapping_id,)).fetchone():
                raise ValueError(f"模型映射不存在: {mapping_id}")
            self._delete_mapping_rows(conn, mapping_id)

    def set_mapping_enabled(self, mapping_id: str, *, enabled: bool) -> None:
        """更新一个映射的启用状态。"""
        now_text = now_local_datetime_text()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE model_mappings SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, now_text, mapping_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"模型映射不存在: {mapping_id}")
            if not enabled:
                conn.execute(
                    """
                    UPDATE model_mapping_runtime
                    SET current_target_model_id = NULL, updated_at = ?
                    WHERE mapping_id = ?
                    """,
                    (now_text, mapping_id),
                )

    def reorder_mappings(self, mapping_ids: Sequence[str]) -> None:
        """按给定完整 ID 列表更新映射顺序。"""
        with self._get_connection() as conn:
            current_ids = [row["id"] for row in conn.execute("SELECT id FROM model_mappings").fetchall()]
            if len(mapping_ids) != len(current_ids) or set(mapping_ids) != set(current_ids):
                raise ValueError("模型映射顺序必须完整包含每个映射 ID")
            now_text = now_local_datetime_text()
            conn.executemany(
                "UPDATE model_mappings SET sort_order = ?, updated_at = ? WHERE id = ?",
                [(index, now_text, mapping_id) for index, mapping_id in enumerate(mapping_ids)],
            )

    def set_target_enabled(self, mapping_id: str, target_model_id: str, *, enabled: bool) -> None:
        """只更新一个目标的配置启用状态。"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE model_mapping_targets
                SET enabled = ?, updated_at = ?
                WHERE mapping_id = ? AND target_model_id = ?
                """,
                (1 if enabled else 0, now_local_datetime_text(), mapping_id, target_model_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"模型映射目标不存在: {target_model_id}")
            conn.execute(
                """
                UPDATE model_mapping_runtime
                SET current_target_model_id = NULL, updated_at = ?
                WHERE mapping_id = ? AND current_target_model_id = ?
                """,
                (now_local_datetime_text(), mapping_id, target_model_id),
            )

    def import_mappings(self, mappings: Sequence[ModelMappingSchema]) -> None:
        now_text = now_local_datetime_text()
        with self._get_connection() as conn:
            existing_ids = {
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM model_mappings WHERE id IN ({','.join('?' for _ in mappings)})",
                    tuple(mapping.id for mapping in mappings),
                ).fetchall()
            }
            if existing_ids:
                raise ValueError(f"导入包含已存在的模型映射: {', '.join(sorted(existing_ids))}")
            next_sort_order = self._next_mapping_sort_order(conn)
            for offset, mapping in enumerate(mappings):
                self._insert_mapping(conn, mapping, now_text, sort_order=next_sort_order + offset)

    def list_target_runtime_states(self, mapping_id: str) -> dict[str, dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT target_model_id, auto_disabled, disabled_reason, cooldown_until,
                       last_status_code, last_error_type, last_error_message, updated_at
                FROM model_mapping_target_runtime
                WHERE mapping_id = ?
                """,
                (mapping_id,),
            ).fetchall()
        return {
            row["target_model_id"]: {
                "auto_disabled": bool(row["auto_disabled"]),
                "disabled_reason": row["disabled_reason"],
                "cooldown_until": row["cooldown_until"],
                "last_status_code": row["last_status_code"],
                "last_error_type": row["last_error_type"],
                "last_error_message": row["last_error_message"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def save_target_runtime_state(
        self,
        mapping_id: str,
        target_model_id: str,
        *,
        auto_disabled: bool,
        disabled_reason: str | None,
        cooldown_until: str | None,
        last_status_code: int | None,
        last_error_type: str | None,
        last_error_message: str | None,
    ) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO model_mapping_target_runtime (
                    mapping_id, target_model_id, auto_disabled, disabled_reason, cooldown_until,
                    last_status_code, last_error_type, last_error_message, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mapping_id, target_model_id) DO UPDATE SET
                    auto_disabled = excluded.auto_disabled,
                    disabled_reason = excluded.disabled_reason,
                    cooldown_until = excluded.cooldown_until,
                    last_status_code = excluded.last_status_code,
                    last_error_type = excluded.last_error_type,
                    last_error_message = excluded.last_error_message,
                    updated_at = excluded.updated_at
                """,
                (
                    mapping_id,
                    target_model_id,
                    1 if auto_disabled else 0,
                    disabled_reason,
                    cooldown_until,
                    last_status_code,
                    last_error_type,
                    last_error_message,
                    now_local_datetime_text(),
                ),
            )

    def restore_target(self, mapping_id: str, target_model_id: str) -> None:
        self.save_target_runtime_state(
            mapping_id,
            target_model_id,
            auto_disabled=False,
            disabled_reason=None,
            cooldown_until=None,
            last_status_code=None,
            last_error_type=None,
            last_error_message=None,
        )

    def get_current_target(self, mapping_id: str) -> str | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT current_target_model_id FROM model_mapping_runtime WHERE mapping_id = ?",
                (mapping_id,),
            ).fetchone()
        return str(row["current_target_model_id"]) if row and row["current_target_model_id"] else None

    def set_current_target(self, mapping_id: str, target_model_id: str | None) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO model_mapping_runtime (mapping_id, current_target_model_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(mapping_id) DO UPDATE SET
                    current_target_model_id = excluded.current_target_model_id,
                    updated_at = excluded.updated_at
                """,
                (mapping_id, target_model_id, now_local_datetime_text()),
            )

    @staticmethod
    def _insert_mapping(
        conn: Any,
        mapping: ModelMappingSchema,
        now_text: str,
        *,
        created_at: str | None = None,
        sort_order: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO model_mappings (
                id, enabled, sort_order, strategy, cooldown_seconds_on_429, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mapping.id,
                1 if mapping.enabled else 0,
                sort_order,
                mapping.strategy,
                mapping.cooldown_seconds_on_429,
                created_at or now_text,
                now_text,
            ),
        )
        conn.executemany(
            """
            INSERT INTO model_mapping_targets (
                mapping_id, target_model_id, priority, enabled, sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    mapping.id,
                    target.model_id,
                    target.priority,
                    1 if target.enabled else 0,
                    target.sort_order,
                    now_text,
                    now_text,
                )
                for target in mapping.targets
            ],
        )

    @staticmethod
    def _next_mapping_sort_order(conn: Any) -> int:
        row = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM model_mappings").fetchone()
        return int(row["next_order"])

    @staticmethod
    def _delete_mapping_rows(conn: Any, mapping_id: str) -> None:
        conn.execute("DELETE FROM model_mapping_runtime WHERE mapping_id = ?", (mapping_id,))
        conn.execute("DELETE FROM model_mapping_target_runtime WHERE mapping_id = ?", (mapping_id,))
        conn.execute("DELETE FROM model_mapping_targets WHERE mapping_id = ?", (mapping_id,))
        conn.execute("DELETE FROM model_mappings WHERE id = ?", (mapping_id,))
