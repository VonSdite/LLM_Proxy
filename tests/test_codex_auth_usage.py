from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext, Logger
from src.repositories.log_repository import LogRepository
from src.services.codex_oauth_service import (
    CODEX_USAGE_ACTIVE_REFRESH_DELAY_SECONDS,
    CODEX_USAGE_ACTIVE_REFRESH_INTERVAL_SECONDS,
    CodexAuthCandidate,
    CodexOAuthService,
)
from src.services.codex_proxy_service import CodexProxyService
from src.utils.database import create_connection_factory
from src.utils.local_time import parse_local_datetime


class FakeLogger:
    def info(self, msg: str, *args: object) -> None:
        del msg, args

    def warning(self, msg: str, *args: object) -> None:
        del msg, args

    def error(self, msg: str, *args: object) -> None:
        del msg, args

    def debug(self, msg: str, *args: object) -> None:
        del msg, args


class FakeConfigManager:
    def get_oauth_proxy(self) -> None:
        return None

    def is_oauth_verify_ssl_enabled(self) -> bool:
        return False


class CodexAuthUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.auth_dir = self.root / "data" / "oauth" / "codex"
        self.auth_dir.mkdir(parents=True)
        self.auth_file = self.auth_dir / "codex-demo.json"
        self.auth_file.write_text(
            json.dumps(
                {
                    "type": "codex",
                    "email": "demo@example.com",
                    "account_id": "account-demo",
                    "access_token": "access-demo",
                    "plan_type": "plus",
                    "expired": "2999-01-01T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        ctx = AppContext(
            logger=cast(Logger, FakeLogger()),
            config_manager=FakeConfigManager(),  # type: ignore[arg-type]
            root_path=self.root,
            flask_app=Flask(__name__),
        )
        self.repository = LogRepository(create_connection_factory(self.root / "requests.db"))
        self.service = CodexOAuthService(ctx, self.repository)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _quota(*, used_percent: float, reset_at: str, refreshed_at: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "refreshed_at": refreshed_at,
            "windows": [
                {
                    "label": "Codex 7 天",
                    "window_key": "secondary_window",
                    "limit_window_seconds": 604800,
                    "used_percent": used_percent,
                    "remaining_percent": 100 - used_percent,
                    "reset_at": reset_at,
                }
            ],
        }

    def _local_datetime(self, iso_value: str) -> datetime:
        value = parse_local_datetime(self.service._format_usage_query_time(iso_value))
        assert value is not None
        return value

    def _insert_usage(self, iso_value: str, *, tokens: int, cost: float) -> None:
        started_at = self._local_datetime(iso_value)
        self.repository.insert(
            request_model="public-model",
            target_model_id="gpt-6-astra",
            response_model="gpt-6-astra",
            total_tokens=tokens,
            prompt_tokens=tokens // 2,
            completion_tokens=tokens // 2,
            usage_status="known",
            start_time=started_at,
            end_time=started_at,
            auth_file_name=self.auth_file.name,
            auth_account_id="account-demo",
            estimated_cost_usd=cost,
        )

    def test_mid_window_baseline_estimates_from_local_increment_only(self) -> None:
        baseline_quota = self._quota(
            used_percent=40,
            reset_at="2026-09-21T00:00:00Z",
            refreshed_at="2026-09-16T00:00:00Z",
        )
        self.service._store_auth_file_quota(self.auth_file.name, baseline_quota)
        self.service._ensure_auth_file_usage_tracking(self.auth_file.name, "account-demo", baseline_quota)
        self._insert_usage("2026-09-17T00:00:00Z", tokens=12_000, cost=120)

        self.service._store_auth_file_quota(
            self.auth_file.name,
            self._quota(
                used_percent=46,
                reset_at="2026-09-21T00:00:00Z",
                refreshed_at="2026-09-18T00:00:00Z",
            ),
        )
        usage = self.service.list_auth_files()["files"][0]["current_usage"]

        self.assertEqual(1, usage["request_count"])
        self.assertEqual(12_000, usage["total_tokens"])
        self.assertEqual(6, usage["consumed_percent"])
        self.assertAlmostEqual(2_000, usage["estimated_full_cost_usd"])

    def test_new_reset_window_keeps_previous_usage_and_regroups_on_real_boundary(self) -> None:
        baseline_quota = self._quota(
            used_percent=40,
            reset_at="2026-09-21T00:00:00Z",
            refreshed_at="2026-09-16T00:00:00Z",
        )
        self.service._store_auth_file_quota(self.auth_file.name, baseline_quota)
        self.service._ensure_auth_file_usage_tracking(self.auth_file.name, "account-demo", baseline_quota)
        self._insert_usage("2026-09-17T00:00:00Z", tokens=12_000, cost=120)
        self.service._store_auth_file_quota(
            self.auth_file.name,
            self._quota(
                used_percent=46,
                reset_at="2026-09-21T00:00:00Z",
                refreshed_at="2026-09-18T00:00:00Z",
            ),
        )

        self.service._store_auth_file_quota(
            self.auth_file.name,
            self._quota(
                used_percent=2,
                reset_at="2026-09-28T00:00:00Z",
                refreshed_at="2026-09-22T00:00:00Z",
            ),
        )
        self._insert_usage("2026-09-22T00:00:00Z", tokens=5_000, cost=50)
        auth_file = self.service.list_auth_files()["files"][0]

        self.assertEqual(1, auth_file["previous_usage"]["request_count"])
        self.assertEqual(12_000, auth_file["previous_usage"]["total_tokens"])
        self.assertEqual(120, auth_file["previous_usage"]["estimated_cost_usd"])
        self.assertEqual(6, auth_file["previous_usage"]["consumed_percent"])
        self.assertAlmostEqual(2_000, auth_file["previous_usage"]["estimated_full_cost_usd"])
        self.assertEqual(1, auth_file["current_usage"]["request_count"])
        self.assertEqual(5_000, auth_file["current_usage"]["total_tokens"])
        self.assertAlmostEqual(2_500, auth_file["current_usage"]["estimated_full_cost_usd"])

    def test_same_sticky_auth_file_refreshes_quota_only_once(self) -> None:
        candidate = CodexAuthCandidate(
            name=self.auth_file.name,
            path=self.auth_file,
            access_token="access-demo",
            account_id="account-demo",
            email="demo@example.com",
            plan_type="plus",
            payload={},
        )
        quota = self._quota(
            used_percent=40,
            reset_at="2026-09-21T00:00:00Z",
            refreshed_at="2026-09-16T00:00:00Z",
        )

        with patch.object(self.service, "_get_auth_file_quota", return_value=quota) as refresh_mock:
            self.assertIs(candidate, self.service.prepare_auth_candidate_for_use(candidate))
            self.assertIs(candidate, self.service.prepare_auth_candidate_for_use(candidate))

        refresh_mock.assert_called_once()

    def test_success_schedules_rate_limited_active_usage_refresh(self) -> None:
        candidate = CodexAuthCandidate(
            name=self.auth_file.name,
            path=self.auth_file,
            access_token="access-demo",
            account_id="account-demo",
            email="demo@example.com",
            plan_type="plus",
            payload={},
        )
        baseline_quota = self._quota(
            used_percent=40,
            reset_at="2026-09-21T00:00:00Z",
            refreshed_at="2026-09-16T00:00:00Z",
        )
        refreshed_quota = self._quota(
            used_percent=46,
            reset_at="2026-09-21T00:00:00Z",
            refreshed_at="2026-09-18T00:00:00Z",
        )
        scheduled: list[tuple[float, Any, tuple[Any, ...]]] = []

        def fake_spawn_later(delay: float, func: Any, *args: Any) -> object:
            scheduled.append((delay, func, args))
            return object()

        with patch.object(self.service, "_get_auth_file_quota", return_value=baseline_quota):
            self.assertIs(candidate, self.service.prepare_auth_candidate_for_use(candidate))
        self._insert_usage("2026-09-17T00:00:00Z", tokens=12_000, cost=120)
        with (
            patch("src.services.codex_oauth_service.time.monotonic", side_effect=(100.0, 101.0, 401.0)),
            patch("gevent.spawn_later", side_effect=fake_spawn_later),
        ):
            self.service.record_auth_file_success(self.auth_file.name)
            self.service.record_auth_file_success(self.auth_file.name)
            self.service.record_auth_file_success(self.auth_file.name)

        self.assertEqual(2, len(scheduled))
        self.assertEqual(CODEX_USAGE_ACTIVE_REFRESH_DELAY_SECONDS, scheduled[0][0])
        self.assertEqual(self.service.refresh_auth_file_quota_snapshot, scheduled[0][1])
        self.assertEqual((self.auth_file.name,), scheduled[0][2])
        self.assertEqual(5 * 60, CODEX_USAGE_ACTIVE_REFRESH_INTERVAL_SECONDS)

        self.service._store_auth_file_quota(self.auth_file.name, refreshed_quota)
        usage = self.service.list_auth_files()["files"][0]["current_usage"]
        self.assertAlmostEqual(2_000, usage["estimated_full_cost_usd"])

    def test_unknown_model_cost_remains_unavailable_with_auth_identity(self) -> None:
        meta = CodexProxyService._build_auth_usage_meta(
            {
                "response_model": "unknown-model",
                "usage_status": "known",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            model_name="unknown-request-model",
            auth_file_name=self.auth_file.name,
            auth_account_id="account-demo",
        )

        self.assertEqual(self.auth_file.name, meta["auth_file_name"])
        self.assertEqual("account-demo", meta["auth_account_id"])
        self.assertIsNone(meta["estimated_cost_usd"])


if __name__ == "__main__":
    unittest.main()
