#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auth group runtime manager."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Mapping, Optional, Sequence

from ..application.app_context import AppContext, Logger
from ..hooks import HookErrorType
from ..repositories import AuthGroupRepository
from ..utils.local_time import format_local_datetime, now_local_datetime, parse_local_datetime
from .provider_config import (
    AuthGroupSchema,
    AuthEntrySchema,
)


@dataclass(frozen=True)
class SelectedAuthEntry:
    """Resolved auth entry selection for a single upstream attempt."""

    auth_group_name: str
    entry_id: str
    headers: tuple[tuple[str, str], ...]

    def headers_mapping(self) -> Dict[str, str]:
        return dict(self.headers)


class AuthGroupSelectionError(Exception):
    """Raised when no usable auth entry can be selected."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 503,
        error_type: str = "auth_group_unavailable",
        error_code: str = "auth_group_unavailable",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.error_code = error_code


class AuthGroupManager:
    """Select auth entries and persist their runtime state."""

    def __init__(self, ctx: AppContext, repository: AuthGroupRepository):
        self._logger: Logger = ctx.logger
        self._repository = repository
        self._groups_by_name: Dict[str, AuthGroupSchema] = {}
        self._inflight_counts: Dict[tuple[str, str], int] = {}
        self._rotation_cursor_by_group: Dict[str, int] = {}

    def load_auth_groups(self, auth_groups: Sequence[AuthGroupSchema]) -> None:
        self._groups_by_name = {group.name: group for group in auth_groups}
        self._inflight_counts.clear()
        self._rotation_cursor_by_group.clear()
        # TODO: Remove this migration cleanup in a future release after all
        # deployments have aged out the hidden __legacy_provider__ auth groups.
        runtime_rows, usage_rows = self._repository.purge_legacy_provider_runtime_state(
            self._groups_by_name.keys()
        )
        if runtime_rows or usage_rows:
            self._logger.info(
                "Purged legacy provider runtime state rows: runtime=%s usage=%s",
                runtime_rows,
                usage_rows,
            )
        self._logger.info("Loaded auth groups: %s", list(sorted(self._groups_by_name.keys())))

    def acquire(self, auth_group_name: Optional[str]) -> Optional[SelectedAuthEntry]:
        normalized_group_name = str(auth_group_name or "").strip()
        if not normalized_group_name:
            return None

        group = self._groups_by_name.get(normalized_group_name)
        if group is None:
            raise AuthGroupSelectionError(
                f"Unknown auth_group: {normalized_group_name}",
                error_code="unknown_auth_group",
            )

        now = now_local_datetime()
        runtime_states = self._repository.list_group_runtime_states(group.name)
        usage_by_entry = self._repository.list_current_usage(group.name, (entry.id for entry in group.entries), now)

        candidates: list[tuple[int, int, int, AuthEntrySchema]] = []
        for index, entry in enumerate(group.entries):
            runtime_state = runtime_states.get(entry.id) or self._repository.get_entry_runtime_state(group.name, entry.id)
            usage = usage_by_entry.get(entry.id) or {}
            if not self._is_entry_available(group, entry, runtime_state, usage, now):
                continue
            inflight = self._inflight_counts.get((group.name, entry.id), 0)
            candidates.append((inflight, self._rotation_distance(group.name, index, len(group.entries)), index, entry))

        if not candidates:
            raise AuthGroupSelectionError(
                f"No available auth entries for auth_group: {group.name}",
                error_code="auth_entry_unavailable",
            )

        _, _, selected_index, selected_entry = min(candidates, key=lambda item: (item[0], item[1], item[2]))
        key = (group.name, selected_entry.id)
        self._inflight_counts[key] = self._inflight_counts.get(key, 0) + 1
        self._rotation_cursor_by_group[group.name] = (selected_index + 1) % max(len(group.entries), 1)

        return SelectedAuthEntry(
            auth_group_name=group.name,
            entry_id=selected_entry.id,
            headers=selected_entry.headers,
        )

    def mark_request_dispatched(self, selection: Optional[SelectedAuthEntry]) -> None:
        if selection is None:
            return
        self._repository.increment_request_usage(
            selection.auth_group_name,
            selection.entry_id,
            now_local_datetime(),
        )

    def finish(
        self,
        selection: Optional[SelectedAuthEntry],
        *,
        status_code: Optional[int] = None,
        error_type: Optional[HookErrorType] = None,
        error_message: Optional[str] = None,
        response_headers: Optional[Mapping[str, Any]] = None,
        usage: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if selection is None:
            return

        key = (selection.auth_group_name, selection.entry_id)
        current_inflight = self._inflight_counts.get(key, 0)
        if current_inflight <= 1:
            self._inflight_counts.pop(key, None)
        else:
            self._inflight_counts[key] = current_inflight - 1

        group = self._groups_by_name.get(selection.auth_group_name)
        if group is None:
            return
        entry = self._find_entry(group, selection.entry_id)
        if entry is None:
            return

        runtime_state = self._repository.get_entry_runtime_state(group.name, entry.id)
        disabled = bool(runtime_state.get("disabled"))
        disabled_reason = runtime_state.get("disabled_reason")
        cooldown_until = runtime_state.get("cooldown_until")
        last_status_code: Optional[int] = runtime_state.get("last_status_code")
        last_error_type: Optional[str] = runtime_state.get("last_error_type")
        last_error_message: Optional[str] = runtime_state.get("last_error_message")

        if status_code is not None and 200 <= int(status_code) < 400:
            disabled = False
            disabled_reason = None
            cooldown_until = None
            last_status_code = None
            last_error_type = None
            last_error_message = None
            if usage:
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                total_tokens = int(usage.get("total_tokens") or 0)
                if total_tokens > 0 or prompt_tokens > 0 or completion_tokens > 0:
                    self._repository.increment_token_usage(
                        group.name,
                        entry.id,
                        now_local_datetime(),
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                    )
        elif status_code == 429:
            disabled = False
            disabled_reason = None
            cooldown_until = format_local_datetime(
                now_local_datetime()
                + timedelta(seconds=self._resolve_cooldown_seconds(group, entry, response_headers))
            )
            last_status_code = int(status_code)
            last_error_type = None
            last_error_message = error_message
        elif status_code in {401, 403}:
            disabled = True
            disabled_reason = f"http_{status_code}"
            cooldown_until = None
            last_status_code = int(status_code)
            last_error_type = None
            last_error_message = error_message
        elif status_code is not None and int(status_code) >= 400:
            disabled = bool(runtime_state.get("disabled"))
            disabled_reason = runtime_state.get("disabled_reason")
            last_status_code = int(status_code)
            last_error_type = None
            last_error_message = error_message
        elif error_type is not None:
            last_status_code = None
            last_error_type = error_type.value
            last_error_message = error_message

        self._repository.save_entry_runtime_state(
            group.name,
            entry.id,
            disabled=disabled,
            disabled_reason=disabled_reason,
            cooldown_until=cooldown_until,
            last_status_code=last_status_code,
            last_error_type=last_error_type,
            last_error_message=last_error_message,
        )

    def list_auth_group_summaries(self) -> list[Dict[str, Any]]:
        return [self._build_group_summary(group) for group in self.list_explicit_auth_groups()]

    def get_auth_group_runtime(self, auth_group_name: str) -> Dict[str, Any]:
        group = self._groups_by_name.get(str(auth_group_name).strip())
        if group is None:
            raise ValueError(f"Auth group not found: {auth_group_name}")

        now = now_local_datetime()
        runtime_states = self._repository.list_group_runtime_states(group.name)
        usage_by_entry = self._repository.list_current_usage(group.name, (entry.id for entry in group.entries), now)
        entries = [
            self._build_entry_runtime_view(
                group,
                entry,
                runtime_states.get(entry.id) or self._repository.get_entry_runtime_state(group.name, entry.id),
                usage_by_entry.get(entry.id) or {},
                now,
            )
            for entry in group.entries
        ]
        return {
            "name": group.name,
            "strategy": group.strategy,
            "cooldown_seconds_on_429": group.cooldown_seconds_on_429,
            "summary": self._summarize_entries(entries),
            "entries": entries,
        }

    def restore_entry(self, auth_group_name: str, entry_id: str) -> None:
        group = self._groups_by_name.get(str(auth_group_name).strip())
        if group is None:
            raise ValueError(f"Auth group not found: {auth_group_name}")
        if self._find_entry(group, entry_id) is None:
            raise ValueError(f"Auth entry not found: {entry_id}")
        self._repository.restore_entry(group.name, entry_id)

    def clear_entry_cooldown(self, auth_group_name: str, entry_id: str) -> None:
        group, entry = self._resolve_explicit_entry(auth_group_name, entry_id)
        runtime_state = self._repository.get_entry_runtime_state(group.name, entry.id)
        self._repository.save_entry_runtime_state(
            group.name,
            entry.id,
            disabled=bool(runtime_state.get("disabled")),
            disabled_reason=runtime_state.get("disabled_reason"),
            cooldown_until=None,
            last_status_code=runtime_state.get("last_status_code"),
            last_error_type=runtime_state.get("last_error_type"),
            last_error_message=runtime_state.get("last_error_message"),
        )

    def set_entry_disabled(self, auth_group_name: str, entry_id: str, *, disabled: bool) -> None:
        group, entry = self._resolve_explicit_entry(auth_group_name, entry_id)
        runtime_state = self._repository.get_entry_runtime_state(group.name, entry.id)
        self._repository.save_entry_runtime_state(
            group.name,
            entry.id,
            disabled=bool(disabled),
            disabled_reason="manual_disabled" if disabled else None,
            cooldown_until=runtime_state.get("cooldown_until"),
            last_status_code=runtime_state.get("last_status_code"),
            last_error_type=runtime_state.get("last_error_type"),
            last_error_message=runtime_state.get("last_error_message"),
        )

    def reset_entry_minute_usage(self, auth_group_name: str, entry_id: str) -> None:
        group, entry = self._resolve_explicit_entry(auth_group_name, entry_id)
        self._repository.reset_current_minute_usage(group.name, entry.id, now_local_datetime())

    def reset_entry_runtime(self, auth_group_name: str, entry_id: str) -> None:
        group, entry = self._resolve_explicit_entry(auth_group_name, entry_id)
        now = now_local_datetime()
        self._repository.restore_entry(group.name, entry.id)
        self._repository.reset_current_minute_usage(group.name, entry.id, now)
        self._repository.reset_current_day_usage(group.name, entry.id, now)

    def list_explicit_auth_groups(self) -> tuple[AuthGroupSchema, ...]:
        return tuple(
            self._groups_by_name[name]
            for name in sorted(self._groups_by_name)
        )

    def _build_group_summary(self, group: AuthGroupSchema) -> Dict[str, Any]:
        runtime = self.get_auth_group_runtime(group.name)
        return {
            "name": group.name,
            "strategy": group.strategy,
            "cooldown_seconds_on_429": group.cooldown_seconds_on_429,
            "entries": [entry.to_mapping() for entry in group.entries],
            "summary": runtime["summary"],
        }

    def _build_entry_runtime_view(
        self,
        group: AuthGroupSchema,
        entry: AuthEntrySchema,
        runtime_state: Mapping[str, Any],
        usage: Mapping[str, Any],
        now: datetime,
    ) -> Dict[str, Any]:
        inflight = self._inflight_counts.get((group.name, entry.id), 0)
        cooldown_until_text = runtime_state.get("cooldown_until")
        cooldown_until = parse_local_datetime(cooldown_until_text)
        is_cooling_down = cooldown_until is not None and cooldown_until > now
        is_disabled = (not entry.enabled) or bool(runtime_state.get("disabled"))
        is_request_quota_exceeded = self._is_request_quota_exceeded(entry, usage)
        is_token_quota_exceeded = self._is_token_quota_exceeded(entry, usage)
        is_saturated = entry.max_concurrency is not None and inflight >= entry.max_concurrency

        if is_disabled:
            status = "disabled"
        elif is_cooling_down:
            status = "cooldown"
        elif is_request_quota_exceeded or is_token_quota_exceeded:
            status = "quota_exceeded"
        elif is_saturated:
            status = "saturated"
        else:
            status = "available"

        return {
            "id": entry.id,
            "enabled": bool(entry.enabled),
            "headers_count": len(entry.headers),
            "max_concurrency": entry.max_concurrency,
            "cooldown_seconds_on_429": entry.cooldown_seconds_on_429,
            "request_quota_per_minute": entry.request_quota_per_minute,
            "request_quota_per_day": entry.request_quota_per_day,
            "token_quota_per_minute": entry.token_quota_per_minute,
            "token_quota_per_day": entry.token_quota_per_day,
            "inflight": inflight,
            "cooldown_until": cooldown_until_text,
            "disabled": is_disabled,
            "disabled_reason": runtime_state.get("disabled_reason"),
            "last_status_code": runtime_state.get("last_status_code"),
            "last_error_type": runtime_state.get("last_error_type"),
            "last_error_message": runtime_state.get("last_error_message"),
            "updated_at": runtime_state.get("updated_at"),
            "minute_request_count": int(usage.get("minute_request_count") or 0),
            "day_request_count": int(usage.get("day_request_count") or 0),
            "minute_prompt_tokens": int(usage.get("minute_prompt_tokens") or 0),
            "day_prompt_tokens": int(usage.get("day_prompt_tokens") or 0),
            "minute_completion_tokens": int(usage.get("minute_completion_tokens") or 0),
            "day_completion_tokens": int(usage.get("day_completion_tokens") or 0),
            "minute_total_tokens": int(usage.get("minute_total_tokens") or 0),
            "day_total_tokens": int(usage.get("day_total_tokens") or 0),
            "status": status,
        }

    @staticmethod
    def _summarize_entries(entries: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
        summary = {
            "entry_count": len(entries),
            "available_count": 0,
            "cooldown_count": 0,
            "disabled_count": 0,
        }
        for entry in entries:
            status = str(entry.get("status") or "")
            if status == "available":
                summary["available_count"] += 1
            elif status == "cooldown":
                summary["cooldown_count"] += 1
            elif status == "disabled":
                summary["disabled_count"] += 1
        return summary

    def _is_entry_available(
        self,
        group: AuthGroupSchema,
        entry: AuthEntrySchema,
        runtime_state: Mapping[str, Any],
        usage: Mapping[str, Any],
        now: datetime,
    ) -> bool:
        if not entry.enabled:
            return False
        if bool(runtime_state.get("disabled")):
            return False
        cooldown_until = parse_local_datetime(runtime_state.get("cooldown_until"))
        if cooldown_until is not None and cooldown_until > now:
            return False
        inflight = self._inflight_counts.get((group.name, entry.id), 0)
        if entry.max_concurrency is not None and inflight >= entry.max_concurrency:
            return False
        if self._is_request_quota_exceeded(entry, usage):
            return False
        if self._is_token_quota_exceeded(entry, usage):
            return False
        return True

    @staticmethod
    def _is_request_quota_exceeded(entry: AuthEntrySchema, usage: Mapping[str, Any]) -> bool:
        minute_requests = int(usage.get("minute_request_count") or 0)
        day_requests = int(usage.get("day_request_count") or 0)
        if entry.request_quota_per_minute is not None and minute_requests >= entry.request_quota_per_minute:
            return True
        if entry.request_quota_per_day is not None and day_requests >= entry.request_quota_per_day:
            return True
        return False

    @staticmethod
    def _is_token_quota_exceeded(entry: AuthEntrySchema, usage: Mapping[str, Any]) -> bool:
        minute_tokens = int(usage.get("minute_total_tokens") or 0)
        day_tokens = int(usage.get("day_total_tokens") or 0)
        if entry.token_quota_per_minute is not None and minute_tokens >= entry.token_quota_per_minute:
            return True
        if entry.token_quota_per_day is not None and day_tokens >= entry.token_quota_per_day:
            return True
        return False

    def _resolve_cooldown_seconds(
        self,
        group: AuthGroupSchema,
        entry: AuthEntrySchema,
        response_headers: Optional[Mapping[str, Any]],
    ) -> int:
        retry_after_seconds = self._parse_retry_after_seconds(response_headers)
        if retry_after_seconds is not None and retry_after_seconds > 0:
            return retry_after_seconds
        if entry.cooldown_seconds_on_429 is not None:
            return entry.cooldown_seconds_on_429
        return group.cooldown_seconds_on_429

    @staticmethod
    def _parse_retry_after_seconds(response_headers: Optional[Mapping[str, Any]]) -> Optional[int]:
        if not response_headers:
            return None
        retry_after_value: Optional[Any] = None
        for key, value in response_headers.items():
            if str(key).lower() == "retry-after":
                retry_after_value = value
                break
        if retry_after_value in (None, ""):
            return None

        try:
            parsed_seconds = int(str(retry_after_value).strip())
            return parsed_seconds if parsed_seconds > 0 else None
        except (TypeError, ValueError):
            pass

        try:
            retry_after_datetime = parsedate_to_datetime(str(retry_after_value).strip())
        except (TypeError, ValueError, IndexError):
            return None
        if retry_after_datetime.tzinfo is None:
            retry_after_datetime = retry_after_datetime.replace(tzinfo=timezone.utc)
        delta_seconds = int((retry_after_datetime - datetime.now(timezone.utc)).total_seconds())
        return delta_seconds if delta_seconds > 0 else None

    def _rotation_distance(self, group_name: str, index: int, size: int) -> int:
        cursor = self._rotation_cursor_by_group.get(group_name, 0)
        return (index - cursor) % max(size, 1)

    @staticmethod
    def _find_entry(group: AuthGroupSchema, entry_id: str) -> Optional[AuthEntrySchema]:
        normalized_entry_id = str(entry_id).strip()
        for entry in group.entries:
            if entry.id == normalized_entry_id:
                return entry
        return None

    def _resolve_explicit_entry(self, auth_group_name: str, entry_id: str) -> tuple[AuthGroupSchema, AuthEntrySchema]:
        group = self._groups_by_name.get(str(auth_group_name).strip())
        if group is None:
            raise ValueError(f"Auth group not found: {auth_group_name}")
        entry = self._find_entry(group, entry_id)
        if entry is None:
            raise ValueError(f"Auth entry not found: {entry_id}")
        return group, entry
