from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import uuid4
from zipfile import ZipFile

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext, Logger
from src.presentation.web_controller import WebController
from src.repositories.log_repository import LogRepository
from src.repositories.user_repository import UserRepository
from src.services import AuthenticationService, LogService, SettingsService
from src.utils.database import create_connection_factory


class FakeLogger:
    def info(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None:
        del msg, args, exc_info, stack_info, stacklevel, extra

    def warning(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None:
        del msg, args, exc_info, stack_info, stacklevel, extra

    def error(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None:
        del msg, args, exc_info, stack_info, stacklevel, extra

    def debug(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None:
        del msg, args, exc_info, stack_info, stacklevel, extra


class FakeAuthService:
    def is_auth_enabled(self) -> bool:
        return False

    def validate_session(self, session_token: str | None) -> bool:
        del session_token
        return True

    def get_session_username(self, session_token: str | None) -> str:
        del session_token
        return ""


class FakeSettingsService:
    def get_system_settings(self) -> dict:
        return {}

    def update_system_settings(self, payload: dict) -> dict:
        del payload
        raise RuntimeError("Settings service is not configured for this test")

    def update_basic_settings(self, payload: dict) -> dict:
        del payload
        raise RuntimeError("Settings service is not configured for this test")

    def update_debug_settings(self, payload: dict) -> dict:
        del payload
        raise RuntimeError("Settings service is not configured for this test")


class DashboardFilterApiTests(unittest.TestCase):
    DATE_FILTER = {
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
    }

    def setUp(self) -> None:
        self.root_path = Path(__file__).resolve().parents[1]
        self.db_path = self.root_path / f"dashboard-filters-{uuid4().hex}.db"
        self.app = Flask(__name__)
        self.ctx = AppContext(
            logger=cast(Logger, FakeLogger()),
            config_manager=None,  # type: ignore[arg-type]
            root_path=self.root_path,
            flask_app=self.app,
        )
        self.connection_factory = create_connection_factory(self.db_path)
        self.log_repository = LogRepository(self.connection_factory)
        self.user_repository = UserRepository(self.connection_factory)
        self.log_service = LogService(self.ctx, self.log_repository)
        WebController(
            self.ctx,
            self.log_service,
            cast(SettingsService, FakeSettingsService()),
            cast(AuthenticationService, FakeAuthService()),
        )
        self.client = self.app.test_client()

        self.user_repository.create("alice", "10.0.0.1")
        self.user_repository.create("bob", "10.0.0.2")
        self.user_repository.create("carol", "10.0.0.3")

        self._log_request("model-a", "resp-a", 10, "10.0.0.1", datetime(2026, 4, 8, 9, 0, 0))
        self._log_request("model-c", "resp-c", 20, "10.0.0.1", datetime(2026, 4, 8, 10, 0, 0))
        self._log_request("model-b", "resp-b", 30, "10.0.0.2", datetime(2026, 4, 8, 11, 0, 0))
        self._log_request("model-a", "resp-a", 40, "10.0.0.3", datetime(2026, 4, 8, 12, 0, 0))

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def _log_request(
        self,
        request_model: str,
        response_model: str,
        total_tokens: int,
        ip_address: str,
        start_time: datetime,
    ) -> None:
        self.log_service.log_request(
            request_model=request_model,
            response_model=response_model,
            total_tokens=total_tokens,
            prompt_tokens=total_tokens // 2,
            completion_tokens=total_tokens // 2,
            start_time=start_time,
            end_time=start_time,
            ip_address=ip_address,
        )

    def test_statistics_api_supports_multi_value_filters(self) -> None:
        response = self.client.get(
            "/api/statistics",
            query_string=[
                ("start_date", self.DATE_FILTER["start_date"]),
                ("end_date", self.DATE_FILTER["end_date"]),
                ("username", "alice"),
                ("username", "bob"),
                ("request_model", "model-a"),
                ("request_model", "model-b"),
            ],
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(
            {("alice", "model-a"), ("bob", "model-b")},
            {(item["username"], item["request_model"]) for item in payload},
        )

    def test_usage_status_distinguishes_known_and_unknown_counts(self) -> None:
        self.log_service.log_request(
            request_model="model-a",
            response_model="resp-a",
            total_tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
            usage_status="unknown",
            start_time=datetime(2026, 4, 8, 13, 0, 0),
            end_time=datetime(2026, 4, 8, 13, 0, 0),
            ip_address="10.0.0.1",
        )

        response = self.client.get(
            "/api/statistics",
            query_string={
                **self.DATE_FILTER,
                "username": "alice",
                "request_model": "model-a",
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(1, len(payload))
        self.assertEqual("partial", payload[0]["usage_status"])

    def test_user_usage_summary_api_groups_by_username(self) -> None:
        self._log_request("model-a", "resp-extra", 5, "10.0.0.1", datetime(2026, 4, 9, 9, 0, 0))

        response = self.client.get(
            "/api/statistics/user-usage-summary",
            query_string=[
                ("start_date", self.DATE_FILTER["start_date"]),
                ("end_date", self.DATE_FILTER["end_date"]),
                ("username", "alice"),
            ],
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(1, len(payload))
        self.assertEqual("alice", payload[0]["username"])
        self.assertNotIn("request_model", payload[0])
        self.assertEqual(3, payload[0]["request_count"])
        self.assertEqual(35, payload[0]["total_tokens"])
        self.assertEqual(1, payload[0]["ip_count"])
        self.assertEqual("2026-04-09", payload[0]["last_request_date"])

    def test_request_logs_api_supports_multi_value_filters(self) -> None:
        response = self.client.get(
            "/api/request-logs",
            query_string=[
                ("page", "1"),
                ("page_size", "50"),
                ("start_date", self.DATE_FILTER["start_date"]),
                ("end_date", self.DATE_FILTER["end_date"]),
                ("username", "alice"),
                ("username", "bob"),
                ("request_model", "model-a"),
                ("request_model", "model-b"),
            ],
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(2, payload["total"])
        self.assertEqual(
            {("alice", "model-a"), ("bob", "model-b")},
            {(item["username"], item["request_model"]) for item in payload["logs"]},
        )

    def test_statistics_export_logs_returns_full_xlsx_without_pagination(self) -> None:
        response = self.client.get(
            "/api/statistics/export",
            query_string={
                "tab": "logs",
                "start_date": self.DATE_FILTER["start_date"],
                "end_date": self.DATE_FILTER["end_date"],
                "sort_key": "total_tokens",
                "sort_direction": "asc",
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            response.headers["Content-Type"],
        )
        self.assertIn("request-logs-", response.headers["Content-Disposition"])
        with ZipFile(BytesIO(response.data)) as archive:
            sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

        self.assertIn("请求模型", sheet_xml)
        self.assertIn("Token 状态", sheet_xml)
        self.assertIn("完整", sheet_xml)
        self.assertIn("model-a", sheet_xml)
        self.assertIn("model-b", sheet_xml)
        self.assertIn("model-c", sheet_xml)

    def test_statistics_export_summary_returns_xlsx(self) -> None:
        response = self.client.get(
            "/api/statistics/export",
            query_string={
                "tab": "stats",
                "start_date": self.DATE_FILTER["start_date"],
                "end_date": self.DATE_FILTER["end_date"],
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertIn("call-summary-", response.headers["Content-Disposition"])
        with ZipFile(BytesIO(response.data)) as archive:
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
            sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

        self.assertIn("调用汇总", workbook_xml)
        self.assertIn("响应模型", sheet_xml)
        self.assertIn("Token 状态", sheet_xml)
        self.assertIn("缓存读取 Token", sheet_xml)
        self.assertIn("缓存写入 Token", sheet_xml)
        self.assertIn("缓存命中率", sheet_xml)
        self.assertIn("resp-a", sheet_xml)

    def test_statistics_export_user_usage_summary_returns_xlsx(self) -> None:
        response = self.client.get(
            "/api/statistics/export",
            query_string={
                "tab": "user_usage",
                "start_date": self.DATE_FILTER["start_date"],
                "end_date": self.DATE_FILTER["end_date"],
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertIn("user-usage-", response.headers["Content-Disposition"])
        with ZipFile(BytesIO(response.data)) as archive:
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
            sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

        self.assertIn("用户用量", workbook_xml)
        self.assertIn("关联 IP 数", sheet_xml)
        self.assertIn("Token 状态", sheet_xml)
        self.assertIn("缓存命中率", sheet_xml)
        self.assertNotIn("请求模型", sheet_xml)
        self.assertIn("alice", sheet_xml)

    def test_daily_stats_export_returns_json_rows(self) -> None:
        response = self.client.get(
            "/api/statistics/daily-stats/export",
            query_string=[
                ("start_date", self.DATE_FILTER["start_date"]),
                ("end_date", self.DATE_FILTER["end_date"]),
                ("username", "alice"),
                ("request_model", "model-a"),
            ],
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(3, payload["version"])
        self.assertEqual("llm_proxy.statistics", payload["kind"])
        self.assertEqual("known", payload["request_logs"][0]["usage_status"])
        self.assertEqual("known", payload["daily_request_stats"][0]["usage_status"])
        self.assertEqual("unknown", payload["request_logs"][0]["cache_usage_status"])
        self.assertEqual("unknown", payload["daily_request_stats"][0]["cache_usage_status"])
        self.assertEqual(
            {("10.0.0.1", "model-a", "resp-a", 10)},
            {
                (item["ip_address"], item["request_model"], item["response_model"], item["total_tokens"])
                for item in payload["request_logs"]
            },
        )
        self.assertEqual(
            {("2026-04-08", "10.0.0.1", "model-a")},
            {(item["stat_date"], item["ip_address"], item["request_model"]) for item in payload["daily_request_stats"]},
        )

    def test_daily_stats_import_merges_duplicate_keys(self) -> None:
        payload = {
            "daily_request_stats": [
                {
                    "stat_date": "2026-04-08",
                    "ip_address": "10.0.0.1",
                    "request_model": "model-a",
                    "response_model": "resp-a",
                    "request_count": 2,
                    "total_tokens": 12,
                    "prompt_tokens": 5,
                    "completion_tokens": 7,
                },
                {
                    "stat_date": "2026-04-10",
                    "ip_address": "10.0.0.2",
                    "request_model": "model-imported",
                    "response_model": "resp-imported",
                    "request_count": 1,
                    "total_tokens": 9,
                    "prompt_tokens": 4,
                    "completion_tokens": 5,
                },
            ]
        }
        response = self.client.post(
            "/api/statistics/daily-stats/import",
            json=payload,
        )

        self.assertEqual(201, response.status_code)
        result = response.get_json()
        self.assertEqual(2, result["count"])
        self.assertEqual(1, result["daily_request_stats_inserted_count"])
        self.assertEqual(1, result["daily_request_stats_updated_count"])
        self.assertEqual(1, result["daily_request_stats_merged_count"])

        stats_response = self.client.get(
            "/api/statistics",
            query_string={
                "start_date": "2026-04-08",
                "end_date": "2026-04-10",
                "username": "alice",
                "request_model": "model-a",
            },
        )
        self.assertEqual(200, stats_response.status_code)
        merged_row = stats_response.get_json()[0]
        self.assertEqual(3, merged_row["request_count"])
        self.assertEqual(22, merged_row["total_tokens"])
        self.assertEqual(10, merged_row["prompt_tokens"])
        self.assertEqual(12, merged_row["completion_tokens"])
        self.assertEqual("known", merged_row["usage_status"])

    def test_statistics_v2_import_preserves_usage_status(self) -> None:
        payload = {
            "version": 2,
            "kind": "llm_proxy.statistics",
            "request_logs": [
                {
                    "api_key_id": None,
                    "ip_address": "10.0.0.2",
                    "request_model": "model-partial-log",
                    "response_model": "resp-partial-log",
                    "total_tokens": 8,
                    "prompt_tokens": 8,
                    "completion_tokens": 0,
                    "usage_status": "partial",
                    "start_time": "2026-04-12 09:00:00.000000",
                    "end_time": "2026-04-12 09:00:01.000000",
                    "created_at": "2026-04-12 09:00:01.000000",
                }
            ],
            "daily_request_stats": [
                {
                    "stat_date": "2026-04-12",
                    "ip_address": "10.0.0.2",
                    "request_model": "model-partial-stat",
                    "response_model": "resp-partial-stat",
                    "request_count": 1,
                    "total_tokens": 8,
                    "prompt_tokens": 8,
                    "completion_tokens": 0,
                    "usage_status": "partial",
                }
            ],
        }

        response = self.client.post("/api/statistics/daily-stats/import", json=payload)

        self.assertEqual(201, response.status_code)
        logs_response = self.client.get(
            "/api/request-logs",
            query_string={"start_date": "2026-04-12", "end_date": "2026-04-12"},
        )
        self.assertEqual("partial", logs_response.get_json()["logs"][0]["usage_status"])
        stats_response = self.client.get(
            "/api/statistics",
            query_string={
                "start_date": "2026-04-12",
                "end_date": "2026-04-12",
                "request_model": "model-partial-stat",
            },
        )
        self.assertEqual("partial", stats_response.get_json()[0]["usage_status"])

    def test_cache_hit_rate_uses_only_requests_with_known_cache_usage(self) -> None:
        self.log_service.log_request(
            request_model="model-cache",
            response_model="resp-cache",
            total_tokens=120,
            prompt_tokens=100,
            completion_tokens=20,
            cache_read_input_tokens=40,
            cache_creation_input_tokens=10,
            cache_usage_status="known",
            start_time=datetime(2026, 4, 13, 9, 0, 0),
            end_time=datetime(2026, 4, 13, 9, 0, 1),
            ip_address="10.0.0.1",
        )
        self.log_service.log_request(
            request_model="model-cache",
            response_model="resp-cache",
            total_tokens=220,
            prompt_tokens=200,
            completion_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            cache_usage_status="unknown",
            start_time=datetime(2026, 4, 13, 10, 0, 0),
            end_time=datetime(2026, 4, 13, 10, 0, 1),
            ip_address="10.0.0.1",
        )

        response = self.client.get(
            "/api/statistics",
            query_string={
                "start_date": "2026-04-13",
                "end_date": "2026-04-13",
                "request_model": "model-cache",
            },
        )

        self.assertEqual(200, response.status_code)
        row = response.get_json()[0]
        self.assertEqual(40, row["cache_read_input_tokens"])
        self.assertEqual(10, row["cache_creation_input_tokens"])
        self.assertEqual("partial", row["cache_usage_status"])
        self.assertEqual(0.4, row["cache_hit_rate"])

    def test_known_zero_cache_usage_has_zero_hit_rate(self) -> None:
        self.log_service.log_request(
            request_model="model-no-hit",
            response_model="resp-no-hit",
            total_tokens=60,
            prompt_tokens=50,
            completion_tokens=10,
            cache_usage_status="known",
            start_time=datetime(2026, 4, 14, 9, 0, 0),
            end_time=datetime(2026, 4, 14, 9, 0, 1),
            ip_address="10.0.0.2",
        )

        response = self.client.get(
            "/api/statistics",
            query_string={
                "start_date": "2026-04-14",
                "end_date": "2026-04-14",
                "request_model": "model-no-hit",
            },
        )

        row = response.get_json()[0]
        self.assertEqual("known", row["cache_usage_status"])
        self.assertEqual(0.0, row["cache_hit_rate"])

    def test_statistics_v3_import_preserves_cache_usage(self) -> None:
        payload = {
            "version": 3,
            "kind": "llm_proxy.statistics",
            "request_logs": [
                {
                    "ip_address": "10.0.0.2",
                    "request_model": "model-cache-log",
                    "response_model": "resp-cache-log",
                    "total_tokens": 12,
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "usage_status": "known",
                    "cache_read_input_tokens": 4,
                    "cache_creation_input_tokens": 2,
                    "cache_usage_status": "known",
                    "start_time": "2026-04-15 09:00:00.000000",
                    "end_time": "2026-04-15 09:00:01.000000",
                }
            ],
            "daily_request_stats": [
                {
                    "stat_date": "2026-04-15",
                    "ip_address": "10.0.0.2",
                    "request_model": "model-cache-stat",
                    "response_model": "resp-cache-stat",
                    "request_count": 1,
                    "total_tokens": 12,
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "usage_status": "known",
                    "cache_read_input_tokens": 4,
                    "cache_creation_input_tokens": 2,
                    "cache_known_prompt_tokens": 10,
                    "cache_usage_status": "known",
                }
            ],
        }

        response = self.client.post("/api/statistics/daily-stats/import", json=payload)

        self.assertEqual(201, response.status_code)
        logs = self.client.get(
            "/api/request-logs",
            query_string={"start_date": "2026-04-15", "end_date": "2026-04-15"},
        ).get_json()["logs"]
        imported_log = next(item for item in logs if item["request_model"] == "model-cache-log")
        self.assertEqual(4, imported_log["cache_read_input_tokens"])
        self.assertEqual(0.4, imported_log["cache_hit_rate"])
        stats = self.client.get(
            "/api/statistics",
            query_string={
                "start_date": "2026-04-15",
                "end_date": "2026-04-15",
                "request_model": "model-cache-stat",
            },
        ).get_json()[0]
        self.assertEqual(0.4, stats["cache_hit_rate"])

    def test_statistics_v3_import_uses_only_known_cache_prompt_as_denominator(self) -> None:
        payload = {
            "version": 3,
            "kind": "llm_proxy.statistics",
            "request_logs": [],
            "daily_request_stats": [
                {
                    "stat_date": "2026-04-16",
                    "ip_address": "10.0.0.2",
                    "request_model": "model-cache-mixed",
                    "response_model": "resp-cache-mixed",
                    "request_count": 1,
                    "total_tokens": 120,
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "usage_status": "known",
                    "cache_read_input_tokens": 40,
                    "cache_creation_input_tokens": 10,
                    "cache_known_prompt_tokens": 100,
                    "cache_usage_status": "known",
                },
                {
                    "stat_date": "2026-04-16",
                    "ip_address": "10.0.0.2",
                    "request_model": "model-cache-mixed",
                    "response_model": "resp-cache-mixed",
                    "request_count": 1,
                    "total_tokens": 220,
                    "prompt_tokens": 200,
                    "completion_tokens": 20,
                    "usage_status": "known",
                    "cache_usage_status": "unknown",
                },
            ],
        }

        response = self.client.post("/api/statistics/daily-stats/import", json=payload)

        self.assertEqual(201, response.status_code)
        stats = self.client.get(
            "/api/statistics",
            query_string={
                "start_date": "2026-04-16",
                "end_date": "2026-04-16",
                "request_model": "model-cache-mixed",
            },
        ).get_json()[0]
        self.assertEqual(2, stats["request_count"])
        self.assertEqual(100, stats["cache_known_prompt_tokens"])
        self.assertEqual("partial", stats["cache_usage_status"])
        self.assertEqual(0.4, stats["cache_hit_rate"])

    def test_request_logs_import_skips_duplicate_detail_rows(self) -> None:
        export_response = self.client.get(
            "/api/statistics/daily-stats/export",
            query_string=[
                ("start_date", self.DATE_FILTER["start_date"]),
                ("end_date", self.DATE_FILTER["end_date"]),
                ("username", "alice"),
                ("request_model", "model-a"),
            ],
        )
        self.assertEqual(200, export_response.status_code)
        duplicate_log = dict(export_response.get_json()["request_logs"][0])
        duplicate_log["id"] = 999999
        new_log = {
            "id": 1000000,
            "api_key_id": None,
            "ip_address": "10.0.0.2",
            "request_model": "model-imported-log",
            "response_model": "resp-imported-log",
            "total_tokens": 18,
            "prompt_tokens": 8,
            "completion_tokens": 10,
            "start_time": "2026-04-11 09:00:00.000000",
            "end_time": "2026-04-11 09:00:01.000000",
            "created_at": "2026-04-11 09:00:01.000000",
        }

        response = self.client.post(
            "/api/statistics/daily-stats/import",
            json={"request_logs": [duplicate_log, new_log]},
        )

        self.assertEqual(201, response.status_code)
        result = response.get_json()
        self.assertEqual(2, result["request_logs_count"])
        self.assertEqual(1, result["request_logs_inserted_count"])
        self.assertEqual(1, result["request_logs_skipped_count"])
        self.assertEqual(1, result["request_logs_duplicate_count"])

        logs_response = self.client.get(
            "/api/request-logs",
            query_string={
                "page": "1",
                "page_size": "50",
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
            },
        )
        self.assertEqual(200, logs_response.status_code)
        logs_payload = logs_response.get_json()
        self.assertEqual(5, logs_payload["total"])
        self.assertEqual(
            1,
            sum(1 for item in logs_payload["logs"] if item["request_model"] == "model-imported-log"),
        )

    def test_statistics_api_sorts_on_server(self) -> None:
        response = self.client.get(
            "/api/statistics",
            query_string={
                **self.DATE_FILTER,
                "sort_key": "total_tokens",
                "sort_direction": "asc",
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual([10, 20, 30, 40], [item["total_tokens"] for item in payload])

    def test_request_logs_api_sorts_before_pagination(self) -> None:
        response = self.client.get(
            "/api/request-logs",
            query_string={
                "page": "1",
                "page_size": "1",
                **self.DATE_FILTER,
                "sort_key": "total_tokens",
                "sort_direction": "asc",
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(4, payload["total"])
        self.assertEqual(10, payload["logs"][0]["total_tokens"])

    def test_request_logs_api_sorts_duration_before_pagination(self) -> None:
        start_time = datetime(2026, 4, 7, 8, 0, 0)
        self.log_service.log_request(
            request_model="model-duration",
            response_model="resp-duration",
            total_tokens=50,
            prompt_tokens=25,
            completion_tokens=25,
            start_time=start_time,
            end_time=start_time + timedelta(seconds=5),
            ip_address="10.0.0.2",
        )

        response = self.client.get(
            "/api/request-logs",
            query_string={
                "page": "1",
                "page_size": "1",
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "sort_key": "duration",
                "sort_direction": "desc",
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(5, payload["total"])
        self.assertEqual("model-duration", payload["logs"][0]["request_model"])

    def test_statistics_api_rejects_missing_date_range(self) -> None:
        response = self.client.get("/api/statistics")

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {"error": "start_date and end_date are required"},
            response.get_json(),
        )

    def test_request_logs_api_rejects_date_range_over_one_year(self) -> None:
        response = self.client.get(
            "/api/request-logs",
            query_string={
                "page": "1",
                "page_size": "50",
                "start_date": "2025-01-01",
                "end_date": "2026-01-02",
            },
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {"error": "date range must not exceed one year"},
            response.get_json(),
        )


class LogRepositoryUsageStatusMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(__file__).resolve().parents[1] / f"usage-status-migration-{uuid4().hex}.db"
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE request_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT,
                    request_model TEXT NOT NULL,
                    response_model TEXT,
                    total_tokens INTEGER,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE daily_request_stats (
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
                );
                INSERT INTO request_logs (
                    ip_address, request_model, response_model, total_tokens,
                    prompt_tokens, completion_tokens, start_time, end_time, created_at
                ) VALUES
                    ('10.0.0.1', 'known-log', 'resp', 12, 7, 5,
                     '2026-04-01 00:00:00.000000', '2026-04-01 00:00:01.000000',
                     '2026-04-01 00:00:01.000000'),
                    ('10.0.0.2', 'unknown-log', 'resp', 0, 0, 0,
                     '2026-04-01 00:00:00.000000', '2026-04-01 00:00:01.000000',
                     '2026-04-01 00:00:01.000000');
                INSERT INTO daily_request_stats (
                    stat_date, ip_address, request_model, response_model, request_count,
                    total_tokens, prompt_tokens, completion_tokens, created_at, updated_at
                ) VALUES
                    ('2026-04-01', '10.0.0.1', 'known-stat', 'resp', 1, 12, 7, 5,
                     '2026-04-01 00:00:01.000000', '2026-04-01 00:00:01.000000'),
                    ('2026-04-01', '10.0.0.2', 'unknown-stat', 'resp', 1, 0, 0, 0,
                     '2026-04-01 00:00:01.000000', '2026-04-01 00:00:01.000000');
                """
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def test_legacy_tables_add_and_backfill_usage_status(self) -> None:
        connection_factory = create_connection_factory(self.db_path)

        LogRepository(connection_factory)

        with connection_factory() as conn:
            request_columns = {row["name"] for row in conn.execute("PRAGMA table_info(request_logs)")}
            daily_columns = {row["name"] for row in conn.execute("PRAGMA table_info(daily_request_stats)")}
            request_statuses = dict(conn.execute("SELECT request_model, usage_status FROM request_logs").fetchall())
            daily_statuses = dict(
                conn.execute("SELECT request_model, usage_status FROM daily_request_stats").fetchall()
            )

        self.assertIn("usage_status", request_columns)
        self.assertIn("usage_status", daily_columns)
        self.assertEqual({"known-log": "known", "unknown-log": "unknown"}, request_statuses)
        self.assertEqual({"known-stat": "known", "unknown-stat": "unknown"}, daily_statuses)


if __name__ == "__main__":
    unittest.main()
