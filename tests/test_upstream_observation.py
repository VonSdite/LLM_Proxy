"""上游调用结果与客户端请求结果分别计数。"""

import json
import time
import unittest
from contextvars import copy_context
from datetime import datetime
from unittest.mock import Mock, patch

import requests
from flask import Response
from src.external import LLMProvider
from src.services.request_observation import RequestObservation
from src.services.upstream_observation import ObservedResponse, attempt_recorder, observe_response
from tests import test_proxy_pipeline as pipeline


def response(status=200, payload=None):
    item = requests.Response()
    item.status_code = status
    item.headers["Content-Type"] = "application/json"
    item._content = json.dumps(
        payload or {"model": "actual", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    ).encode()
    return item


class UpstreamObservationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        token = attempt_recorder.set(self.events.append)
        self.addCleanup(attempt_recorder.reset, token)

    def test_actual_request_model_is_available_without_changing_statistics_fields(self):
        capture = Mock()
        logs = []
        observation = RequestObservation(
            lambda **row: logs.append(row),
            started_at=time.perf_counter(),
            start_time=datetime.now(),
            request_model="public/model",
            capture=capture,
        )
        observation.record_attempt(
            {
                "target_model_id": "public/model",
                "actual_request_model": "upstream/model",
                "response_model": "actual-response",
                "outcome": "success",
            }
        )
        observation.finish("success")

        capture.observe_attempt.assert_called_once_with(
            {
                "target_model_id": "public/model",
                "actual_request_model": "upstream/model",
                "response_model": "actual-response",
                "outcome": "success",
            }
        )
        capture.finish.assert_called_once_with("success")
        self.assertNotIn("actual_request_model", logs[0]["upstream_attempts"][0])
        self.assertNotIn("upstream_protocol", logs[0]["upstream_attempts"][0])

    def test_nonstream_capture_reads_final_body_and_finishes_once(self):
        capture = Mock()
        observation = RequestObservation(
            Mock(),
            started_at=time.perf_counter(),
            start_time=datetime.now(),
            capture=capture,
        )
        response = observation.wrap(
            Response(
                '{"model":"downstream-default","choices":[]}',
                content_type="application/json",
            ),
            "openai_chat",
        )
        observation.finish("cancelled")

        capture.feed.assert_called_once_with(response.get_data())
        capture.finish.assert_called_once_with("success")

    def test_observe_response_forwards_actual_upstream_model(self):
        tracked = observe_response(
            lambda: response(),
            "gateway/model",
            actual_model="upstream/model",
            upstream_protocol="openai_responses",
        )
        tracked.json()

        self.assertEqual("upstream/model", self.events[0]["actual_request_model"])
        self.assertEqual("openai_responses", self.events[0]["upstream_protocol"])
        self.assertEqual("actual", self.events[0]["response_model"])

    def test_request_guard_rewrite_is_the_captured_upstream_model(self):
        app, service = pipeline.ProxyServicePipelineTests()._build_service()
        provider = LLMProvider(
            name="demo",
            api="https://example.test/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("original-model", "rewritten-model"),
            max_retries=1,
            hook=pipeline.RewriteRequestModelHook("rewritten-model"),
        )
        capture = Mock()
        observation = RequestObservation(
            Mock(),
            started_at=time.perf_counter(),
            start_time=datetime.now(),
            request_model="demo/original-model",
            capture=capture,
        )
        observation.activate()
        try:
            with (
                app.test_request_context(),
                patch.object(service, "_open_upstream_response", return_value=(response(), False, 200)),
            ):
                result, status, failure = service.proxy_request(
                    provider,
                    {"model": "demo/original-model", "messages": [{"role": "user", "content": "hi"}]},
                    {},
                    on_complete=observation.complete,
                )
                self.assertEqual(200, status)
                self.assertIsNone(failure)
                observation.wrap(result, "openai_chat")
        finally:
            observation.deactivate()

        attempt = capture.observe_attempt.call_args.args[0]
        self.assertEqual("rewritten-model", attempt["actual_request_model"])
        self.assertEqual("openai_chat", attempt["upstream_protocol"])
        self.assertEqual("actual", attempt["response_model"])

    def test_same_name_mapping_keeps_target_while_direct_request_omits_it(self):
        for mapped in (False, True):
            with self.subTest(mapped=mapped):
                logs = []
                observation = RequestObservation(
                    lambda **row: logs.append(row),
                    started_at=time.perf_counter(),
                    start_time=datetime.now(),
                    request_model="same-model",
                    is_model_mapping=mapped,
                )
                observation.record_attempt(
                    {"target_model_id": "same-model", "response_model": "same-model", "outcome": "success"}
                )
                observation.finish("success")
                self.assertEqual(
                    "same-model" if mapped else None,
                    logs[0]["upstream_attempts"][0]["target_model_id"],
                )
                self.assertNotIn("is_model_mapping", logs[0])

    def test_provider_retry_counts_failure_and_success_once(self):
        app, service = pipeline.ProxyServicePipelineTests()._build_service()
        provider = LLMProvider(
            name="demo",
            api="https://example.test/v1/chat/completions",
            source_format="openai_chat",
            target_formats=("openai_chat",),
            model_list=("model",),
            max_retries=2,
        )
        responses = [(response(503, {"error": {"message": "busy"}}), False, 503), (response(), False, 200)]
        logs = []
        observation = RequestObservation(
            lambda **row: logs.append(row),
            started_at=time.perf_counter(),
            start_time=datetime.now(),
            request_model="public",
        )
        observation.activate()
        try:
            with app.test_request_context(), patch.object(service, "_open_upstream_response", side_effect=responses):
                result, status, failure = service.proxy_request(
                    provider,
                    {"model": "demo/model", "messages": [{"role": "user", "content": "hi"}]},
                    {},
                    on_complete=observation.complete,
                )
                self.assertEqual(200, status)
                self.assertIsNone(failure)
                observation.wrap(result, "openai_chat")
        finally:
            observation.deactivate()
        self.assertEqual(1, len(logs))
        self.assertEqual("success", logs[0]["outcome"])
        attempts = logs[0]["upstream_attempts"]
        self.assertEqual(["error", "success"], [item["outcome"] for item in attempts])
        self.assertEqual(["demo/model", "demo/model"], [item["target_model_id"] for item in attempts])

    def test_connection_failure_is_exception_and_internal_error_is_excluded(self):
        with self.assertRaises(requests.Timeout):
            observe_response(lambda: (_ for _ in ()).throw(requests.Timeout("timeout")), "target")
        with self.assertRaises(ValueError):
            observe_response(lambda: (_ for _ in ()).throw(ValueError("local bug")), "target")
        self.assertEqual(["exception"], [item["outcome"] for item in self.events])

    def test_stream_error_incomplete_cancel_and_terminal_are_distinct(self):
        cases = [
            ([b'data: {"error":{"message":"busy"}}\n\n', b"data: [DONE]\n\n"], "error"),
            ([b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'], "exception"),
            ([b'data: {"type":"response.completed","response":{"model":"actual"}}\n\n'], "success"),
            ([b'data: {"type":"message_stop"}\n\n'], "success"),
            (
                [
                    b'data: {"type":"response.incomplete","response":{"incomplete_details":{"reason":"max_output_tokens"}}}\n\n'
                ],
                "success",
            ),
        ]
        for chunks, expected in cases:
            raw = pipeline.FakeStreamResponse(chunks)
            tracked = ObservedResponse(raw, self.events.append, "target", stream=True)
            self.assertEqual(chunks, list(tracked.iter_content()))
            tracked.close()
            self.assertEqual(expected, self.events[-1]["outcome"])
        self.assertEqual(len(cases), len(self.events))
        tracked = ObservedResponse(pipeline.FakeStreamResponse([]), self.events.append, "target", stream=True)
        tracked.close()
        self.assertEqual("cancelled", self.events[-1]["outcome"])

    def test_raw_success_is_preserved_if_later_processing_fails(self):
        tracked = observe_response(lambda: response(), "target")
        self.assertIn(b"actual", tracked.content)
        try:
            raise ValueError("translation failed")
        except ValueError:
            tracked.close()
        self.assertEqual(["success"], [item["outcome"] for item in self.events])

    def test_stream_formats_and_final_unterminated_event(self):
        cases = [
            ("ndjson", [b'{"model":"actual","done":false}\n', b'{"done":true}'], "success"),
            ("ndjson", [b'{"error":"busy"}\n'], "error"),
            ("ndjson", [b'{"done":false}\n'], "exception"),
            ("raw_text", [b"hello", b" world"], "success"),
            ("sse_json", [b"data: [DONE]"], "success"),
        ]
        for stream_format, chunks, expected in cases:
            with self.subTest(stream_format=stream_format, expected=expected):
                tracked = ObservedResponse(
                    pipeline.FakeStreamResponse(chunks),
                    self.events.append,
                    "target",
                    stream=True,
                    stream_format=stream_format,
                )
                self.assertEqual(chunks, list(tracked.iter_content()))
                tracked.close()
                self.assertEqual(expected, self.events[-1]["outcome"])
        self.assertEqual(len(cases), len(self.events))

    def test_nonstream_failed_status_is_upstream_failure(self):
        tracked = observe_response(lambda: response(payload={"status": "failed"}), "target")
        tracked.json()
        tracked.close()
        self.assertEqual(["error"], [item["outcome"] for item in self.events])

    def test_captured_stream_recorder_survives_scope_exit_and_is_isolated(self):
        other = []
        context = copy_context()
        context.run(attempt_recorder.set, other.append)
        tracked = context.run(observe_response, lambda: response(), "other")
        self.assertIn(b"actual", tracked.content)
        self.assertEqual([], self.events)
        self.assertEqual(1, len(other))
