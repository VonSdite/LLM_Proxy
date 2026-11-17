"""LLM 数据记录核心行为测试。"""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import gevent
from gevent.monkey import get_original
from src.services.llm_recording import LLMRecordingService

ROOT = Path(__file__).resolve().parents[1]


def sse(payload, event=None):
    event_line = f"event: {event}\n" if event else ""
    if isinstance(payload, str):
        data = payload
    else:
        data = json.dumps(payload, ensure_ascii=False)
    return f"{event_line}data: {data}\n\n".encode("utf-8")


class Config:
    def __init__(self, directory, enabled=True):
        self.directory = str(directory)
        self.enabled = enabled

    def is_llm_recording_enabled(self):
        return self.enabled

    def get_llm_recording_directory(self):
        return self.directory


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "records-root"
        self.config = Config(self.directory)
        self.logger = Mock()
        self.service = self.create_service()

    def create_service(self):
        service = LLMRecordingService(self.config, self.root, self.logger)
        self.addCleanup(service.close)
        return service

    def capture(self, body=None, source="openai_chat", model="actual/model", response_model="actual-response"):
        request = body or {"model": "public/model", "messages": [{"role": "user", "content": "question"}]}
        capture = self.service.start_request(request, source)
        self.assertIsNotNone(capture)
        capture.observe_attempt(
            {
                "actual_request_model": model,
                "upstream_protocol": source,
                "response_model": response_model,
                "outcome": "success",
            }
        )
        return capture

    def finish(self, capture, payload, outcome="success"):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        capture.feed(payload)
        capture.finish(outcome)

    def close_and_read(self):
        self.service.close()
        paths = sorted((self.directory / "records").glob("*.jsonl"))
        lines = []
        for path in paths:
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r", raw)
            self.assertTrue(raw.endswith(b"\n"))
            lines.extend(json.loads(line) for line in raw.splitlines())
        return lines, paths

    def test_disabled_does_not_copy_or_create_directory(self):
        self.config.enabled = False
        body = {"model": "public", "messages": [{"role": "user", "content": "private"}]}
        self.assertIsNone(self.service.start_request(body, "openai_chat"))
        self.assertFalse(self.directory.exists())
        self.service.close()

    def test_relative_directory_is_rejected_without_creating_records(self):
        self.config.directory = "data/relative-records"
        capture = self.service.start_request(
            {"model": "public", "messages": [{"role": "user", "content": "q"}]},
            "openai_chat",
        )
        self.assertIsNone(capture)
        self.service.close()
        self.assertFalse((self.root / "data/relative-records").exists())
        self.logger.log.assert_called()

    def test_empty_directory_uses_absolute_project_default(self):
        self.config.directory = ""
        self.directory = (self.root / "data/llm-records").resolve()
        capture = self.capture()
        self.finish(
            capture,
            {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]},
        )
        records, paths = self.close_and_read()
        self.assertEqual(1, len(records))
        self.assertEqual(1, len(paths))
        self.assertTrue(paths[0].is_absolute())

    def test_chat_nonstream_preserves_candidates_prompts_and_actual_models(self):
        body = {
            "model": "my-assistant",
            "messages": [
                {"role": "system", "content": "Use Chinese."},
                {"role": "user", "content": "question"},
            ],
            "temperature": 0.7,
            "max_completion_tokens": 100,
            "tools": [{"type": "function", "function": {"name": "weather"}}],
            "tool_choice": "auto",
            "response_format": {"type": "json_object"},
        }
        capture = self.capture(body, model="gpt-4.1", response_model="gpt-4.1-2025-04-14")
        structured = '{"explanation":"生成器","example":"yield 1"}'
        self.finish(
            capture,
            {
                "model": "downstream-default-must-not-be-used",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": structured},
                        "finish_reason": "stop",
                    },
                    {
                        "index": 2,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "weather", "arguments": '{"city":"上海"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    },
                ],
            },
        )
        records, _ = self.close_and_read()
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual(1, record["schema_version"])
        self.assertEqual("gpt-4.1", record["request_model"])
        self.assertEqual("gpt-4.1-2025-04-14", record["response_model"])
        self.assertEqual("openai_chat", record["upstream_protocol"])
        self.assertEqual("complete", record["capture_status"])
        self.assertIsNone(record["finish_reason"])
        self.assertEqual([0, 2], [item["choice_index"] for item in record["output"]])
        self.assertEqual(structured, record["output"][0]["content"])
        self.assertEqual("tool_calls", record["output"][1]["finish_reason"])
        self.assertEqual({"temperature": 0.7, "max_completion_tokens": 100}, record["generation_params"])
        self.assertEqual("auto", record["tool_choice"])
        reference = record["input"][0]["prompt_ref"]
        prompt_path = self.directory / "prompts" / f"{reference.removeprefix('sha256:')}.json"
        self.assertEqual({"role": "system", "content": "Use Chinese."}, json.loads(prompt_path.read_text("utf-8")))
        self.assertNotIn("usage", record)

    def test_chat_stream_handles_split_utf8_parallel_tools_and_missing_response_model(self):
        capture = self.capture(response_model=None)
        chunks = [
            sse({"choices": [{"index": 0, "delta": {"role": "assistant", "content": "你"}}]}),
            sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": "好",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_a",
                                        "type": "function",
                                        "function": {"name": "get_", "arguments": '{"city":"'},
                                    },
                                    {
                                        "index": 1,
                                        "id": "call_b",
                                        "type": "function",
                                        "function": {"name": "time", "arguments": '{"zone":"'},
                                    },
                                ],
                            },
                        }
                    ]
                }
            ),
            sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"name": "weather", "arguments": '上海"}'}},
                                    {"index": 1, "function": {"arguments": 'UTC"}'}},
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ),
            b"data: [DONE]\n\n",
        ]
        raw = b"".join(chunks)
        split = raw.index("你".encode()) + 1
        for piece in (raw[:split], raw[split : split + 7], raw[split + 7 :]):
            capture.feed(piece)
        capture.finish("success")
        capture.finish("success")
        records, _ = self.close_and_read()
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertIsNone(record["response_model"])
        self.assertEqual("complete", record["capture_status"])
        self.assertEqual("你好", record["output"][0]["content"])
        self.assertEqual("get_weather", record["output"][0]["tool_calls"][0]["function"]["name"])
        self.assertEqual('{"city":"上海"}', record["output"][0]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual('{"zone":"UTC"}', record["output"][0]["tool_calls"][1]["function"]["arguments"])

    def test_responses_stream_terminal_output_does_not_duplicate_deltas(self):
        body = {
            "model": "public",
            "instructions": "developer instructions",
            "input": [{"role": "user", "content": "hello"}],
            "max_output_tokens": 50,
            "text": {"format": {"type": "json_object"}},
        }
        capture = self.capture(body, "openai_responses")
        events = [
            sse(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {"id": "msg_1", "type": "message", "role": "assistant", "content": []},
                }
            ),
            sse(
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "item_id": "msg_1",
                    "content_index": 0,
                    "delta": "Hel",
                }
            ),
            sse(
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "item_id": "msg_1",
                    "content_index": 0,
                    "delta": "lo",
                }
            ),
            sse(
                {
                    "type": "response.output_item.added",
                    "output_index": 1,
                    "item": {"id": "fc_1", "type": "function_call", "name": "weather", "call_id": "call_1"},
                }
            ),
            sse(
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 1,
                    "item_id": "fc_1",
                    "delta": '{"city":"上海"}',
                }
            ),
            sse(
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "output": [
                            {
                                "id": "msg_1",
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Hello"}],
                            },
                            {
                                "id": "fc_1",
                                "type": "function_call",
                                "name": "weather",
                                "call_id": "call_1",
                                "arguments": '{"city":"上海"}',
                            },
                        ],
                    },
                }
            ),
        ]
        self.finish(capture, b"".join(events))
        records, _ = self.close_and_read()
        record = records[0]
        self.assertEqual("complete", record["capture_status"])
        self.assertEqual("completed", record["finish_reason"])
        self.assertEqual(2, len(record["output"]))
        self.assertEqual("Hello", record["output"][0]["content"][0]["text"])
        self.assertEqual('{"city":"上海"}', record["output"][1]["arguments"])
        self.assertEqual({"type": "json_object"}, record["response_format"])
        instruction_ref = record["input"][0]
        self.assertEqual("responses.instructions", instruction_ref["source"])
        prompt = json.loads(
            (self.directory / "prompts" / f"{instruction_ref['prompt_ref'].removeprefix('sha256:')}.json").read_text(
                "utf-8"
            )
        )
        self.assertEqual("developer", prompt["role"])

    def test_responses_reference_and_media_are_explicitly_incomplete(self):
        body = {
            "model": "public",
            "previous_response_id": "resp_hidden",
            "input": [
                {"role": "user", "content": "describe"},
                {"type": "input_image", "source": {"type": "base64", "media_type": "image/png", "data": "SECRET"}},
            ],
        }
        capture = self.capture(body, "openai_responses")
        self.finish(capture, {"status": "completed", "output": []})
        records, _ = self.close_and_read()
        record = records[0]
        self.assertEqual("partial", record["capture_status"])
        self.assertIn("media_not_archived", record["capture_issues"])
        self.assertIn("referenced_context_missing", record["capture_issues"])
        self.assertEqual("resp_hidden", record["input"][0]["previous_response_id"])
        self.assertFalse(record["input"][0]["context_complete"])
        self.assertNotIn("SECRET", json.dumps(record, ensure_ascii=False))
        self.assertFalse(record["input"][2]["capture_media_archived"])

    def test_responses_reasoning_text_and_summary_remain_structured(self):
        capture = self.capture(source="openai_responses")
        events = [
            sse(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {"id": "reason_1", "type": "reasoning", "content": [], "summary": []},
                }
            ),
            sse(
                {
                    "type": "response.reasoning_text.delta",
                    "output_index": 0,
                    "item_id": "reason_1",
                    "content_index": 0,
                    "delta": "reason",
                }
            ),
            sse(
                {
                    "type": "response.reasoning_summary_text.delta",
                    "output_index": 0,
                    "item_id": "reason_1",
                    "summary_index": 0,
                    "delta": "summary",
                }
            ),
            sse({"type": "response.completed", "response": {"status": "completed", "output": []}}),
        ]
        self.finish(capture, b"".join(events))
        records, _ = self.close_and_read()
        item = records[0]["output"][0]
        self.assertEqual("reasoning", item["type"])
        self.assertEqual("reason", item["content"][0]["text"])
        self.assertEqual("summary", item["summary"][0]["text"])
        self.assertEqual("complete", records[0]["capture_status"])

    def test_claude_nonstream_preserves_native_blocks(self):
        capture = self.capture(source="claude_chat")
        content = [{"type": "thinking", "thinking": "思考"}, {"type": "text", "text": "回答"}]
        self.finish(capture, {"role": "assistant", "content": content, "stop_reason": "end_turn"})
        records, _ = self.close_and_read()
        self.assertEqual("complete", records[0]["capture_status"])
        self.assertEqual("end_turn", records[0]["finish_reason"])
        self.assertEqual(content, records[0]["output"][0]["content"])

    def test_claude_stream_preserves_thinking_tool_input_and_stop_reason(self):
        capture = self.capture(
            {
                "model": "claude-public",
                "system": "system prompt",
                "messages": [{"role": "user", "content": "use a tool"}],
                "max_tokens": 200,
            },
            "claude_chat",
        )
        events = [
            sse({"type": "message_start", "message": {"role": "assistant", "content": []}}),
            sse({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
            sse({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "思"}}),
            sse({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "考"}}),
            sse(
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "tool_use", "id": "tool_1", "name": "weather", "input": {}},
                }
            ),
            sse(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"city":"上'},
                }
            ),
            sse(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '海"}'},
                }
            ),
            sse({"type": "content_block_stop", "index": 1}),
            sse({"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
            sse({"type": "message_stop"}),
        ]
        self.finish(capture, b"".join(events))
        records, _ = self.close_and_read()
        record = records[0]
        self.assertEqual("complete", record["capture_status"])
        self.assertEqual("tool_use", record["finish_reason"])
        content = record["output"][0]["content"]
        self.assertEqual("思考", content[0]["thinking"])
        self.assertEqual({"city": "上海"}, content[1]["input"])
        self.assertNotIn("_input_json_parts", content[1])

    def test_claude_partial_tool_arguments_and_cancellation_remain_visible(self):
        capture = self.capture(source="claude_chat")
        self.finish(
            capture,
            b"".join(
                [
                    sse(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "tool_use", "id": "tool_1", "name": "weather"},
                        }
                    ),
                    sse(
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "input_json_delta", "partial_json": '{"city":'},
                        }
                    ),
                ]
            ),
            "cancelled",
        )
        records, _ = self.close_and_read()
        record = records[0]
        self.assertEqual("interrupted", record["capture_status"])
        self.assertEqual('{"city":', record["output"][0]["content"][0]["partial_json"])
        self.assertIn("incomplete_tool_arguments", record["capture_issues"])

    def test_failure_interruption_parse_error_and_truncation_are_distinct(self):
        failed = self.capture()
        self.finish(failed, {"error": {"message": "failed"}}, "error")
        cancelled = self.capture()
        self.finish(cancelled, sse({"choices": [{"index": 0, "delta": {"content": "partial"}}]}), "cancelled")
        malformed = self.capture()
        self.finish(malformed, b"data: {not-json}\n\n")
        with (
            patch("src.services.llm_recording.MAX_INPUT_BYTES", 128),
            patch("src.services.llm_recording.MAX_OUTPUT_BYTES", 32),
        ):
            truncated = self.capture({"model": "public", "messages": [{"role": "user", "content": "x" * 500}]})
            truncated.feed(b"x" * 100)
            truncated.finish("success")
        records, _ = self.close_and_read()
        self.assertEqual(["failed", "interrupted", "parse_error", "truncated"], [r["capture_status"] for r in records])
        self.assertEqual("partial", records[1]["output"][0]["content"])
        self.assertIn("output_truncated", records[3]["capture_issues"])
        self.assertIn("input_truncated", records[3]["capture_issues"])

    def test_prompt_deduplication_is_exact_and_survives_restart(self):
        contents = [
            ("system", "same"),
            ("system", "same"),
            ("system", "same "),
            ("developer", "same"),
            ("system", ["a", "b"]),
            ("system", ["b", "a"]),
        ]
        for role, content in contents:
            capture = self.capture(
                {
                    "model": "public",
                    "messages": [{"role": role, "content": content}, {"role": "user", "content": "q"}],
                }
            )
            self.finish(
                capture, {"choices": [{"message": {"role": "assistant", "content": "a"}, "finish_reason": "stop"}]}
            )
        records, paths = self.close_and_read()
        self.assertEqual(6, len(records))
        self.assertEqual(5, len(list((self.directory / "prompts").glob("*.json"))))
        self.assertEqual(records[0]["input"][0]["prompt_ref"], records[1]["input"][0]["prompt_ref"])
        self.assertEqual(1, len(paths))
        first_prompt = (
            self.directory / "prompts" / f"{records[0]['input'][0]['prompt_ref'].removeprefix('sha256:')}.json"
        )
        modified = first_prompt.stat().st_mtime_ns

        self.service = self.create_service()
        capture = self.capture(
            {"model": "public", "messages": [{"role": "system", "content": "same"}, {"role": "user", "content": "q"}]}
        )
        self.finish(capture, {"choices": [{"message": {"role": "assistant", "content": "b"}, "finish_reason": "stop"}]})
        records, paths = self.close_and_read()
        self.assertEqual(7, len(records))
        self.assertEqual(2, len(paths))
        self.assertEqual(5, len(list((self.directory / "prompts").glob("*.json"))))
        self.assertEqual(modified, first_prompt.stat().st_mtime_ns)

    def test_prompt_is_durable_before_referencing_record(self):
        from src.services.llm_recording import _ShardWriter

        original = _ShardWriter._write_line
        checked = []

        def checking_write(writer, date, line):
            record = json.loads(line)
            for message in record["input"]:
                if isinstance(message, dict) and "prompt_ref" in message:
                    digest = message["prompt_ref"].removeprefix("sha256:")
                    self.assertTrue((writer.prompts_directory / f"{digest}.json").is_file())
                    checked.append(digest)
            return original(writer, date, line)

        with patch.object(_ShardWriter, "_write_line", checking_write):
            capture = self.capture(
                {
                    "model": "public",
                    "messages": [{"role": "system", "content": "dedup"}, {"role": "user", "content": "q"}],
                }
            )
            self.finish(
                capture, {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
            )
            self.close_and_read()
        self.assertEqual(1, len(checked))

    def test_concurrent_requests_have_complete_unmixed_lines_and_close_drains(self):
        def write(index):
            capture = self.capture(model=f"model-{index}", response_model=f"response-{index}")
            for _ in range(3):
                capture.feed(sse({"choices": [{"index": 0, "delta": {"content": f"answer-{index}-"}}]}))
            capture.feed(b"data: [DONE]\n\n")
            capture.finish("success")

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(24)))
        records, paths = self.close_and_read()
        self.assertEqual(24, len(records))
        self.assertEqual({f"model-{index}" for index in range(24)}, {r["request_model"] for r in records})
        for record in records:
            index = record["request_model"].removeprefix("model-")
            self.assertEqual(f"response-{index}", record["response_model"])
            self.assertEqual(f"answer-{index}-" * 3, record["output"][0]["content"])
        self.assertEqual(1, len(paths))

    def test_slow_native_write_does_not_block_other_greenlets(self):
        from src.services.llm_recording import _ShardWriter

        started = get_original("queue", "SimpleQueue")()
        release = get_original("queue", "SimpleQueue")()
        original = _ShardWriter.write

        def slow_write(writer, job):
            started.put(None)
            release.get()
            return original(writer, job)

        ticks = []

        def tick():
            gevent.sleep(0.02)
            ticks.append(True)

        with patch.object(_ShardWriter, "write", slow_write):
            capture = self.capture()
            greenlet = gevent.spawn(tick)
            try:
                self.finish(capture, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
                greenlet.join(timeout=1)
                self.assertEqual([True], ticks)
                for _ in range(100):
                    if not started.empty():
                        break
                    gevent.sleep(0.01)
                self.assertFalse(started.empty())
            finally:
                release.put(None)
        records, _ = self.close_and_read()
        self.assertEqual(1, len(records))

    def test_queue_full_and_disk_failure_do_not_raise_in_request_path(self):
        with patch("src.services.llm_recording.MAX_QUEUE_RECORDS", 0):
            capture = self.capture()
            self.finish(capture, {"choices": []})
        self.service.close()
        self.assertFalse(self.directory.exists())

        self.config.directory = str(self.root / "not-a-directory")
        self.config.directory and Path(self.config.directory).write_text("file", encoding="utf-8")
        self.service = self.create_service()
        capture = self.capture()
        self.finish(capture, {"choices": []})
        self.service.close()
        self.assertTrue(self.logger.log.called or self.logger.error.called)

    def test_active_memory_limit_drops_new_capture(self):
        with patch("src.services.llm_recording.MAX_ACTIVE_BYTES", 32):
            capture = self.service.start_request(
                {"model": "public", "messages": [{"role": "user", "content": "x" * 200}]},
                "openai_chat",
            )
        self.assertIsNone(capture)
        self.service.close()
        self.assertFalse(self.directory.exists())

    def test_configuration_snapshot_controls_directory_and_existing_tail_is_not_appended(self):
        first = self.capture()
        self.config.directory = str(self.root / "second")
        second = self.capture()
        self.finish(
            first, {"choices": [{"message": {"role": "assistant", "content": "first"}, "finish_reason": "stop"}]}
        )
        self.finish(
            second, {"choices": [{"message": {"role": "assistant", "content": "second"}, "finish_reason": "stop"}]}
        )
        self.service.close()
        self.assertEqual(1, len(list((self.directory / "records").glob("*.jsonl"))))
        second_directory = Path(self.config.directory)
        self.assertEqual(1, len(list((second_directory / "records").glob("*.jsonl"))))

        self.config.directory = str(self.directory)
        self.service = self.create_service()
        self.service._instance = "fixed-instance"
        date = datetime.now().astimezone().date().isoformat()
        existing = self.directory / "records" / f"{date}-fixed-instance-000001.jsonl"
        existing.write_bytes(b'{"incomplete":')
        capture = self.capture()
        self.finish(
            capture, {"choices": [{"message": {"role": "assistant", "content": "new"}, "finish_reason": "stop"}]}
        )
        self.service.close()
        self.assertEqual(b'{"incomplete":', existing.read_bytes())
        successor = self.directory / "records" / f"{date}-fixed-instance-000002.jsonl"
        self.assertTrue(successor.is_file())

    def test_size_rotation_uses_new_files(self):
        with patch("src.services.llm_recording.MAX_SHARD_BYTES", 100):
            for value in ("a", "b"):
                capture = self.capture()
                self.finish(
                    capture,
                    {"choices": [{"message": {"role": "assistant", "content": value}, "finish_reason": "stop"}]},
                )
            records, paths = self.close_and_read()
        self.assertEqual(2, len(records))
        self.assertEqual(2, len(paths))


if __name__ == "__main__":
    unittest.main()
