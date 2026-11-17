"""LLM 数据记录与真实下游请求生命周期的集成测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import yaml
from flask import Flask, Response
from src.application.app_context import AppContext
from src.config.config_manager import ConfigManager
from src.external import LLMProvider
from src.presentation.proxy_controller import ProxyController
from src.services.llm_recording import LLMRecordingService
from src.services.upstream_observation import attempt_recorder
from tests.test_proxy_error_handling import (
    FakeCodexProxyService,
    FakeLogService,
    FakeProviderManager,
    FakeUserService,
)

ROOT = Path(__file__).resolve().parents[1]


def sse(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


class AttemptProxyService:
    """在观测范围内提供受控上游尝试与最终下游响应。"""

    def __init__(self, response: Response, attempts: list[dict], *, mutate_request: bool = False):
        self.response = response
        self.attempts = attempts
        self.mutate_request = mutate_request

    def proxy_request(self, *args, **kwargs):
        del kwargs
        recorder = attempt_recorder.get()
        assert recorder is not None
        for attempt in self.attempts:
            recorder(attempt)
        if self.mutate_request:
            args[1]["messages"][0]["content"] = "changed after capture"
        return self.response, self.response.status_code, None


class RecordingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    @staticmethod
    def attempts(model="gpt-4.1", response_model="gpt-4.1-2025-04-14", protocol="openai_chat"):
        return [
            {
                "target_model_id": "demo/model",
                "actual_request_model": model,
                "upstream_protocol": protocol,
                "response_model": response_model,
                "outcome": "success",
                "http_status": 200,
            }
        ]

    def build_app(
        self,
        case: str,
        response: Response,
        *,
        enabled: bool | None = True,
        attempts: list[dict] | None = None,
        mutate_request: bool = False,
    ):
        case_root = self.root / case
        case_root.mkdir()
        config = {
            "server": {"host": "127.0.0.1", "port": 8080},
            "admin": {"username": "", "password": ""},
            "providers": [],
        }
        if enabled is not None:
            config["llm_recording"] = {
                "enabled": enabled,
                "directory": str(case_root / "records"),
            }
        config_path = case_root / "config.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n")
        config_manager = ConfigManager(config_path, case_root)
        app = Flask(__name__)
        logger = Mock()
        ctx = AppContext(
            logger=logger,
            config_manager=config_manager,
            root_path=case_root,
            flask_app=app,
        )
        recording = LLMRecordingService(config_manager, case_root, logger)
        self.addCleanup(recording.close)
        provider = LLMProvider(
            name="demo",
            api="https://example.test/v1/chat/completions",
            source_format="openai_chat",
            model_list=("model",),
        )
        proxy = AttemptProxyService(
            response,
            self.attempts() if attempts is None else attempts,
            mutate_request=mutate_request,
        )
        ProxyController(
            ctx,
            proxy,
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
            llm_recording_service=recording,
        )
        return app, recording, case_root

    @staticmethod
    def read_records(case_root: Path):
        records = []
        for path in sorted((case_root / "records" / "records").glob("*.jsonl")):
            records.extend(json.loads(line) for line in path.read_text("utf-8").splitlines())
        return records

    def test_default_disabled_does_not_create_recording_directory(self):
        app, recording, case_root = self.build_app(
            "disabled",
            Response('{"choices":[]}', content_type="application/json"),
            enabled=None,
        )
        response = app.test_client().post(
            "/v1/chat/completions",
            json={"model": "demo/model", "messages": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(200, response.status_code)
        recording.close()
        self.assertFalse((case_root / "data/llm-records").exists())
        self.assertFalse((case_root / "records").exists())

    def test_nonstream_uses_successful_fallback_model_and_original_input_snapshot(self):
        attempts = [
            {
                "target_model_id": "demo/broken",
                "actual_request_model": "broken-model",
                "response_model": None,
                "outcome": "error",
                "http_status": 503,
            },
            *self.attempts(),
        ]
        app, recording, case_root = self.build_app(
            "fallback",
            Response(
                json.dumps(
                    {
                        "model": "downstream-default",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "回答"},
                                "finish_reason": "stop",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                content_type="application/json",
            ),
            attempts=attempts,
            mutate_request=True,
        )
        response = app.test_client().post(
            "/v1/chat/completions",
            json={
                "model": "demo/model",
                "messages": [
                    {"role": "system", "content": "Use Chinese."},
                    {"role": "user", "content": "你好"},
                ],
            },
        )
        self.assertEqual(200, response.status_code)
        recording.close()
        records = self.read_records(case_root)
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual("gpt-4.1", record["request_model"])
        self.assertEqual("gpt-4.1-2025-04-14", record["response_model"])
        self.assertEqual("openai_chat", record["upstream_protocol"])
        self.assertEqual("回答", record["output"][0]["content"])
        self.assertEqual("complete", record["capture_status"])
        reference = record["input"][0]["prompt_ref"]
        prompt_path = case_root / "records" / "prompts" / f"{reference.removeprefix('sha256:')}.json"
        self.assertEqual("Use Chinese.", json.loads(prompt_path.read_text("utf-8"))["content"])

    def test_three_downstream_protocols_record_streaming_output(self):
        cases = {
            "openai_chat": (
                "/v1/chat/completions",
                {"model": "demo/model", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                [
                    sse({"choices": [{"index": 0, "delta": {"role": "assistant", "content": "你"}}]}),
                    sse({"choices": [{"index": 0, "delta": {"content": "好"}, "finish_reason": "stop"}]}),
                    b"data: [DONE]\n\n",
                ],
            ),
            "openai_responses": (
                "/v1/responses",
                {"model": "demo/model", "input": [{"role": "user", "content": "hello"}], "stream": True},
                [
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
                            "delta": "你好",
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
                                        "content": [{"type": "output_text", "text": "你好"}],
                                    }
                                ],
                            },
                        }
                    ),
                ],
            ),
            "claude_chat": (
                "/v1/messages",
                {"model": "demo/model", "messages": [{"role": "user", "content": "hello"}], "stream": True},
                [
                    sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                    sse({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你好"}}),
                    sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
                    sse({"type": "message_stop"}),
                ],
            ),
        }
        for source_format, (route, body, chunks) in cases.items():
            with self.subTest(source_format=source_format):
                raw = b"".join(chunks)
                split = raw.index("你".encode("utf-8")) + 1
                response = Response(
                    iter([raw[:split], raw[split : split + 5], raw[split + 5 :]]),
                    content_type="text/event-stream",
                )
                app, recording, case_root = self.build_app(
                    source_format,
                    response,
                    attempts=self.attempts(protocol=source_format),
                )
                downstream = app.test_client().post(route, json=body)
                self.assertEqual(raw, downstream.get_data())
                recording.close()
                records = self.read_records(case_root)
                self.assertEqual(1, len(records))
                self.assertEqual("complete", records[0]["capture_status"])
                self.assertEqual(source_format, records[0]["upstream_protocol"])
                if source_format in {"openai_responses", "claude_chat"}:
                    self.assertEqual("你好", records[0]["output"][0]["content"][0]["text"])
                else:
                    self.assertEqual("你好", records[0]["output"][0]["content"])

    def test_client_close_preserves_partial_stream_as_interrupted(self):
        app, recording, case_root = self.build_app(
            "cancelled",
            Response(
                iter([sse({"choices": [{"index": 0, "delta": {"content": "partial"}}]})]),
                content_type="text/event-stream",
            ),
        )
        response = app.test_client().post(
            "/v1/chat/completions",
            json={"model": "demo/model", "messages": [{"role": "user", "content": "hello"}], "stream": True},
            buffered=False,
        )
        iterator = iter(response.response)
        self.assertIn(b"partial", next(iterator))
        response.close()
        recording.close()
        records = self.read_records(case_root)
        self.assertEqual(1, len(records))
        self.assertEqual("interrupted", records[0]["capture_status"])
        self.assertEqual("partial", records[0]["output"][0]["content"])

    def test_compact_and_images_never_start_capture(self):
        case_root = self.root / "excluded"
        case_root.mkdir()
        config_path = case_root / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "server": {"host": "127.0.0.1", "port": 8080},
                    "admin": {"username": "", "password": ""},
                    "llm_recording": {"enabled": True, "directory": str(case_root / "records")},
                    "providers": [],
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        app = Flask(__name__)
        config_manager = ConfigManager(config_path, case_root)
        ctx = AppContext(
            logger=Mock(),
            config_manager=config_manager,
            root_path=case_root,
            flask_app=app,
        )
        recording = Mock()
        provider = LLMProvider(
            name="demo",
            api="https://example.test/v1/chat/completions",
            source_format="openai_chat",
            model_list=("model",),
        )
        codex = FakeCodexProxyService(
            proxy_result=(Response('{"output":[]}', content_type="application/json"), 200, None),
            image_proxy_result=(Response('{"created":1,"data":[]}', content_type="application/json"), 200, None),
        )
        ProxyController(
            ctx,
            AttemptProxyService(Response('{"choices":[]}'), self.attempts()),
            FakeUserService(),
            FakeLogService(),
            FakeProviderManager(provider),
            codex_proxy_service=codex,
            llm_recording_service=recording,
        )
        client = app.test_client()
        compact = client.post("/v1/responses/compact", json={"model": "gpt-5", "input": []})
        image = client.post("/v1/images/generations", json={"prompt": "draw"})
        self.assertEqual(200, compact.status_code)
        self.assertEqual(200, image.status_code)
        recording.start_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
