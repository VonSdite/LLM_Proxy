from __future__ import annotations

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
        self.assertEqual("llm_proxy.statistics", payload["kind"])
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


if __name__ == "__main__":
    unittest.main()
