from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import patch
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.app_context import AppContext, Logger
from src.presentation.web_controller import WebController
from src.repositories.api_key_repository import ApiKeyRepository
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
        self.db_path = self.root_path / "tmp" / f"dashboard-filters-{uuid4().hex}.db"
        self.app = Flask(__name__)
        self.ctx = AppContext(
            logger=cast(Logger, FakeLogger()),
            config_manager=None,  # type: ignore[arg-type]
            root_path=self.root_path,
            flask_app=self.app,
        )
        self.connection_factory = create_connection_factory(self.db_path)
        self.api_key_repository = ApiKeyRepository(self.connection_factory)
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

    def test_response_and_ip_filters_apply_to_all_views_and_exports(self) -> None:
        query = {**self.DATE_FILTER, "response_model": ["resp-a", "resp-b"], "ip_address": ["10.0.0.1", "10.0.0.2"]}
        for path in ("/api/statistics", "/api/statistics/user-usage-summary"):
            with self.subTest(path=path):
                response = self.client.get(path, query_string=query)
                self.assertEqual(200, response.status_code)
                rows = response.get_json()
                self.assertEqual(40, sum(row["total_tokens"] for row in rows))
                self.assertEqual(2, sum(row["request_count"] for row in rows))
        overview = self.client.get("/api/statistics/overview", query_string=query).get_json()
        self.assertEqual(40, overview["totals"]["total_tokens"])
        for dimension in ("series", "models", "model_flows", "response_models", "ips"):
            self.assertEqual(40, sum(row["total_tokens"] for row in overview[dimension]))
        logs = self.client.get("/api/request-logs", query_string={**query, "page_size": 1}).get_json()
        self.assertEqual(2, logs["total"])
        self.assertEqual(1, len(logs["logs"]))
        package = self.client.get("/api/statistics/daily-stats/export", query_string=query).get_json()
        for key in ("request_logs", "daily_request_stats"):
            self.assertEqual(2, len(package[key]))
            self.assertEqual(40, sum(row["total_tokens"] for row in package[key]))
        for tab in ("stats", "model_usage", "response_model_usage", "user_usage", "ip_usage", "models", "logs"):
            with self.subTest(tab=tab):
                response = self.client.get("/api/statistics/export", query_string={**query, "tab": tab})
                self.assertEqual(200, response.status_code)
                with ZipFile(BytesIO(response.data)) as archive:
                    sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                self.assertNotIn("resp-c", sheet)
                self.assertNotIn("carol", sheet)
                self.assertNotIn("model-c", sheet)
                self.assertGreater(len(ElementTree.fromstring(sheet).findall(".//{*}row")), 1)
        empty = self.client.get("/api/request-logs", query_string={**query, "username": "carol"}).get_json()
        self.assertEqual(0, empty["total"])
        literal = self.client.get(
            "/api/request-logs", query_string={**query, "response_model": "' OR 1=1 --"}
        ).get_json()
        self.assertEqual(0, literal["total"])

    def test_response_filter_preserves_retry_failures_and_api_key_scope(self) -> None:
        key_id = self._create_api_key("filtered", "abcd")
        self.log_repository.insert(
            request_model="retry",
            response_model="actual",
            total_tokens=12,
            prompt_tokens=8,
            completion_tokens=4,
            start_time="2026-04-08 13:00:00",
            end_time="2026-04-08 13:00:01",
            ip_address="10.0.0.1",
            api_key_id=key_id,
            outcome="success",
            upstream_attempts=[{"outcome": "error"}, {"outcome": "success", "response_model": "actual"}],
        )
        query = {**self.DATE_FILTER, "response_model": "actual", "ip_address": "10.0.0.1"}
        overview = self.client.get("/api/statistics/overview", query_string=query).get_json()
        self.assertEqual(1, overview["totals"]["request_count"])
        for row in [overview["totals"], *overview["series"], *overview["models"], *overview["ips"]]:
            if not row["request_count"]:
                continue
            self.assertEqual(0.5, row["upstream_success_rate"])
            self.assertEqual(1, row["upstream_error_count"])
        rows = self.client.get("/api/statistics/api-key-usage-summary", query_string=query).get_json()
        self.assertEqual(1, len(rows))
        self.assertEqual(12, rows[0]["total_tokens"])
        self.assertEqual(0.5, rows[0]["upstream_success_rate"])
        wrong_ip = {**query, "ip_address": "10.0.0.2"}
        self.assertEqual([], self.client.get("/api/statistics/api-key-usage-summary", query_string=wrong_ip).get_json())
        for filters, expected_rows in ((query, 2), (wrong_ip, 1)):
            response = self.client.get("/api/statistics/export", query_string={**filters, "tab": "api_key_usage"})
            with ZipFile(BytesIO(response.data)) as archive:
                sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(expected_rows, len(sheet.findall(".//{*}row")))

    def test_filter_options_include_daily_only_and_unregistered_sources(self) -> None:
        self._log_request(
            "archived", "historical", 10, "2001:db8::1", datetime(2026, 4, 8), target_model_id="target/archived"
        )
        with self.connection_factory() as conn:
            conn.execute("DELETE FROM request_logs WHERE request_model = 'archived'")
            conn.commit()
        response = self.client.get("/api/statistics/filter-options")
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "users": ["alice", "bob", "carol"],
                "models": ["archived", "model-a", "model-b", "model-c"],
                "target_models": ["target/archived"],
                "response_models": ["historical", "resp-a", "resp-b", "resp-c"],
                "ip_addresses": ["10.0.0.1", "10.0.0.2", "10.0.0.3", "2001:db8::1"],
            },
            response.get_json(),
        )

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
        api_key_id: int | None = None,
        target_model_id: str | None = None,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cache_usage_status: str | None = None,
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
            api_key_id=api_key_id,
            target_model_id=target_model_id,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_usage_status=cache_usage_status,
        )

    def _create_api_key(self, name: str, suffix: str) -> int:
        key_id = self.api_key_repository.create(
            name=name,
            api_key=f"sk-test-{suffix}",
            key_hash=f"hash-{suffix}",
            key_prefix="sk-test",
            key_suffix=suffix,
        )
        assert key_id is not None
        return key_id

    def test_all_usage_dimensions_report_filtered_outcomes_and_export(self) -> None:
        key_id = self._create_api_key("Rates", "rates")
        for outcome in ("success", "success", "error", "cancelled", None):
            self.log_repository.insert(
                request_model="rates",
                target_model_id="target/a",
                response_model="actual",
                total_tokens=10,
                prompt_tokens=8,
                completion_tokens=2,
                start_time=datetime(2026, 4, 10),
                ip_address="10.0.0.1",
                api_key_id=key_id,
                outcome=outcome,
                upstream_attempts=[{"outcome": outcome, "target_model_id": "target/a", "response_model": "actual"}]
                if outcome
                else None,
            )
        # 不同用户、模型、日期和流向不混入当前分组。
        for changes in (
            {"ip_address": "10.0.0.2"},
            {"request_model": "other"},
            {"start_time": datetime(2026, 5, 1)},
            {"target_model_id": "target/b"},
        ):
            self.log_repository.insert(
                **{
                    "request_model": "rates",
                    "target_model_id": "target/a",
                    "response_model": "actual",
                    "total_tokens": 10,
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "start_time": datetime(2026, 4, 10),
                    "ip_address": "10.0.0.1",
                    "api_key_id": key_id,
                    "outcome": "error",
                    "upstream_attempts": [
                        {
                            "outcome": "error",
                            "target_model_id": changes.get("target_model_id", "target/a"),
                            "response_model": "actual",
                        }
                    ],
                    **changes,
                }
            )
        filters = {**self.DATE_FILTER, "username": "alice", "request_model": "rates"}
        overview = self.client.get("/api/statistics/overview", query_string=filters).get_json()
        source_rows = self.client.get("/api/statistics", query_string=filters).get_json()
        for rows in (source_rows, overview["model_flows"]):
            self.assertEqual(2, len(rows))
            row = next(item for item in rows if item["target_model_id"] == "target/a")
            self.assertAlmostEqual(2 / 3, row["success_rate"])
            self.assertEqual(
                [2, 1, 1, 1],
                [row[name] for name in ("success_count", "error_count", "cancelled_count", "unknown_count")],
            )
        for rows in (
            overview["ips"],
            self.client.get("/api/statistics/user-usage-summary", query_string=filters).get_json(),
            self.client.get("/api/statistics/api-key-usage-summary", query_string=filters).get_json(),
        ):
            self.assertEqual(1, len(rows))
            self.assertEqual(0.5, rows[0]["success_rate"])
            self.assertEqual(6, rows[0]["request_count"])
        for direction, expected in (("asc", [0, 2 / 3]), ("desc", [2 / 3, 0])):
            rows = self.client.get(
                "/api/statistics", query_string={**filters, "sort_key": "success_rate", "sort_direction": direction}
            ).get_json()
            self.assertEqual(expected, [row["success_rate"] for row in rows])
        for view in ("stats", "model_usage", "ip_usage", "user_usage", "api_key_usage"):
            response = self.client.get("/api/statistics/export", query_string={**filters, "tab": view})
            self.assertEqual(200, response.status_code)
            with ZipFile(BytesIO(response.data)) as archive:
                sheet = archive.read("xl/worksheets/sheet1.xml").decode()
                self.assertIn("成功率", sheet)
                self.assertIn("未知调用结果", sheet)
                self.assertIn("66.67%" if view in {"stats", "model_usage"} else "50.00%", sheet)

    def test_usage_outcomes_preserve_daily_only_and_log_only_records(self) -> None:
        self.log_repository.insert(
            request_model="observed",
            response_model="actual",
            total_tokens=10,
            prompt_tokens=8,
            completion_tokens=2,
            start_time=datetime(2026, 4, 10),
            ip_address="10.0.0.1",
            outcome="error",
        )
        with self.connection_factory() as conn:
            conn.execute("DELETE FROM request_logs WHERE request_model != 'observed'")
            conn.execute("DELETE FROM daily_request_stats WHERE request_model = 'observed'")
        historical = self.client.get(
            "/api/statistics", query_string={**self.DATE_FILTER, "request_model": "model-a"}
        ).get_json()
        self.assertTrue(
            all(row["success_rate"] is None and row["unknown_count"] == row["request_count"] for row in historical)
        )
        filters = {**self.DATE_FILTER, "request_model": "observed"}
        overview = self.client.get("/api/statistics/overview", query_string=filters).get_json()
        for rows in (
            overview["model_flows"],
            overview["ips"],
            self.client.get("/api/statistics", query_string=filters).get_json(),
            self.client.get("/api/statistics/user-usage-summary", query_string=filters).get_json(),
        ):
            self.assertEqual(1, len(rows))
            self.assertEqual(0, rows[0]["success_rate"])
            self.assertEqual(1, rows[0]["error_count"])
            self.assertEqual(0, rows[0]["total_tokens"])

    def test_same_name_mapping_attempts_join_usage_without_empty_rows(self) -> None:
        base = {
            "request_model": "same-model",
            "target_model_id": "same-model",
            "response_model": "same-model",
            "total_tokens": 10,
            "prompt_tokens": 8,
            "completion_tokens": 2,
            "start_time": datetime(2026, 4, 10),
            "ip_address": "10.0.0.1",
            "outcome": "success",
        }
        # 兼容已保存的省略目标、当前完整目标与未采集调用明细三种记录。
        for target in (None, "same-model"):
            self.log_repository.insert(
                **base,
                upstream_attempts=[{"target_model_id": target, "response_model": "same-model", "outcome": "success"}],
            )
        self.log_repository.insert(**base)
        filters = {**self.DATE_FILTER, "request_model": "same-model"}
        overview = self.client.get("/api/statistics/overview", query_string=filters).get_json()
        for rows in (
            self.client.get("/api/statistics", query_string=filters).get_json(),
            overview["model_flows"],
        ):
            self.assertEqual(1, len(rows))
            row = rows[0]
            self.assertEqual("same-model", row["target_model_id"])
            self.assertEqual(3, row["request_count"])
            self.assertEqual(30, row["total_tokens"])
            self.assertEqual(2, row["upstream_success_count"])
            self.assertEqual(1, row["upstream_unknown_count"])
            self.assertEqual(1, row["upstream_success_rate"])

        # 同名模型的直接调用保持两段流向，不与三段映射流向合并。
        self.log_repository.insert(
            **{**base, "target_model_id": None},
            upstream_attempts=[{"target_model_id": None, "response_model": "same-model", "outcome": "success"}],
        )
        rows = self.client.get("/api/statistics", query_string=filters).get_json()
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row["request_count"] > 0 for row in rows))
        self.assertEqual(4, sum(row["request_count"] for row in rows))
        self.assertEqual(3, sum(row["upstream_success_count"] for row in rows))

    def test_upstream_retry_rate_excludes_internal_and_transport_failures(self) -> None:
        key_id = self._create_api_key("Attempts", "attempts")
        base = {
            "request_model": "retried",
            "target_model_id": "target/b",
            "response_model": "actual",
            "total_tokens": 10,
            "prompt_tokens": 8,
            "completion_tokens": 2,
            "start_time": datetime(2026, 4, 10),
            "ip_address": "10.0.0.1",
            "api_key_id": key_id,
        }
        self.log_repository.insert(
            **base,
            outcome="success",
            upstream_attempts=[
                {"target_model_id": "target/a", "outcome": "error", "http_status": 503},
                {"target_model_id": "target/b", "response_model": "actual", "outcome": "success", "http_status": 200},
            ],
        )
        self.log_repository.insert(**base, outcome="error", error_type="server_error", upstream_attempts=[])
        self.log_repository.insert(
            **base, outcome="error", upstream_attempts=[{"target_model_id": "target/b", "outcome": "exception"}]
        )
        filters = {**self.DATE_FILTER, "request_model": "retried"}
        overview = self.client.get("/api/statistics/overview", query_string=filters).get_json()
        # 无响应模型的异常与最终用量分行，不应被误认为缺失历史观测。
        self.assertTrue(all(row["upstream_unknown_count"] == 0 for row in overview["model_flows"]))
        for row in [
            overview["totals"],
            overview["models"][0],
            overview["ips"][0],
            *self.client.get("/api/statistics/user-usage-summary", query_string=filters).get_json(),
            *self.client.get("/api/statistics/api-key-usage-summary", query_string=filters).get_json(),
        ]:
            self.assertEqual(3, row["request_count"])
            self.assertEqual(0.5, row["upstream_success_rate"])
            self.assertEqual(
                [1, 1, 1, 0],
                [
                    row[key]
                    for key in (
                        "upstream_success_count",
                        "upstream_error_count",
                        "upstream_exception_count",
                        "upstream_unknown_count",
                    )
                ],
            )
        failed = next(row for row in overview["model_flows"] if row["target_model_id"] == "target/a")
        self.assertEqual(0, failed["upstream_success_rate"])
        self.assertEqual(0, failed["total_tokens"])
        day = next(row for row in overview["series"] if row["date"] == "2026-04-10")
        self.assertEqual(0.5, day["upstream_success_rate"])
        for direction, expected in (("asc", [0, 1, None]), ("desc", [1, 0, None])):
            rows = self.client.get(
                "/api/statistics",
                query_string={**filters, "sort_key": "upstream_success_rate", "sort_direction": direction},
            ).get_json()
            self.assertEqual(expected, [row["upstream_success_rate"] for row in rows])
        historical = self.client.get(
            "/api/statistics/overview", query_string={**self.DATE_FILTER, "request_model": "model-a"}
        ).get_json()["totals"]
        self.assertIsNone(historical["upstream_success_rate"])
        self.assertEqual(2, historical["upstream_unknown_count"])
        exported = self.log_repository.export_request_logs("2026-04-10", "2026-04-10", request_model="retried")
        with self.connection_factory() as conn:
            conn.execute("DELETE FROM request_logs WHERE request_model = 'retried'")
        self.log_repository.import_request_logs(exported)
        reloaded = self.client.get("/api/statistics/overview", query_string=filters).get_json()["totals"]
        self.assertEqual(0.5, reloaded["upstream_success_rate"])
        self.assertEqual(1, reloaded["upstream_exception_count"])

    def test_five_minute_trends_use_request_times_filters_and_weighted_rates(self) -> None:
        base = {
            "request_model": "trend-mapped",
            "target_model_id": "target/b",
            "response_model": "actual",
            "total_tokens": 110,
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "ip_address": "10.0.0.1",
            "cache_read_input_tokens": 80,
            "cache_usage_status": "known",
            "outcome": "success",
            "start_time": datetime(2026, 4, 8, 9, 4, 59, 999999),
        }
        self.log_repository.insert(
            **base,
            upstream_attempts=[
                {"target_model_id": "target/a", "outcome": "error"},
                {"target_model_id": "target/b", "response_model": "actual", "outcome": "success"},
            ],
        )
        self.log_repository.insert(
            **{
                **base,
                "start_time": datetime(2026, 4, 8, 9),
                "total_tokens": 910,
                "prompt_tokens": 900,
                "cache_read_input_tokens": 90,
                "outcome": "error",
            },
            upstream_attempts=[{"target_model_id": "target/b", "outcome": "error"}],
        )
        self.log_repository.insert(**{**base, "start_time": datetime(2026, 4, 8, 9, 5)})
        for excluded in (
            {"ip_address": "10.0.0.2"},
            {"request_model": "other-model"},
            {"target_model_id": "other-target"},
            {"response_model": "other-response"},
        ):
            self.log_repository.insert(**{**base, **excluded})
        response = self.client.get(
            "/api/statistics/overview",
            query_string={
                "start_date": "2026-04-08",
                "end_date": "2026-04-08",
                "interval": "5m",
                "username": "alice",
                "request_model": "trend-mapped",
                "target_model_id": "target/b",
                "response_model": "actual",
                "ip_address": "10.0.0.1",
            },
        )
        self.assertEqual(200, response.status_code)
        result = response.get_json()
        self.assertEqual("5m", result["series_interval"])
        self.assertFalse(result["series_partial"])
        self.assertEqual(288, len(result["series"]))
        rows = {row["date"]: row for row in result["series"]}
        first = rows["2026-04-08 09:00:00"]
        self.assertEqual(2, first["request_count"])
        self.assertEqual(1020, first["total_tokens"])
        self.assertAlmostEqual(0.17, first["cache_hit_rate"])
        self.assertAlmostEqual(1 / 3, first["upstream_success_rate"])
        self.assertEqual(1, rows["2026-04-08 09:05:00"]["request_count"])
        self.assertEqual(0, rows["2026-04-08 09:10:00"]["total_tokens"])
        self.assertIsNone(rows["2026-04-08 09:10:00"]["upstream_success_rate"])
        for field in ("request_count", "total_tokens", "prompt_tokens", "completion_tokens"):
            self.assertEqual(result["totals"][field], sum(row.get(field, 0) for row in result["series"]))

    def test_hourly_trends_keep_midnight_boundaries_and_empty_hours(self) -> None:
        for moment in (datetime(2026, 4, 8, 23, 59, 59), datetime(2026, 4, 9)):
            self._log_request("hourly", "actual", 10, "10.0.0.1", moment)
        result = self.client.get(
            "/api/statistics/overview",
            query_string={
                "start_date": "2026-04-08",
                "end_date": "2026-04-09",
                "interval": "1h",
                "request_model": "hourly",
            },
        ).get_json()
        self.assertEqual(48, len(result["series"]))
        rows = {row["date"]: row for row in result["series"]}
        self.assertEqual(10, rows["2026-04-08 23:00:00"]["total_tokens"])
        self.assertEqual(10, rows["2026-04-09 00:00:00"]["total_tokens"])
        self.assertEqual(0, rows["2026-04-09 01:00:00"]["request_count"])
        self.assertIsNone(rows["2026-04-09 01:00:00"]["cache_hit_rate"])

    def test_trend_auto_interval_and_explicit_limits(self) -> None:
        for end, interval, count in (
            ("2026-04-08", "5m", 288),
            ("2026-04-14", "1h", 168),
            ("2026-04-15", "1d", 8),
            ("2026-04-30", "1d", 23),
        ):
            with self.subTest(end=end):
                response = self.client.get(
                    "/api/statistics/overview",
                    query_string={
                        "start_date": "2026-04-08",
                        "end_date": end,
                        "interval": "auto",
                    },
                )
                self.assertEqual(200, response.status_code)
                self.assertEqual(interval, response.get_json()["series_interval"])
                self.assertEqual(count, len(response.get_json()["series"]))
        response = self.client.get(
            "/api/statistics/overview",
            query_string={"start_date": "2026-04-08", "end_date": "2026-04-14", "interval": "1h"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(168, len(response.get_json()["series"]))
        for interval, end in (("invalid", "2026-04-08"), ("5m", "2026-04-09"), ("1h", "2026-04-15")):
            with self.subTest(interval=interval):
                response = self.client.get(
                    "/api/statistics/overview",
                    query_string={
                        "start_date": "2026-04-08",
                        "end_date": end,
                        "interval": interval,
                    },
                )
                self.assertEqual(400, response.status_code)

    def test_fine_trends_report_missing_history_and_keep_daily_totals(self) -> None:
        with self.connection_factory() as conn:
            conn.execute("DELETE FROM request_logs WHERE request_model = 'model-a'")
            conn.commit()
        filters = {"start_date": "2026-04-08", "end_date": "2026-04-08", "request_model": "model-a"}
        fine = self.client.get("/api/statistics/overview", query_string={**filters, "interval": "5m"}).get_json()
        day = self.client.get("/api/statistics/overview", query_string={**filters, "interval": "1d"}).get_json()
        self.assertTrue(fine["series_partial"])
        self.assertEqual(0, sum(row["total_tokens"] for row in fine["series"]))
        self.assertEqual(50, fine["totals"]["total_tokens"])
        self.assertFalse(day["series_partial"])
        self.assertEqual(50, day["series"][0]["total_tokens"])

    def test_fine_trends_do_not_extend_into_future_buckets(self) -> None:
        with patch("src.repositories.statistics_queries.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 4, 8, 9, 7)
            result = self.client.get(
                "/api/statistics/overview",
                query_string={
                    "start_date": "2026-04-08",
                    "end_date": "2026-04-08",
                    "interval": "5m",
                },
            ).get_json()
        self.assertEqual("2026-04-08 09:05:00", result["series"][-1]["date"])
        self.assertEqual(110, len(result["series"]))

    def test_overview_filters_and_performance_exports(self) -> None:
        self.log_service.log_request(
            request_model="model-a",
            response_model="resp-a",
            total_tokens=100,
            prompt_tokens=80,
            completion_tokens=20,
            ip_address="10.0.0.1",
            start_time=datetime(2026, 4, 8, 13),
            outcome="success",
            http_status=200,
            duration_ms=3500,
            first_token_latency_ms=1200,
            tps=12.5,
            cache_read_input_tokens=40,
            cache_usage_status="known",
            is_stream=True,
        )
        filters = {**self.DATE_FILTER, "username": "alice", "request_model": "model-a"}
        response = self.client.get("/api/statistics/overview", query_string=filters)
        self.assertEqual(200, response.status_code)
        result = response.get_json()
        self.assertEqual(2, result["totals"]["request_count"])
        self.assertEqual(1, result["totals"]["unknown_count"])
        self.assertEqual(1, result["totals"]["success_rate"])
        self.assertEqual(0.5, result["totals"]["cache_hit_rate"])
        self.assertEqual(1200, result["totals"]["first_token_latency_ms"])
        self.assertEqual(30, len(result["series"]))
        for view, sheet in (
            ("models", "模型表现"),
            ("model_usage", "请求模型用量"),
            ("ip_usage", "IP 用量"),
            ("logs", "请求明细"),
        ):
            exported = self.client.get("/api/statistics/export", query_string={**filters, "tab": view})
            self.assertEqual(200, exported.status_code)
            with ZipFile(BytesIO(exported.data)) as archive:
                self.assertIn(sheet, archive.read("xl/workbook.xml").decode())
                values = archive.read("xl/worksheets/sheet1.xml").decode()
                if view in {"models", "logs"}:
                    self.assertIn("1200", values)
                    self.assertIn("5.71", values)
                elif view == "model_usage":
                    self.assertIn("model-a", values)
                    self.assertNotIn("model-b", values)
                    self.assertIn("缓存读取 Token", values)
                    self.assertIn('<c r="D2"><v>85</v></c>', values)
                    self.assertIn('<c r="F2"><v>110</v></c>', values)
        self.assertEqual(400, self.client.get("/api/statistics/overview").status_code)
        self.assertEqual(
            400,
            self.client.get(
                "/api/statistics/overview",
                query_string={
                    "start_date": "2025-01-01",
                    "end_date": "2026-01-02",
                },
            ).status_code,
        )

    def test_model_usage_export_preserves_distinct_flows(self) -> None:
        for target, response in (("target/a", "response-a"), ("target/b", "response-a"), ("target/b", "response-b")):
            self.log_service.log_request(
                request_model="flow-model",
                target_model_id=target,
                response_model=response,
                prompt_tokens=80,
                completion_tokens=20,
                total_tokens=100,
                ip_address="10.0.0.1",
                start_time=datetime(2026, 4, 8, 13),
            )
        exported = self.client.get(
            "/api/statistics/export",
            query_string={
                **self.DATE_FILTER,
                "tab": "model_usage",
                "request_model": "flow-model",
                "sort_key": "target_model_id",
                "sort_direction": "desc",
            },
        )
        self.assertEqual(200, exported.status_code)
        with ZipFile(BytesIO(exported.data)) as archive:
            sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows = sheet.findall("s:sheetData/s:row", ns)
        self.assertEqual(4, len(rows))
        self.assertEqual(
            [
                "target/b",
                "target/b",
                "target/a",
            ],
            [row.findall("s:c", ns)[1].find("s:is/s:t", ns).text for row in rows[1:]],
        )
        self.assertTrue(all(row.find("s:c/s:is/s:t", ns).text == "flow-model" for row in rows[1:]))
        self.assertCountEqual(
            ["response-a", "response-a", "response-b"],
            [row.findall("s:c", ns)[2].find("s:is/s:t", ns).text for row in rows[1:]],
        )

    def test_response_model_usage_merges_sources_and_requests_with_weighted_rates(self) -> None:
        for request_model, ip, target, response_model, prompt, cached in (
            ("response-request-a", "10.0.0.1", "target/a", "actual", 80, 40),
            ("response-request-b", "10.0.0.2", "target/b", "actual", 160, 120),
            ("response-request-a", "10.0.0.2", "target/c", "other", 40, 0),
            ("response-request-a", "10.0.0.1", "target/a", None, 0, 0),
        ):
            attempt = {
                "target_model_id": target,
                "response_model": response_model,
                "outcome": "success" if response_model else "exception",
            }
            attempts = [attempt]
            if prompt == 80:
                attempts.insert(0, {**attempt, "outcome": "error"})
            self.log_repository.insert(
                request_model=request_model,
                target_model_id=target,
                response_model=response_model,
                prompt_tokens=prompt,
                completion_tokens=prompt // 4,
                total_tokens=prompt * 5 // 4,
                cache_read_input_tokens=cached,
                cache_usage_status="known" if prompt else "unknown",
                ip_address=ip,
                start_time=datetime(2026, 4, 10),
                upstream_attempts=attempts,
                outcome="success" if response_model else "error",
            )
        filters = {**self.DATE_FILTER, "request_model": ["response-request-a", "response-request-b"]}
        overview = self.client.get("/api/statistics/overview", query_string=filters).get_json()
        rows = {row["response_model"]: row for row in overview["response_models"]}
        self.assertEqual({"actual", "other", None}, set(rows))
        self.assertEqual(2, rows["actual"]["request_count"])
        self.assertEqual(300, rows["actual"]["total_tokens"])
        self.assertEqual(160, rows["actual"]["cache_read_input_tokens"])
        self.assertAlmostEqual(2 / 3, rows["actual"]["cache_hit_rate"])
        self.assertAlmostEqual(2 / 3, rows["actual"]["upstream_success_rate"])
        self.assertEqual(1, rows[None]["request_count"])
        self.assertEqual(1, rows[None]["upstream_exception_count"])
        self.assertIsNone(rows[None]["upstream_success_rate"])
        self.assertEqual(overview["totals"]["total_tokens"], sum(row["total_tokens"] for row in rows.values()))
        filtered = self.client.get(
            "/api/statistics/overview",
            query_string={**filters, "username": "bob", "request_model": "response-request-b"},
        ).get_json()["response_models"]
        self.assertEqual(1, len(filtered))
        self.assertEqual(200, filtered[0]["total_tokens"])
        exported = self.client.get(
            "/api/statistics/export",
            query_string={
                **filters,
                "tab": "response_model_usage",
                "sort_key": "total_tokens",
                "sort_direction": "desc",
            },
        )
        self.assertEqual(200, exported.status_code)
        with ZipFile(BytesIO(exported.data)) as archive:
            sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheet_rows = sheet.findall("s:sheetData/s:row", ns)
        self.assertEqual(4, len(sheet_rows))
        self.assertEqual(
            ["响应模型", "actual", "other", "未知"], [row.find("s:c/s:is/s:t", ns).text for row in sheet_rows]
        )

    def test_mapping_filter_applies_to_all_views_and_exports_and_keeps_retries(self) -> None:
        key_id = self._create_api_key("mapping-filter", "abcd")
        for index, target in enumerate(("target/a", "target/b", "target/c", None), start=1):
            attempts = [{"outcome": "success", "target_model_id": target, "response_model": "actual"}]
            if index == 1:
                attempts.insert(0, {"outcome": "error", "target_model_id": "target/c"})
            self.log_repository.insert(
                request_model="mapped",
                target_model_id=target,
                response_model="actual",
                total_tokens=index * 11,
                prompt_tokens=index * 8,
                completion_tokens=index * 3,
                start_time="2026-04-08 13:00:00",
                end_time="2026-04-08 13:00:01",
                ip_address="10.0.0.2" if index == 2 else "10.0.0.1",
                api_key_id=key_id,
                outcome="success",
                upstream_attempts=attempts,
            )
        query = {
            **self.DATE_FILTER,
            "username": ["alice", "bob"],
            "request_model": "mapped",
            "target_model_id": ["target/a", "target/b"],
            "response_model": "actual",
            "ip_address": ["10.0.0.1", "10.0.0.2"],
        }
        for path in ("/api/statistics", "/api/statistics/user-usage-summary", "/api/statistics/api-key-usage-summary"):
            with self.subTest(path=path):
                response = self.client.get(path, query_string=query)
                self.assertEqual(200, response.status_code)
                rows = response.get_json()
                self.assertEqual(33, sum(row["total_tokens"] for row in rows))
                self.assertEqual(2, sum(row["request_count"] for row in rows))
                self.assertEqual(1, sum(row["upstream_error_count"] for row in rows))
                self.assertEqual(2, sum(row["upstream_success_count"] for row in rows))
        overview = self.client.get("/api/statistics/overview", query_string=query).get_json()
        self.assertEqual(33, overview["totals"]["total_tokens"])
        self.assertAlmostEqual(2 / 3, overview["totals"]["upstream_success_rate"])
        for dimension in ("series", "models", "model_flows", "response_models", "ips"):
            self.assertEqual(33, sum(row["total_tokens"] for row in overview[dimension]))
            self.assertEqual(1, sum(row["upstream_error_count"] for row in overview[dimension]))
        logs = self.client.get("/api/request-logs", query_string={**query, "page_size": 1}).get_json()
        self.assertEqual(2, logs["total"])
        self.assertEqual(1, len(logs["logs"]))
        package = self.client.get("/api/statistics/daily-stats/export", query_string=query).get_json()
        for key in ("request_logs", "daily_request_stats"):
            self.assertEqual(2, len(package[key]))
            self.assertEqual(33, sum(row["total_tokens"] for row in package[key]))
            self.assertEqual({"target/a", "target/b"}, {row["target_model_id"] for row in package[key]})
        for tab in (
            "stats",
            "model_usage",
            "response_model_usage",
            "user_usage",
            "api_key_usage",
            "ip_usage",
            "models",
            "logs",
        ):
            with self.subTest(tab=tab):
                response = self.client.get("/api/statistics/export", query_string={**query, "tab": tab})
                self.assertEqual(200, response.status_code)
                with ZipFile(BytesIO(response.data)) as archive:
                    sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
                rows = sheet.findall(".//{*}row")
                headers = ["".join(cell.itertext()) for cell in rows[0]]
                token_column = headers.index("总 Token")
                self.assertEqual(33, sum(float(list(row)[token_column].find("{*}v").text) for row in rows[1:]))
                if tab in {"stats", "model_usage", "logs"}:
                    model_column = headers.index("请求模型")
                    self.assertEqual(["请求模型", "映射模型", "响应模型"], headers[model_column : model_column + 3])
                    self.assertTrue(all("".join(list(row)[model_column].itertext()) == "mapped" for row in rows[1:]))
        empty = self.client.get("/api/request-logs", query_string={**query, "target_model_id": "' OR 1=1 --"})
        self.assertEqual(0, empty.get_json()["total"])
        options = self.client.get("/api/statistics/filter-options").get_json()
        self.assertEqual(["target/a", "target/b", "target/c"], options["target_models"])

    def test_statistics_api_supports_multi_value_filters(self) -> None:
        self._log_request(
            "mapped-model",
            "actual-upstream-model",
            16,
            "10.0.0.1",
            datetime(2026, 4, 8, 13, 0, 0),
            target_model_id="target/provider-model",
        )

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

        mapped_response = self.client.get(
            "/api/statistics",
            query_string={
                **self.DATE_FILTER,
                "username": "alice",
                "request_model": "mapped-model",
            },
        )
        self.assertEqual(200, mapped_response.status_code)
        mapped_payload = mapped_response.get_json()
        self.assertEqual(1, len(mapped_payload))
        self.assertEqual("target/provider-model", mapped_payload[0]["target_model_id"])
        self.assertEqual("actual-upstream-model", mapped_payload[0]["response_model"])

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

    def test_api_key_usage_summary_groups_by_key_with_date_and_model_filters(self) -> None:
        client_a_id = self._create_api_key("客户端 A", "aaaa")
        client_b_id = self._create_api_key("客户端 B", "bbbb")
        self._log_request(
            "model-a",
            "resp-a",
            12,
            "10.0.0.1",
            datetime(2026, 4, 9, 9, 0, 0),
            api_key_id=client_a_id,
            cache_read_input_tokens=3,
            cache_creation_input_tokens=2,
            cache_usage_status="known",
        )
        self._log_request(
            "model-a",
            "resp-a",
            18,
            "10.0.0.1",
            datetime(2026, 4, 10, 9, 0, 0),
            api_key_id=client_a_id,
            cache_read_input_tokens=6,
            cache_creation_input_tokens=4,
            cache_usage_status="known",
        )
        self._log_request(
            "model-b",
            "resp-b",
            50,
            "10.0.0.2",
            datetime(2026, 4, 10, 10, 0, 0),
            api_key_id=client_b_id,
        )

        response = self.client.get(
            "/api/statistics/api-key-usage-summary",
            query_string={
                **self.DATE_FILTER,
                "username": "alice",
                "request_model": "model-a",
            },
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(1, len(payload))
        self.assertEqual("客户端 A", payload[0]["key_name"])
        self.assertEqual("sk-test...aaaa", payload[0]["key_preview"])
        self.assertEqual("enabled", payload[0]["status"])
        self.assertEqual(2, payload[0]["request_count"])
        self.assertEqual(30, payload[0]["total_tokens"])
        self.assertEqual(9, payload[0]["cache_read_input_tokens"])
        self.assertEqual(6, payload[0]["cache_creation_input_tokens"])
        self.assertEqual(15, payload[0]["cache_known_prompt_tokens"])
        self.assertEqual(0.6, payload[0]["cache_hit_rate"])
        self.assertEqual("known", payload[0]["cache_usage_status"])
        self.assertNotIn("last_used_at", payload[0])

    def test_request_logs_api_supports_multi_value_filters(self) -> None:
        self._log_request(
            "mapped-model",
            "actual-upstream-model",
            16,
            "10.0.0.1",
            datetime(2026, 4, 8, 13, 0, 0),
            target_model_id="target/provider-model",
        )

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

        mapped_response = self.client.get(
            "/api/request-logs",
            query_string={
                "page": "1",
                "page_size": "50",
                **self.DATE_FILTER,
                "username": "alice",
                "request_model": "mapped-model",
            },
        )
        self.assertEqual(200, mapped_response.status_code)
        mapped_payload = mapped_response.get_json()
        self.assertEqual("target/provider-model", mapped_payload["logs"][0]["target_model_id"])
        self.assertEqual("actual-upstream-model", mapped_payload["logs"][0]["response_model"])

    def test_statistics_export_logs_returns_full_xlsx_without_pagination(self) -> None:
        self._log_request(
            "mapped-model",
            "actual-upstream-model",
            16,
            "10.0.0.1",
            datetime(2026, 4, 8, 13, 0, 0),
            target_model_id="target/provider-model",
        )

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
        self.assertIn("响应模型", sheet_xml)
        self.assertIn("Token 状态", sheet_xml)
        self.assertIn("完整", sheet_xml)
        self.assertIn("model-a", sheet_xml)
        self.assertIn("model-b", sheet_xml)
        self.assertIn("model-c", sheet_xml)
        self.assertIn("映射模型", sheet_xml)
        self.assertIn("mapped-model", sheet_xml)
        self.assertIn("target/provider-model", sheet_xml)
        self.assertNotIn("mapped-model -&gt;", sheet_xml)

    def test_statistics_export_summary_returns_xlsx(self) -> None:
        self._log_request(
            "mapped-model",
            "actual-upstream-model",
            16,
            "10.0.0.1",
            datetime(2026, 4, 8, 13, 0, 0),
            target_model_id="target/provider-model",
        )

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

            self.assertIn("来源与模型", workbook_xml)
        self.assertIn("请求模型", sheet_xml)
        self.assertIn("响应模型", sheet_xml)
        self.assertIn("Token 状态", sheet_xml)
        self.assertIn("缓存读取 Token", sheet_xml)
        self.assertIn("缓存写入 Token", sheet_xml)
        self.assertIn("缓存命中率", sheet_xml)
        self.assertIn("resp-a", sheet_xml)
        self.assertIn("映射模型", sheet_xml)
        self.assertIn("mapped-model", sheet_xml)
        self.assertIn("target/provider-model", sheet_xml)
        self.assertNotIn("mapped-model -&gt;", sheet_xml)

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

    def test_statistics_export_api_key_usage_returns_xlsx(self) -> None:
        client_a_id = self._create_api_key("客户端 A", "aaaa")
        self._log_request(
            "model-a",
            "resp-a",
            12,
            "10.0.0.1",
            datetime(2026, 4, 9, 9, 0, 0),
            api_key_id=client_a_id,
            cache_read_input_tokens=3,
            cache_creation_input_tokens=2,
            cache_usage_status="known",
        )

        response = self.client.get(
            "/api/statistics/export",
            query_string={
                "tab": "api_key_usage",
                **self.DATE_FILTER,
            },
        )

        self.assertEqual(200, response.status_code)
        self.assertIn("api-key-usage-", response.headers["Content-Disposition"])
        with ZipFile(BytesIO(response.data)) as archive:
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
            sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

        self.assertIn("KEY 用量", workbook_xml)
        self.assertIn("KEY 名称", sheet_xml)
        self.assertIn("客户端 A", sheet_xml)
        self.assertIn("sk-test...aaaa", sheet_xml)
        self.assertIn("缓存读取 Token", sheet_xml)
        self.assertIn("缓存写入 Token", sheet_xml)
        self.assertIn("缓存命中率", sheet_xml)
        self.assertIn("50.00%", sheet_xml)
        self.assertNotIn("最近使用时间", sheet_xml)

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
        self.assertEqual(6, payload["version"])
        self.assertEqual("llm_proxy.statistics", payload["kind"])
        self.assertEqual("known", payload["request_logs"][0]["usage_status"])
        self.assertEqual("known", payload["daily_request_stats"][0]["usage_status"])
        self.assertEqual("unknown", payload["request_logs"][0]["cache_usage_status"])
        self.assertEqual("unknown", payload["daily_request_stats"][0]["cache_usage_status"])
        self.assertEqual(
            {("10.0.0.1", "model-a", None, "resp-a", 10)},
            {
                (
                    item["ip_address"],
                    item["request_model"],
                    item["target_model_id"],
                    item["response_model"],
                    item["total_tokens"],
                )
                for item in payload["request_logs"]
            },
        )
        self.assertEqual(
            {("2026-04-08", "10.0.0.1", "model-a", "")},
            {
                (item["stat_date"], item["ip_address"], item["request_model"], item["target_model_id"])
                for item in payload["daily_request_stats"]
            },
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

    def test_daily_stats_target_model_id_is_a_statistics_dimension(self) -> None:
        payload = {
            "version": 4,
            "kind": "llm_proxy.statistics",
            "daily_request_stats": [
                {
                    "stat_date": "2026-04-11",
                    "ip_address": "10.0.0.1",
                    "request_model": "mapped-model",
                    "target_model_id": "target-a",
                    "response_model": "actual-model",
                    "request_count": 1,
                    "total_tokens": 10,
                    "prompt_tokens": 4,
                    "completion_tokens": 6,
                },
                {
                    "stat_date": "2026-04-11",
                    "ip_address": "10.0.0.1",
                    "request_model": "mapped-model",
                    "target_model_id": "target-b",
                    "response_model": "actual-model",
                    "request_count": 1,
                    "total_tokens": 20,
                    "prompt_tokens": 8,
                    "completion_tokens": 12,
                },
            ],
        }
        response = self.client.post("/api/statistics/daily-stats/import", json=payload)

        self.assertEqual(201, response.status_code)
        stats_response = self.client.get(
            "/api/statistics",
            query_string={
                "start_date": "2026-04-11",
                "end_date": "2026-04-11",
                "username": "alice",
                "request_model": "mapped-model",
            },
        )
        self.assertEqual(200, stats_response.status_code)
        rows = stats_response.get_json()
        self.assertEqual(
            {("target-a", 10), ("target-b", 20)},
            {(row["target_model_id"], row["total_tokens"]) for row in rows},
        )

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

    def test_request_logs_import_distinguishes_target_model_id(self) -> None:
        base_log = {
            "api_key_id": None,
            "ip_address": "10.0.0.2",
            "request_model": "mapped-imported-log",
            "response_model": "actual-imported-model",
            "total_tokens": 18,
            "prompt_tokens": 8,
            "completion_tokens": 10,
            "start_time": "2026-04-12 09:00:00.000000",
            "end_time": "2026-04-12 09:00:01.000000",
            "created_at": "2026-04-12 09:00:01.000000",
        }

        response = self.client.post(
            "/api/statistics/daily-stats/import",
            json={
                "request_logs": [
                    {**base_log, "target_model_id": "target-a"},
                    {**base_log, "target_model_id": "target-b"},
                ]
            },
        )

        self.assertEqual(201, response.status_code)
        logs_response = self.client.get(
            "/api/request-logs",
            query_string={
                "page": "1",
                "page_size": "50",
                "start_date": "2026-04-12",
                "end_date": "2026-04-12",
                "request_model": "mapped-imported-log",
            },
        )
        self.assertEqual(200, logs_response.status_code)
        logs_payload = logs_response.get_json()
        self.assertEqual({"target-a", "target-b"}, {item["target_model_id"] for item in logs_payload["logs"]})

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
        self.db_path = Path(__file__).resolve().parents[1] / "tmp" / f"usage-status-migration-{uuid4().hex}.db"
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
        self.assertTrue(
            {"outcome", "http_status", "is_stream", "duration_ms", "first_token_latency_ms", "tps"} <= request_columns
        )
        with connection_factory() as conn:
            for row in conn.execute("SELECT outcome, duration_ms, first_token_latency_ms, tps FROM request_logs"):
                self.assertEqual((None, None, None, None), tuple(row))
        self.assertIn("usage_status", daily_columns)
        self.assertEqual({"known-log": "known", "unknown-log": "unknown"}, request_statuses)
        self.assertEqual({"known-stat": "known", "unknown-stat": "unknown"}, daily_statuses)

    def test_legacy_daily_stats_rebuilds_target_model_dimension(self) -> None:
        connection_factory = create_connection_factory(self.db_path)

        repository = LogRepository(connection_factory)
        UserRepository(connection_factory)
        repository.insert(
            request_model="mapped-model",
            target_model_id="target-a",
            response_model="actual-model",
            total_tokens=10,
            prompt_tokens=4,
            completion_tokens=6,
            start_time=datetime(2026, 4, 2, 9, 0, 0),
            end_time=datetime(2026, 4, 2, 9, 0, 1),
            ip_address="10.0.0.3",
        )
        repository.insert(
            request_model="mapped-model",
            target_model_id="target-b",
            response_model="actual-model",
            total_tokens=20,
            prompt_tokens=8,
            completion_tokens=12,
            start_time=datetime(2026, 4, 2, 9, 1, 0),
            end_time=datetime(2026, 4, 2, 9, 1, 1),
            ip_address="10.0.0.3",
        )

        with connection_factory() as conn:
            unique_columns = [
                [
                    row["name"]
                    for row in conn.execute(
                        f"PRAGMA index_info({LogRepository._quote_identifier(index_row['name'])})"
                    ).fetchall()
                ]
                for index_row in conn.execute("PRAGMA index_list(daily_request_stats)").fetchall()
                if index_row["unique"]
            ]
        stats = repository.get_statistics(
            start_date="2026-04-02",
            end_date="2026-04-02",
            request_model="mapped-model",
        )

        self.assertIn(
            ["stat_date", "ip_address", "request_model", "target_model_id", "response_model"],
            unique_columns,
        )
        self.assertEqual(
            {("target-a", 10), ("target-b", 20)},
            {(row["target_model_id"], row["total_tokens"]) for row in stats},
        )


if __name__ == "__main__":
    unittest.main()
