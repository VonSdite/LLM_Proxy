from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.external import LLMProvider
from src.presentation.proxy_controller import ProxyController
from src.proxy_core import decode_stream_events
from src.services.codex_oauth_service import CodexOAuthService
from src.services.codex_proxy_service import CODEX_BACKEND_RESPONSES_URL, CodexProxyService
from src.services.codex_reasoning_cache import CodexReasoningCache
from src.translators.codex_compat import CodexRequestCompatibility
from src.translators.codex_reasoning import decode_codex_reasoning
from src.translators.registry import OpenAIResponsesClaudeTranslator, OpenAIResponsesTranslator
from tests.test_codex_proxy_service import FakeConfigManager, FakeHTTPResponse, build_context, write_auth_file
from tests.test_proxy_error_handling import (
    FakeProviderManager,
    FakeUserService,
    RecordingLogService,
    RecordingProxyService,
)


def sse_response(output, *, split=False):
    events = []
    if split:
        events.append(
            {
                "type": "response.created",
                "response": {"id": "resp_1", "created_at": 123, "model": "gpt-5.4", "output": []},
            }
        )
        events.extend(
            {"type": "response.output_item.done", "output_index": index, "item": item}
            for index, item in enumerate(output)
        )
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "created_at": 123,
                "model": "gpt-5.4",
                "status": "completed",
                "output": [] if split else output,
                "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
            },
        }
    )
    return FakeHTTPResponse(
        status_code=200, chunks=[("data: " + json.dumps(event) + "\n\n").encode() for event in events]
    )


def request_body(target_format, name="read", call_id="call_1"):
    schema = {
        "type": "object",
        "properties": {"value": {"oneOf": [{"const": index, "description": str(index)} for index in range(8)]}},
    }
    if target_format == "openai_responses":
        return {
            "model": "gpt-5.4",
            "instructions": "system",
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "question"}]}],
            "tools": [{"type": "function", "name": name, "parameters": schema}],
        }
    if target_format == "openai_chat":
        return {
            "model": "gpt-5.4",
            "messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "question"}],
            "tools": [{"type": "function", "function": {"name": name, "parameters": schema}}],
        }
    return {
        "model": "gpt-5.4",
        "system": "system",
        "max_tokens": 1024,
        "metadata": {"user_id": "user_test_session_stable"},
        "messages": [{"role": "user", "content": "question"}],
        "tools": [{"name": name, "input_schema": schema}],
    }


class CodexCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        write_auth_file(root, "only.json", "access", mtime=2000)
        self.ctx = build_context(root)
        self.oauth = CodexOAuthService(self.ctx)
        self.oauth.add_model("gpt-5.4")
        self.service = CodexProxyService(self.ctx, self.oauth)
        self.request_context = self.ctx.flask_app.test_request_context()
        self.request_context.push()
        self.addCleanup(self.request_context.pop)

    def invoke(self, body, target, **kwargs):
        response, status, failure = self.service.proxy_request(
            body, {}, resolved_target_format=target, replay_identity=kwargs.pop("replay_identity", "key:1"), **kwargs
        )
        self.assertIsNone(failure, failure)
        self.assertEqual(200, status)
        self.assertIsNotNone(response)
        if body.get("stream"):
            return [
                event.payload
                for event in decode_stream_events([response.get_data()], "sse_json")
                if event.kind == "json"
            ]
        return json.loads(response.get_data())

    def test_tool_and_reasoning_roundtrip_all_protocols_and_response_modes(self):
        name = "mcp__" + "server_" * 15 + "__read"
        call_id = "call_" + "long_" * 20
        for target in ("openai_chat", "claude_chat", "openai_responses"):
            for stream in (False, True):
                with self.subTest(target=target, stream=stream):
                    body = request_body(target, name)
                    body["stream"] = stream
                    if target == "openai_responses":
                        body["input"].append(
                            {
                                "type": "function_call",
                                "id": "original_" * 15,
                                "name": name,
                                "call_id": call_id,
                                "arguments": "{}",
                            }
                        )
                        body["input"].append({"type": "function_call_output", "call_id": call_id, "output": "ok"})
                    elif target == "openai_chat":
                        body["messages"].extend(
                            [
                                {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "type": "function",
                                            "id": call_id,
                                            "function": {"name": name, "arguments": "{}"},
                                        }
                                    ],
                                },
                                {"role": "tool", "tool_call_id": call_id, "content": "ok"},
                            ]
                        )
                    else:
                        body["messages"].extend(
                            [
                                {
                                    "role": "assistant",
                                    "content": [{"type": "tool_use", "id": call_id, "name": name, "input": {}}],
                                },
                                {
                                    "role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "ok"}],
                                },
                            ]
                        )
                    original = copy.deepcopy(body)
                    captured = {}

                    def post(url, **kwargs):
                        captured.update(kwargs["json"])
                        function = next(item for item in captured["input"] if item.get("type") == "function_call")
                        output = [
                            {
                                "type": "reasoning",
                                "id": "rs_1",
                                "summary": [{"type": "summary_text", "text": "thinking"}],
                                "encrypted_content": "encrypted-data",
                            },
                            {
                                "type": "function_call",
                                "id": "fc_new",
                                "call_id": function["call_id"],
                                "name": function["name"],
                                "arguments": '{"name":"business-value"}',
                            },
                        ]
                        return sse_response(output, split=stream)

                    with patch("src.services.codex_proxy_service.requests.post", side_effect=post):
                        result = self.invoke(body, target)
                    self.assertEqual(original, body)
                    function = next(item for item in captured["input"] if item.get("type") == "function_call")
                    self.assertLessEqual(len(function["name"]), 64)
                    self.assertLessEqual(len(function["call_id"]), 64)
                    self.assertEqual(
                        function["call_id"],
                        next(
                            item["call_id"] for item in captured["input"] if item.get("type") == "function_call_output"
                        ),
                    )
                    self.assertEqual(list(range(8)), captured["tools"][0]["parameters"]["properties"]["value"]["enum"])
                    self.assertIn("instructions", captured)
                    if stream:
                        wire = json.dumps(result)
                        self.assertIn(name, wire)
                        self.assertIn(call_id, wire)
                        self.assertIn("business-value", wire)
                        if target == "claude_chat":
                            signatures = [
                                event["delta"]["signature"]
                                for event in result
                                if event.get("type") == "content_block_delta"
                                and event.get("delta", {}).get("type") == "signature_delta"
                            ]
                            self.assertEqual(1, len(signatures))
                            self.assertEqual(
                                "encrypted-data", decode_codex_reasoning(signatures[0])["encrypted_content"]
                            )
                            self.assertEqual(
                                "tool_use",
                                next(
                                    event["delta"]["stop_reason"]
                                    for event in result
                                    if event.get("type") == "message_delta"
                                ),
                            )
                        elif target == "openai_chat":
                            details = [
                                event
                                for event in result
                                if event.get("choices")
                                and event["choices"][0].get("delta", {}).get("reasoning_details")
                            ]
                            self.assertEqual(1, len(details))
                            self.assertEqual(123, details[0]["created"])
                    elif target == "claude_chat":
                        self.assertEqual(name, result["content"][1]["name"])
                        self.assertEqual(call_id, result["content"][1]["id"])
                        self.assertEqual(
                            "encrypted-data",
                            decode_codex_reasoning(result["content"][0]["signature"])["encrypted_content"],
                        )
                    elif target == "openai_chat":
                        self.assertEqual(name, result["choices"][0]["message"]["tool_calls"][0]["function"]["name"])
                        self.assertEqual(call_id, result["choices"][0]["message"]["tool_calls"][0]["id"])
                        self.assertEqual("rs_1", result["choices"][0]["message"]["reasoning_details"][0]["id"])
                    else:
                        self.assertEqual(name, result["output"][1]["name"])

    def test_multiturn_reasoning_carrier_and_cache_fallback(self):
        for target in ("openai_chat", "claude_chat", "openai_responses"):
            for drop_carrier in (False, True):
                with self.subTest(target=target, drop_carrier=drop_carrier):
                    body = request_body(target)
                    reasoning = {
                        "type": "reasoning",
                        "id": "rs_original",
                        "summary": [],
                        "encrypted_content": f"encrypted-{target}-{drop_carrier}",
                    }
                    tool = {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "read",
                        "arguments": '{"value": 1}',
                    }
                    with patch(
                        "src.services.codex_proxy_service.requests.post",
                        return_value=sse_response([reasoning, tool], split=True),
                    ):
                        result = self.invoke(body, target)
                    if target == "openai_chat":
                        assistant = result["choices"][0]["message"]
                        if drop_carrier:
                            assistant.pop("reasoning_details", None)
                        body["messages"].extend(
                            [assistant, {"role": "tool", "tool_call_id": "call_1", "content": "ok"}]
                        )
                    elif target == "claude_chat":
                        content = result["content"]
                        if drop_carrier:
                            content = [block for block in content if block["type"] != "thinking"]
                        body["messages"].extend(
                            [
                                {"role": "assistant", "content": content},
                                {
                                    "role": "user",
                                    "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}],
                                },
                            ]
                        )
                    else:
                        body["input"].extend([tool] if drop_carrier else result["output"])
                        body["input"].append({"type": "function_call_output", "call_id": "call_1", "output": "ok"})
                    with patch("src.services.codex_proxy_service.requests.post", return_value=sse_response([])) as post:
                        self.invoke(body, target)
                    items = post.call_args.kwargs["json"]["input"]
                    restored = [item for item in items if item.get("type") == "reasoning"]
                    self.assertEqual([reasoning], restored)
                    self.assertLess(
                        items.index(restored[0]),
                        next(index for index, item in enumerate(items) if item.get("type") == "function_call"),
                    )

    def test_terminal_only_stream_emits_content_once(self):
        output = [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            }
        ]
        for target in ("openai_chat", "claude_chat"):
            with (
                self.subTest(target=target),
                patch("src.services.codex_proxy_service.requests.post", return_value=sse_response(output)),
            ):
                events = self.invoke({**request_body(target), "stream": True}, target)
                self.assertEqual(1, json.dumps(events).count("answer"))

    def test_stream_multiple_thinking_and_tool_blocks_keep_signatures_and_stop_reason(self):
        output = []
        for index in range(2):
            output.extend(
                [
                    {
                        "type": "reasoning",
                        "id": f"rs_{index}",
                        "encrypted_content": f"encrypted-{index}",
                        "summary": [{"type": "summary_text", "text": f"thinking-{index}"}],
                    },
                    {
                        "type": "function_call",
                        "id": f"fc_{index}",
                        "call_id": f"call_{index}",
                        "name": "read",
                        "arguments": json.dumps({"value": index}),
                    },
                ]
            )
        for target in ("openai_chat", "claude_chat"):
            for split in (True, False):
                with (
                    self.subTest(target=target, split=split),
                    patch(
                        "src.services.codex_proxy_service.requests.post", return_value=sse_response(output, split=split)
                    ),
                ):
                    events = self.invoke({**request_body(target), "stream": True}, target)
                if target == "openai_chat":
                    summaries = [
                        event["choices"][0]["delta"]["reasoning_content"]
                        for event in events
                        if event.get("choices") and event["choices"][0].get("delta", {}).get("reasoning_content")
                    ]
                else:
                    summaries = [
                        event["delta"]["thinking"]
                        for event in events
                        if event.get("delta", {}).get("type") == "thinking_delta"
                    ]
                self.assertEqual(["thinking-0", "thinking-1"], summaries)
                if target == "claude_chat":
                    signatures = [
                        event["delta"]["signature"]
                        for event in events
                        if event.get("delta", {}).get("type") == "signature_delta"
                    ]
                    self.assertEqual(
                        ["encrypted-0", "encrypted-1"],
                        [decode_codex_reasoning(signature)["encrypted_content"] for signature in signatures],
                    )
                    calls = [
                        event["content_block"]["id"]
                        for event in events
                        if event.get("type") == "content_block_start"
                        and event.get("content_block", {}).get("type") == "tool_use"
                    ]
                    self.assertEqual(["call_0", "call_1"], calls)

    def test_claude_session_metadata_and_explicit_reasoning_are_preserved(self):
        for metadata in (
            {"session_id": "stable"},
            {"user_id": '{"session_id":"stable"}'},
            {"user_id": "user_test_session_stable"},
        ):
            body = {**request_body("claude_chat"), "metadata": metadata, "thinking": {"type": "disabled"}}
            body["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
            with (
                self.subTest(metadata=metadata),
                patch("src.services.codex_proxy_service.requests.post", return_value=sse_response([])) as post,
            ):
                self.invoke(body, "claude_chat")
            self.assertEqual("stable", post.call_args.kwargs["headers"]["Session-Id"])
            self.assertEqual("stable", post.call_args.kwargs["json"]["prompt_cache_key"])
            self.assertEqual("none", post.call_args.kwargs["json"]["reasoning"]["effort"])
            self.assertFalse(post.call_args.kwargs["json"]["parallel_tool_calls"])
        body = {**request_body("openai_chat"), "reasoning_effort": "high"}
        with patch("src.services.codex_proxy_service.requests.post", return_value=sse_response([])) as post:
            self.invoke(body, "openai_chat")
        self.assertEqual("high", post.call_args.kwargs["json"]["reasoning"]["effort"])

    def test_invalid_encrypted_history_retries_same_account_only_once(self):
        body = request_body("openai_responses")
        body["input"].insert(0, {"type": "reasoning", "id": "rs_bad", "encrypted_content": "bad"})
        invalid = FakeHTTPResponse(
            status_code=400,
            body=b'{"error":{"code":"invalid_encrypted_content","message":"Invalid encrypted reasoning signature"}}',
        )
        with patch("src.services.codex_proxy_service.requests.post", side_effect=[invalid, sse_response([])]) as post:
            self.invoke(body, "openai_responses")
        self.assertEqual(2, post.call_count)
        self.assertTrue(any(item.get("type") == "reasoning" for item in post.call_args_list[0].kwargs["json"]["input"]))
        self.assertFalse(
            any(item.get("type") == "reasoning" for item in post.call_args_list[1].kwargs["json"]["input"])
        )
        self.assertEqual(post.call_args_list[0].kwargs["headers"], post.call_args_list[1].kwargs["headers"])
        with patch(
            "src.services.codex_proxy_service.requests.post",
            return_value=FakeHTTPResponse(
                status_code=400, body=b'{"error":{"message":"Invalid encrypted reasoning signature"}}'
            ),
        ) as post:
            _, status, failure = self.service.proxy_request(body, {}, resolved_target_format="openai_responses")
        self.assertEqual(400, status)
        self.assertIsNotNone(failure)
        self.assertEqual(2, post.call_count)

    def test_compact_returns_entire_canonical_window_and_json_transport(self):
        output = [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "retained"}]},
            {"type": "compaction", "id": "cmp_1", "encrypted_content": "window"},
        ]
        payload = {
            "id": "cmp_response",
            "object": "response.compaction",
            "output": output,
            "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
        }
        upstream = FakeHTTPResponse(status_code=200, body=json.dumps(payload).encode())
        body = {**request_body("openai_responses"), "stream": False, "max_output_tokens": 123}
        completed = []
        with patch("src.services.codex_proxy_service.requests.post", return_value=upstream) as post:
            result = self.invoke(body, "openai_responses", route_name="responses_compact", on_complete=completed.append)
        self.assertEqual(payload, result)
        self.assertEqual(CODEX_BACKEND_RESPONSES_URL + "/compact", post.call_args.args[0])
        self.assertFalse(post.call_args.kwargs["stream"])
        self.assertEqual("application/json", post.call_args.kwargs["headers"]["Accept"])
        self.assertNotIn("stream", post.call_args.kwargs["json"])
        self.assertNotIn("max_output_tokens", post.call_args.kwargs["json"])
        self.assertEqual(["function"], [tool["type"] for tool in post.call_args.kwargs["json"]["tools"]])
        self.assertEqual(16, completed[0]["total_tokens"])
        self.assertTrue(upstream.closed)

    def test_compact_invalid_json_streaming_and_auth_refresh(self):
        for raw in (b"not-json", b'{"output":{}}'):
            with (
                self.subTest(raw=raw),
                patch(
                    "src.services.codex_proxy_service.requests.post",
                    return_value=FakeHTTPResponse(status_code=200, body=raw),
                ),
            ):
                _, status, failure = self.service.proxy_request(
                    request_body("openai_responses"),
                    {},
                    resolved_target_format="openai_responses",
                    route_name="responses_compact",
                )
                self.assertEqual(502, status)
                self.assertEqual("codex_compact_invalid_response", failure.error_code)
        with patch("src.services.codex_proxy_service.requests.post") as post:
            _, status, _ = self.service.proxy_request(
                {**request_body("openai_responses"), "stream": True},
                {},
                resolved_target_format="openai_responses",
                route_name="responses_compact",
            )
            self.assertEqual(400, status)
            post.assert_not_called()
        candidate = self.oauth.iter_auth_candidates_for_model("gpt-5.4")[0]
        candidate = replace(candidate, payload={**candidate.payload, "refresh_token": "refresh"})
        refreshed = replace(candidate, access_token="new-access")
        with (
            patch.object(self.oauth, "iter_auth_candidates_for_model", return_value=[candidate]),
            patch.object(self.oauth, "prepare_auth_candidate_for_use", side_effect=lambda item: item),
            patch.object(self.service, "_refresh_candidate_after_auth_error", return_value=(refreshed, None)),
            patch(
                "src.services.codex_proxy_service.requests.post",
                side_effect=[
                    FakeHTTPResponse(
                        status_code=401, body=b'{"error":{"type":"authentication_error","message":"Expired token"}}'
                    ),
                    FakeHTTPResponse(status_code=200, body=b'{"output":[]}'),
                ],
            ) as post,
        ):
            self.invoke(request_body("openai_responses"), "openai_responses", route_name="responses_compact")
        self.assertEqual(2, post.call_count)
        self.assertEqual("Bearer new-access", post.call_args.kwargs["headers"]["Authorization"])
        self.assertTrue(all(call.args[0].endswith("/compact") for call in post.call_args_list))

    def test_codex_modes_preserve_system_order_and_do_not_affect_generic_translators(self):
        body = {
            "messages": [
                {"role": "user", "content": "one"},
                {"role": "system", "content": "two"},
                {
                    "role": "assistant",
                    "content": "three",
                    "reasoning_details": [{"type": "reasoning", "encrypted_content": "four"}],
                },
            ]
        }
        generic = OpenAIResponsesTranslator().translate_request("model", body, False)
        codex = OpenAIResponsesTranslator(codex_mode=True).translate_request("model", body, False)
        self.assertEqual("two", generic["instructions"])
        self.assertEqual(["user", "developer", None, "assistant"], [item.get("role") for item in codex["input"]])
        payload = {"output": [{"type": "reasoning", "encrypted_content": "opaque"}]}
        generic_claude = OpenAIResponsesClaudeTranslator().translate_nonstream_response("model", {}, {}, payload)
        self.assertFalse(any(block["type"] == "thinking" for block in generic_claude["content"]))
        self.assertIsNone(decode_codex_reasoning("foreign#payload"))
        self.assertIsNone(decode_codex_reasoning("codex#invalid"))

    def test_privacy_preserves_signed_history(self):
        service = CodexProxyService(
            build_context(Path(self.directory.name), FakeConfigManager(oauth_safe_desensitization_enabled=True)),
            self.oauth,
        )
        item = {"type": "reasoning", "id": "rs_keep", "encrypted_content": "signed-user@example.com", "summary": []}
        body = request_body("openai_responses")
        body["input"].insert(0, item)
        body["input"][1]["content"][0]["text"] = "user@example.com"
        with patch("src.services.codex_proxy_service.requests.post", return_value=sse_response([])) as post:
            _, status, failure = service.proxy_request(body, {}, resolved_target_format="openai_responses")
        self.assertEqual(200, status)
        self.assertIsNone(failure)
        self.assertEqual(item, post.call_args.kwargs["json"]["input"][0])
        self.assertNotIn("user@example.com", post.call_args.kwargs["json"]["input"][1]["content"][0]["text"])

    def test_compact_route_enforces_auth_model_permissions_limits_and_records_usage(self):
        self.ctx.config_manager.is_chat_whitelist_enabled = lambda: False
        self.ctx.config_manager.is_api_key_management_enabled = lambda: True
        self.ctx.config_manager.is_real_client_ip_enabled = lambda: False
        self.ctx.config_manager.get_real_client_ip_header = lambda: "X-Forwarded-For"
        allowed = True
        limit_exceeded = False
        api_service = SimpleNamespace(
            extract_api_key_from_headers=lambda headers: headers.get("Authorization", "").removeprefix("Bearer "),
            authenticate_api_key=lambda key: {"id": 9} if key == "valid" else None,
            can_api_key_access_model=lambda *args, **kwargs: allowed,
            is_token_limit_exceeded=lambda key: limit_exceeded,
        )
        logs = RecordingLogService()
        provider = LLMProvider(
            name="demo",
            api="https://example.com/v1/responses",
            model_list=("m1",),
            source_format="openai_responses",
            target_formats=("openai_responses",),
        )
        generic = RecordingProxyService((None, 500, None))
        ProxyController(
            self.ctx,
            generic,
            FakeUserService(),
            logs,
            FakeProviderManager(provider),
            codex_proxy_service=self.service,
            api_key_service=api_service,
        )
        client = self.ctx.flask_app.test_client()
        body = request_body("openai_responses")
        headers = {"Authorization": "Bearer valid"}
        with patch(
            "src.services.codex_proxy_service.requests.post",
            return_value=FakeHTTPResponse(
                status_code=200, body=b'{"output":[],"usage":{"input_tokens":5,"output_tokens":2,"total_tokens":7}}'
            ),
        ) as post:
            self.assertEqual(401, client.post("/v1/responses/compact", json=body).status_code)
            allowed = False
            self.assertEqual(403, client.post("/v1/responses/compact", json=body, headers=headers).status_code)
            allowed = True
            limit_exceeded = True
            self.assertEqual(429, client.post("/v1/responses/compact", json=body, headers=headers).status_code)
            limit_exceeded = False
            self.assertEqual(
                400, client.post("/v1/responses/compact", json={**body, "stream": True}, headers=headers).status_code
            )
            self.assertEqual(
                400,
                client.post("/v1/responses/compact", json={**body, "model": "demo/m1"}, headers=headers).status_code,
            )
            post.assert_not_called()
            with patch.object(self.service, "proxy_request", wraps=self.service.proxy_request) as dispatch:
                response = client.post("/v1/responses/compact", json=body, headers=headers)
            self.assertEqual(200, response.status_code, response.get_data(as_text=True))
            self.assertEqual("api_key:9", dispatch.call_args.kwargs["replay_identity"])
        self.assertIsNone(generic.last_args)
        self.assertEqual(1, len(logs.calls))
        self.assertEqual(7, logs.calls[0]["total_tokens"])
        self.assertEqual(9, logs.calls[0]["api_key_id"])

    def test_compact_quota_fallback_keeps_endpoint_and_session(self):
        write_auth_file(Path(self.directory.name), "second.json", "second-access", mtime=1000)
        with (
            patch(
                "src.services.codex_proxy_service.requests.post",
                side_effect=[
                    FakeHTTPResponse(
                        status_code=429, body=b'{"error":{"type":"usage_limit_reached","message":"Quota exhausted"}}'
                    ),
                    FakeHTTPResponse(status_code=200, body=b'{"output":[]}'),
                ],
            ) as post,
            patch.object(self.oauth, "refresh_auth_file_quota_snapshot", return_value=None),
        ):
            self.invoke(request_body("openai_responses"), "openai_responses", route_name="responses_compact")
        self.assertEqual(2, post.call_count)
        calls = post.call_args_list
        self.assertTrue(all(call.args[0].endswith("/compact") for call in calls))
        self.assertNotEqual(calls[0].kwargs["headers"]["Authorization"], calls[1].kwargs["headers"]["Authorization"])
        self.assertEqual(calls[0].kwargs["headers"]["Session-Id"], calls[1].kwargs["headers"]["Session-Id"])


