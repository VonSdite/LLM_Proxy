"""请求观测的真实响应生命周期、统计口径与迁移回归。"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, Response
from src.repositories import LogRepository, UserRepository
from src.repositories.statistics_queries import REQUEST_TPS_REASON_SQL, REQUEST_TPS_SQL
from src.services.request_observation import RequestObservation
from src.utils.database import create_connection_factory
from src.utils.request_metrics import output_tps


def event(payload):
    return f"data: {json.dumps(payload)}\n\n".encode()


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        self.clock = 100.0
        self.timer = patch("src.services.request_observation.time.perf_counter", lambda: self.clock)
        self.timer.start()
        self.addCleanup(self.timer.stop)
        self.observation = RequestObservation(
            lambda **row: self.rows.append(row),
            started_at=100,
            start_time=datetime(2026, 11, 13),
            request_model="public/model",
            ip_address="127.0.0.1",
        )
        self.app = Flask(__name__)

    def test_stream_ttft_excludes_metadata_tps_includes_usage_tail_and_records_once(self):
        def chunks():
            self.clock = 101
            yield event({"choices": [{"delta": {"role": "assistant"}}]})
            self.clock = 102
            yield event({"choices": [{"delta": {"content": "hello"}}]})
            self.clock = 104
            yield event({"choices": [{"delta": {"content": " world"}}]})
            self.clock = 110
            self.observation.complete({"usage_status": "known", "completion_tokens": 11})
            yield b"data: [DONE]\n\n"

        response = self.observation.wrap(Response(chunks(), content_type="text/event-stream"), "openai_chat")
        body = list(response.response)
        response.close()
        self.assertEqual(4, len(body))
        self.assertEqual(1, len(self.rows))
        self.assertEqual("success", self.rows[0]["outcome"])
        self.assertEqual(2000, self.rows[0]["first_token_latency_ms"])
        self.assertEqual(1.1, self.rows[0]["tps"])
        self.assertIsNone(self.rows[0]["tps_unavailable_reason"])
        self.assertEqual(10000, self.rows[0]["duration_ms"])

    def test_protocol_content_and_hidden_reasoning(self):
        for source, first, last, hidden in (
            (
                "claude_chat",
                {"type": "content_block_delta", "delta": {"thinking": "think"}},
                {"type": "content_block_delta", "delta": {"text": "answer"}},
                False,
            ),
            (
                "openai_responses",
                {"type": "response.output_text.delta", "delta": "hello"},
                {"type": "response.output_text.delta", "delta": "there"},
                False,
            ),
            (
                "openai_chat",
                {"choices": [{"delta": {"tool_calls": [{"function": {"arguments": "{"}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"function": {"arguments": "}"}}]}}]},
                False,
            ),
            (
                "openai_responses",
                {"type": "response.reasoning_summary_text.delta", "delta": "think"},
                {"type": "response.output_text.delta", "delta": "answer"},
                True,
            ),
        ):
            with self.subTest(source=source, hidden=hidden):
                observer = RequestObservation(
                    lambda **row: self.rows.append(row), started_at=100, start_time=datetime.now()
                )
                observer.source_format = source
                observer.stream = True
                observer.observe_payload(first, 101)
                observer.observe_payload(last, 103)
                observer.complete({"completion_tokens": 5, "usage_status": "known"})
                self.clock = 105
                observer.finish("success")
                self.assertEqual(1000, self.rows[-1]["first_token_latency_ms"])
                self.assertEqual(1, self.rows[-1]["tps"])

    def test_stream_error_is_distinct(self):
        response = self.observation.wrap(
            Response(iter([event({"error": {"message": "failed"}})]), content_type="text/event-stream"), "openai_chat"
        )
        list(response.response)
        response.close()
        self.assertEqual("error", self.rows[-1]["outcome"])
        self.assertIsNone(self.rows[-1]["tps"])
        self.assertEqual("unsuccessful", self.rows[-1]["tps_unavailable_reason"])

    def test_custom_tool_stream_ttft_and_full_duration_tps(self):
        def chunks():
            self.clock = 101
            yield event({"type": "response.custom_tool_call_input.delta", "delta": ""})
            self.clock = 102
            yield event({"type": "response.custom_tool_call_input.delta", "delta": "*** Begin Patch\n"})
            self.clock = 104
            yield event({"type": "response.custom_tool_call_input.delta", "delta": "*** End Patch"})
            self.clock = 108
            yield event({"type": "response.custom_tool_call_input.done", "input": "*** Begin Patch\n*** End Patch"})
            self.clock = 110
            yield event(
                {
                    "type": "response.completed",
                    "response": {"status": "completed", "usage": {"input_tokens": 20, "output_tokens": 11}},
                }
            )

        response = self.observation.wrap(Response(chunks(), content_type="text/event-stream"), "openai_responses")
        body = list(response.response)
        response.close()
        self.assertEqual(5, len(body))
        self.assertEqual(1, len(self.rows))
        self.assertEqual("success", self.rows[0]["outcome"])
        self.assertEqual(2000, self.rows[0]["first_token_latency_ms"])
        self.assertEqual(1.1, self.rows[0]["tps"])
        self.assertEqual(10000, self.rows[0]["duration_ms"])

    def test_translated_responses_reasoning_is_included_once_in_total_output(self):
        self.observation.source_format = "openai_chat"
        self.observation.stream = True
        self.observation.observe_payload({"choices": [{"delta": {"reasoning_content": "summary"}}]}, 101)
        self.observation.observe_payload({"choices": [{"delta": {"content": "answer"}}]}, 103)
        self.observation.complete(
            {
                "upstream_source_format": "openai_responses",
                "completion_tokens": 100,
                "reasoning_tokens": 90,
                "usage_status": "known",
            }
        )
        self.clock = 110
        self.observation.finish("success")
        self.assertEqual(1000, self.rows[-1]["first_token_latency_ms"])
        self.assertEqual(10, self.rows[-1]["tps"])
        self.assertIsNone(self.rows[-1]["tps_unavailable_reason"])

    def test_tps_supports_single_chunk_no_content_and_one_token(self):
        for tokens, timestamps in ((20, [101, 101]), (1, [101, 103]), (20, [])):
            with self.subTest(tokens=tokens, timestamps=timestamps):
                observer = RequestObservation(
                    lambda **row: self.rows.append(row), started_at=100, start_time=datetime.now()
                )
                observer.stream = True
                observer.source_format = "openai_responses"
                for timestamp in timestamps:
                    observer.observe_payload({"type": "response.output_text.delta", "delta": "text"}, timestamp)
                observer.complete({"usage_status": "known", "completion_tokens": tokens})
                self.clock = 105
                observer.finish("success")
                self.assertEqual(tokens / 5, self.rows[-1]["tps"])
                self.assertIsNone(self.rows[-1]["tps_unavailable_reason"])

    def test_tps_missing_reasons_and_nonstream_use_same_formula(self):
        for status, tokens, end, outcome, expected, reason in (
            ("unknown", 20, 105, "success", None, "missing_usage"),
            ("known", 0, 105, "success", None, "no_output"),
            ("known", 20, 100, "success", None, "invalid_duration"),
            ("known", 20, 105, "error", None, "unsuccessful"),
            ("known", 20, 105, "cancelled", None, "unsuccessful"),
            ("known", 20, 105, "success", 4, None),
            ("partial", 20, 105, "success", 4, None),
        ):
            with self.subTest(status=status, tokens=tokens, end=end, outcome=outcome):
                observer = RequestObservation(
                    lambda **row: self.rows.append(row), started_at=100, start_time=datetime.now()
                )
                observer.complete({"usage_status": status, "completion_tokens": tokens})
                self.clock = end
                observer.finish(outcome)
                self.assertEqual(expected, self.rows[-1]["tps"])
                self.assertEqual(reason, self.rows[-1]["tps_unavailable_reason"])

    def test_close_before_iteration_closes_original_once(self):
        source = Mock()
        source.__iter__ = lambda _: iter([b"data: [DONE]\n\n"])
        response = self.observation.wrap(Response(source, content_type="text/event-stream"), "openai_chat")
        response.close()
        response.close()
        source.close.assert_called_once()
        self.assertEqual(1, len(self.rows))
        self.assertEqual("cancelled", self.rows[-1]["outcome"])

    def test_split_utf8_and_malformed_chunks_leave_bytes_unchanged(self):
        valid = 'data: {"choices":[{"delta":{"content":"你好"}}]}\r\n\r\n'.encode()
        split = valid.index("你".encode()) + 1
        chunks = [valid[:split], valid[split:], event({"choices": "malformed"}), b"data: [DONE]\n\n"]
        response = self.observation.wrap(Response(iter(chunks), content_type="text/event-stream"), "openai_chat")
        self.clock = 102
        self.assertEqual(b"".join(chunks), response.get_data())
        response.close()
        self.assertEqual(1, len(self.rows))
        self.assertEqual(2000, self.rows[-1]["first_token_latency_ms"])

    def test_cancel_and_nonstream_are_distinct(self):
        observer = RequestObservation(lambda **row: self.rows.append(row), started_at=100, start_time=datetime.now())
        response = observer.wrap(Response(iter([b"data: [DONE]\n\n"]), content_type="text/event-stream"), "openai_chat")
        response.close()
        self.assertEqual("cancelled", self.rows[-1]["outcome"])
        with self.app.app_context():
            observer = RequestObservation(
                lambda **row: self.rows.append(row), started_at=100, start_time=datetime.now()
            )
            observer.wrap(Response('{"ok":true}', content_type="application/json"), "openai_chat")
        self.assertEqual("success", self.rows[-1]["outcome"])
        self.assertIsNone(self.rows[-1]["first_token_latency_ms"])
        self.assertIsNone(self.rows[-1]["tps"])


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.factory = create_connection_factory(Path(self.temp.name) / "test.db")
        self.repo = LogRepository(self.factory)
        UserRepository(self.factory).create("alice", "10.0.0.1")

    def log(self, **changes):
        values = dict(
            request_model="public",
            response_model="actual",
            total_tokens=110,
            prompt_tokens=100,
            completion_tokens=10,
            start_time=datetime(2026, 11, 13),
            ip_address="10.0.0.1",
        )
        values.update(changes)
        self.repo.insert(**values)

    def test_overview_separates_unknown_results_and_weights_cache_and_filters(self):
        self.log(
            outcome="success",
            first_token_latency_ms=1000,
            tps=20,
            duration_ms=500,
            cache_read_input_tokens=80,
            cache_usage_status="known",
        )
        self.log(outcome="error", prompt_tokens=900, cache_usage_status="known")
        self.log(outcome="cancelled")
        self.log()
        self.log(request_model="other", outcome="success")
        result = self.repo.get_overview("2026-11-12", "2026-11-13", ["alice"], ["public"])
        total = result["totals"]
        self.assertEqual(4, total["request_count"])
        self.assertEqual(0.5, total["success_rate"])
        self.assertEqual(0.08, total["cache_hit_rate"])
        self.assertEqual(0.5, total["cache_request_hit_rate"])
        self.assertEqual(1, total["unknown_count"])
        self.assertEqual(1, total["cancelled_count"])
        self.assertEqual(1000, total["first_token_latency_ms"])
        self.assertEqual(20, total["tps"])
        self.assertEqual(0, result["series"][0]["request_count"])
        self.assertEqual(["public"], [row["request_model"] for row in result["models"]])
        self.assertIsNone(self.repo.get_overview("2025-01-01", "2025-01-01")["totals"]["success_rate"])

    def test_observation_round_trip_and_old_rows_remain_unknown(self):
        self.log(
            outcome="success", http_status=200, duration_ms=1500, first_token_latency_ms=500, tps=10, is_stream=True
        )
        exported = self.repo.export_request_logs("2026-11-13", "2026-11-13")
        with self.factory() as conn:
            conn.execute("DELETE FROM request_logs")
        self.repo.import_request_logs(exported)
        self.assertEqual(1, self.repo.import_request_logs(exported)["skipped_count"])
        row = self.repo.get_logs()["logs"][0]
        self.assertEqual("success", row["outcome"])
        self.assertEqual(500, row["first_token_latency_ms"])
        self.assertEqual(6.67, row["tps"])
        self.log(request_model="legacy")
        LogRepository(self.factory)
        legacy = self.repo.get_overview("2026-11-13", "2026-11-13", request_model=["legacy"])["totals"]
        self.assertIsNone(legacy["success_rate"])
        self.assertIsNone(legacy["first_token_latency_ms"])

    def test_missing_tps_reason_survives_storage_and_export_import(self):
        self.log(
            outcome="success",
            is_stream=True,
            duration_ms=0,
            tps_unavailable_reason="invalid_duration",
            first_token_latency_ms=1000,
        )
        row = self.repo.get_logs()["logs"][0]
        self.assertEqual("invalid_duration", row["tps_unavailable_reason"])
        exported = self.repo.export_request_logs("2026-11-13", "2026-11-13")
        with self.factory() as conn:
            conn.execute("DELETE FROM request_logs")
        self.repo.import_request_logs(exported)
        row = self.repo.get_all_logs()[0]
        self.assertEqual("invalid_duration", row["tps_unavailable_reason"])
        self.assertIsNone(row["tps"])

    def test_sql_and_live_tps_agree_on_missing_and_partial_metrics(self):
        for tokens, duration, status, outcome in (
            (100, 2500, "known", "success"),
            (100, 2500, "partial", "success"),
            (1, 2500, "known", "success"),
            (100, 2500, "unknown", "success"),
            (0, 2500, "known", "success"),
            (None, 2500, "known", "success"),
            (100, 0, "known", "success"),
            (100, None, "known", "success"),
            (100, 2500, "known", "error"),
            (100, 2500, "known", "cancelled"),
            (100, 2500, "known", None),
        ):
            with self.subTest(tokens=tokens, duration=duration, status=status, outcome=outcome), self.factory() as conn:
                row = conn.execute(
                    f"SELECT {REQUEST_TPS_SQL}, {REQUEST_TPS_REASON_SQL} FROM "
                    "(SELECT ? AS completion_tokens, ? AS duration_ms, ? AS usage_status, ? AS outcome, "
                    "NULL AS start_time, NULL AS end_time) l",
                    (tokens, duration, status, outcome),
                ).fetchone()
                self.assertEqual(output_tps(tokens, duration, usage_status=status, outcome=outcome), tuple(row))

    def test_historical_tps_recomputed_for_rows_sort_overview_and_export(self):
        self.log(request_model="slow", outcome="success", duration_ms=2000, tps=999)
        self.log(request_model="fast", outcome="success", duration_ms=1000, tps_unavailable_reason="hidden_reasoning")
        self.log(request_model="legacy", outcome="success", end_time=datetime(2026, 11, 13, 0, 0, 4))
        rows = self.repo.get_logs(sort_key="tps", sort_direction="desc")["logs"]
        self.assertEqual(["fast", "slow", "legacy"], [row["request_model"] for row in rows])
        expected = {"fast": 10, "slow": 5, "legacy": 2.5}
        for collection in (rows, self.repo.get_all_logs(), self.repo.export_request_logs("2026-11-13", "2026-11-13")):
            self.assertEqual(expected, {row["request_model"]: row["tps"] for row in collection})
            self.assertTrue(all(row["tps_unavailable_reason"] is None for row in collection))
        total = self.repo.get_overview("2026-11-13", "2026-11-13")["totals"]
        self.assertAlmostEqual(17.5 / 3, total["tps"], places=2)
        self.assertEqual(3, total["tps_sample_count"])
        with self.factory() as conn:
            stored = conn.execute("SELECT tps FROM request_logs WHERE request_model = 'slow'").fetchone()
        self.assertEqual(999, stored[0])

    def test_model_flow_usage_separates_targets_and_responses_and_combines_sources(self):
        self.log(target_model_id="provider/a", cache_read_input_tokens=50, cache_usage_status="known")
        self.log(
            ip_address="10.0.0.2",
            target_model_id="provider/b",
            response_model="other-response",
            prompt_tokens=300,
            completion_tokens=30,
            total_tokens=330,
            cache_read_input_tokens=250,
            cache_usage_status="known",
        )
        self.log(request_model="other")
        result = self.repo.get_overview("2026-11-13", "2026-11-13", request_model=["public"])
        self.assertEqual(1, len(result["models"]))
        row = result["models"][0]
        self.assertEqual("public", row["request_model"])
        self.assertEqual(
            (400, 40, 440, 300, 2),
            tuple(
                row[key]
                for key in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "cache_read_input_tokens",
                    "request_count",
                )
            ),
        )
        self.assertEqual(0.75, row["cache_hit_rate"])
        self.assertEqual(2, len(result["model_flows"]))
        self.log(
            ip_address="10.0.0.3", target_model_id="provider/a", cache_read_input_tokens=10, cache_usage_status="known"
        )
        self.log(target_model_id="provider/a", response_model="different")
        flows = self.repo.get_overview("2026-11-13", "2026-11-13", request_model=["public"])["model_flows"]
        self.assertEqual(3, len(flows))
        by_flow = {(row["request_model"], row["target_model_id"], row["response_model"]): row for row in flows}
        combined = by_flow[("public", "provider/a", "actual")]
        self.assertEqual(2, combined["request_count"])
        self.assertEqual(220, combined["total_tokens"])
        self.assertEqual(0.3, combined["cache_hit_rate"])
        self.assertEqual(330, by_flow[("public", "provider/b", "other-response")]["total_tokens"])
        self.assertEqual(110, by_flow[("public", "provider/a", "different")]["total_tokens"])
        filtered = self.repo.get_overview("2026-11-13", "2026-11-13", ["alice"], ["public"])
        self.assertEqual(2, len(filtered["model_flows"]))
        self.assertTrue(all(row["request_count"] == 1 for row in filtered["model_flows"]))

    def test_daily_only_history_has_unknown_outcomes_and_duration_sort_uses_measured_clock(self):
        self.log(outcome="success", duration_ms=9000)
        self.log(request_model="faster", outcome="success", duration_ms=1000)
        rows = self.repo.get_logs(sort_key="duration", sort_direction="desc")["logs"]
        self.assertEqual([9000, 1000], [row["duration_ms"] for row in rows])
        with self.factory() as conn:
            conn.execute("DELETE FROM request_logs")
        totals = self.repo.get_overview("2026-11-13", "2026-11-13")["totals"]
        self.assertEqual(2, totals["unknown_count"])
        self.assertIsNone(totals["success_rate"])


if __name__ == "__main__":
    unittest.main()