class CodexSchemaAndCacheTests(unittest.TestCase):
    def test_schema_constraints_business_data_and_namespace_tools(self):
        values = [{"const": index} for index in range(8)]
        schema = {
            "type": "object",
            "properties": {
                "unicode": {"type": "string", "pattern": r"\p{L}+"},
                "nul": {"pattern": r"\0"},
                "literal": {"pattern": r"\\p{L}"},
                "union": {"anyOf": values},
                "constrained": {"oneOf": [{**value, "type": "integer"} for value in values]},
                "compound": {"oneOf": values, "anyOf": [{"type": "integer"}]},
                "intersection": {"oneOf": values, "enum": [1]},
                "numeric_duplicate": {"oneOf": [{"const": number} for number in (0, 0.0, 1, 2, 3, 4, 5, 6)]},
                "nested_duplicate": {"oneOf": [{"const": {"value": number}} for number in (0, 0.0, 1, 2, 3, 4, 5, 6)]},
                "big_integer": {"oneOf": [{"const": 10**40 + index} for index in range(8)]},
                "reserved": {"type": "integer"},
                "business": {"default": {"pattern": r"\p{L}", "$schema": "data"}, "enum": [{"pattern": r"\0"}]},
            },
        }
        original = copy.deepcopy(schema)
        body = {
            "tools": [
                {
                    "type": "namespace",
                    "name": "agents",
                    "tools": [{"type": "function", "name": "wait", "parameters": schema}],
                }
            ]
        }
        CodexRequestCompatibility().prepare(body, "openai_responses")
        props = schema["properties"]
        self.assertNotIn("pattern", props["unicode"])
        self.assertNotIn("pattern", props["nul"])
        self.assertEqual(original["properties"]["literal"], props["literal"])
        self.assertEqual(list(range(8)), props["union"]["enum"])
        self.assertEqual([10**40 + index for index in range(8)], props["big_integer"]["enum"])
        for name in (
            "constrained",
            "compound",
            "intersection",
            "numeric_duplicate",
            "nested_duplicate",
            "reserved",
            "business",
        ):
            self.assertEqual(original["properties"][name], props[name])

    def test_collisions_and_long_signed_ids(self):
        names = ["mcp__" + "a" * 80 + "__read", "mcp__" + "b" * 80 + "__read"]
        body = {
            "tools": [{"type": "function", "name": name} for name in names],
            "tool_choice": {"type": "allowed_tools", "tools": [{"type": "function", "name": names[0]}]},
            "input": [
                {"type": "message", "id": "one"},
                {"type": "message", "id": "msg_one"},
                {"type": "message", "id": "x" * 100},
                {"type": "reasoning", "id": "rs_" + "x" * 100, "encrypted_content": "signed"},
            ],
        }
        compat = CodexRequestCompatibility()
        compat.prepare(body, "openai_responses")
        self.assertEqual(3, len(body["input"]))
        self.assertEqual(3, len({item["id"] for item in body["input"]}))
        self.assertEqual("msg_one", body["input"][1]["id"])
        self.assertEqual(2, len({tool["name"] for tool in body["tools"]}))
        self.assertEqual(body["tools"][0]["name"], body["tool_choice"]["tools"][0]["name"])
        self.assertTrue(all(len(item["id"]) <= 64 for item in body["input"]))
        response = compat.restore_payload(
            {
                "output": [
                    {"type": "function_call", "name": tool["name"], "arguments": json.dumps({"name": tool["name"]})}
                    for tool in body["tools"]
                ]
            }
        )
        self.assertEqual(names, [item["name"] for item in response["output"]])
        self.assertEqual(body["tools"][0]["name"], json.loads(response["output"][0]["arguments"])["name"])

    def test_cache_history_scope_ttl_and_capacity(self):
        cache = CodexReasoningCache()
        body = {
            "instructions": "system",
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        }
        reasoning = {"type": "reasoning", "id": "rs_1", "encrypted_content": "signed"}
        answer = {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "ok", "annotations": [], "logprobs": []}],
        }
        with patch("src.services.codex_reasoning_cache.time.monotonic", return_value=100):
            cache.record("caller:account:model", body, [reasoning, answer])
        history = {
            **body,
            "input": body["input"]
            + [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}],
        }
        with patch("src.services.codex_reasoning_cache.time.monotonic", return_value=101):
            restored = copy.deepcopy(history)
            cache.restore("caller:account:model", restored)
            self.assertEqual(reasoning, restored["input"][1])
            for scope, modified in (
                ("other:account:model", history),
                ("caller:other:model", history),
                ("caller:account:other", history),
                ("caller:account:model", {**history, "instructions": "other"}),
                (
                    "caller:account:model",
                    {
                        **history,
                        "input": [{"type": "message", "role": "user", "content": "different"}, history["input"][1]],
                    },
                ),
            ):
                value = copy.deepcopy(modified)
                cache.restore(scope, value)
                self.assertFalse(any(item.get("type") == "reasoning" for item in value["input"]))
        with patch("src.services.codex_reasoning_cache.time.monotonic", return_value=3700):
            cache.restore("caller:account:model", history)
            self.assertEqual(2, len(history["input"]))
        for index in range(513):
            cache.record(str(index), body, [reasoning, answer])
        self.assertEqual(512, len(cache._entries))
        cache.clear("512")
        self.assertEqual(511, len(cache._entries))


if __name__ == "__main__":
    unittest.main()
