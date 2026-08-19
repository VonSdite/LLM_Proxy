from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.external import LLMProvider
from src.hooks import BaseHook, HookAbortError, HookContext
from src.proxy_core import DownstreamChunk


class FakeLogger:
    def info(self, msg: object, *args: object, **kwargs: object) -> None:
        del msg, args, kwargs

    def warning(self, msg: object, *args: object, **kwargs: object) -> None:
        del msg, args, kwargs

    def error(self, msg: object, *args: object, **kwargs: object) -> None:
        del msg, args, kwargs

    def debug(self, msg: object, *args: object, **kwargs: object) -> None:
        del msg, args, kwargs


class LegacyOnlyHook:
    def input_body_hook(self, ctx: HookContext, body: dict) -> dict:
        del ctx, body
        raise AssertionError("legacy input_body_hook should not be called")

    def output_body_hook(self, ctx: HookContext, body):
        del ctx, body
        raise AssertionError("legacy output_body_hook should not be called")


class GuardHook(BaseHook):
    def request_guard(self, ctx: HookContext, body: dict) -> dict:
        del ctx
        guarded = dict(body)
        guarded["guarded"] = True
        return guarded

    def response_guard(self, ctx: HookContext, body):
        del ctx
        guarded = dict(body)
        guarded["checked"] = True
        return guarded


class NoneReturningGuard(BaseHook):
    def request_guard(self, ctx: HookContext, body: dict):
        del ctx, body
        return None

    def response_guard(self, ctx: HookContext, body):
        del ctx, body
        return None


class ModelFetchHook(BaseHook):
    def fetch_models(self, ctx: HookContext, payload: dict[str, Any]) -> list[str]:
        return [ctx.provider_name, str(payload["api"])]


class HookContractsTests(unittest.TestCase):
    def _ctx(
        self,
        *,
        provider_name: str = "demo",
        request_model: str = "demo/model",
        upstream_model: str = "model",
        provider_source_format: str = "openai_chat",
        provider_target_format: str = "openai_chat",
        stream: bool = False,
    ) -> HookContext:
        return HookContext(
            retry=0,
            root_path=Path(__file__).resolve().parents[1],
            logger=FakeLogger(),
            provider_name=provider_name,
            request_model=request_model,
            upstream_model=upstream_model,
            provider_source_format=provider_source_format,
            provider_target_format=provider_target_format,
            stream=stream,
        )

    def _load_hook_module(self, file_name: str):
        hook_dir = Path(__file__).resolve().parents[1] / "hooks"
        hook_path = hook_dir / file_name
        sys.path.insert(0, str(hook_dir))
        try:
            spec = importlib.util.spec_from_file_location(f"{file_name}_under_test", hook_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        finally:
            try:
                sys.path.remove(str(hook_dir))
            except ValueError:
                pass

    def test_hook_context_previous_failure_fields_default_to_none(self) -> None:
        ctx = self._ctx()

        self.assertIsNone(ctx.last_status_code)
        self.assertIsNone(ctx.last_error_type)

    def test_base_hook_default_guards_are_noop(self) -> None:
        hook = BaseHook()
        ctx = self._ctx()
        request_body = {"messages": [{"role": "user", "content": "hello"}]}
        response_body = {"message": "ok"}

        self.assertEqual(request_body, hook.request_guard(ctx, request_body))
        self.assertEqual(response_body, hook.response_guard(ctx, response_body))
        self.assertIsNone(hook.fetch_models(ctx, {"api": "https://example.com"}))

    def test_provider_does_not_call_legacy_hook_methods(self) -> None:
        provider = LLMProvider(name="demo", api="https://example.com", hook=cast(Any, LegacyOnlyHook()))
        ctx = self._ctx()
        request_body = {"messages": [{"role": "user", "content": "hello"}]}
        response_body = {"message": "ok"}

        self.assertEqual(request_body, provider.apply_request_guard(ctx, request_body))
        self.assertEqual(response_body, provider.apply_response_guard(ctx, response_body))
        self.assertIsNone(provider.apply_fetch_models_hook(ctx, {"api": "https://example.com"}))

    def test_provider_uses_request_and_response_guards(self) -> None:
        provider = LLMProvider(name="demo", api="https://example.com", hook=GuardHook())
        ctx = self._ctx()

        self.assertEqual(True, provider.apply_request_guard(ctx, {"messages": []})["guarded"])
        self.assertEqual(True, provider.apply_response_guard(ctx, {"message": "ok"})["checked"])

    def test_provider_uses_fetch_models_hook(self) -> None:
        provider = LLMProvider(name="demo", api="https://example.com", hook=ModelFetchHook())
        ctx = self._ctx(provider_name="demo")

        self.assertEqual(
            ["demo", "https://example.com"],
            provider.apply_fetch_models_hook(ctx, {"api": "https://example.com"}),
        )

    def test_none_from_guard_keeps_original_body(self) -> None:
        provider = LLMProvider(name="demo", api="https://example.com", hook=NoneReturningGuard())
        ctx = self._ctx()
        request_body: dict[str, Any] = {"messages": []}
        response_body = {"message": "ok"}

        self.assertEqual(request_body, provider.apply_request_guard(ctx, request_body))
        self.assertEqual(response_body, provider.apply_response_guard(ctx, response_body))

    def test_example_hook_demonstrates_hook_abort_error(self) -> None:
        hook_path = Path(__file__).resolve().parents[1] / "hooks" / "example_hook.py"
        spec = importlib.util.spec_from_file_location("example_hook_under_test", hook_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        hook = module.Hook()
        ctx = self._ctx()

        request_body = {"messages": [{"role": "user", "content": "hello"}]}
        rewritten_body = hook.request_guard(ctx, request_body)

        self.assertEqual("[PREFIX] hello", rewritten_body["messages"][0]["content"])
        self.assertIsNone(
            hook.fetch_models(
                ctx,
                {
                    "api": "https://example.com/v1/chat/completions",
                    "candidate_urls": ["https://example.com/v1/models"],
                },
            )
        )
        with self.assertRaises(HookAbortError) as caught:
            hook.request_guard(ctx, {"messages": [{"role": "user", "content": "[HOOK_ABORT_EXAMPLE]"}]})
        self.assertEqual(400, caught.exception.status_code)
        self.assertEqual("example_hook_abort", caught.exception.error_type)

    def test_minimax_hook_adds_reasoning_split_and_thinking(self) -> None:
        module = self._load_hook_module("minimax_openai_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="minimax",
            upstream_model="minimax-m3",
            provider_target_format="claude_chat",
            stream=True,
        )

        rewritten = hook.request_guard(
            ctx,
            {
                "model": "minimax-m3",
                "messages": [],
                "stream": True,
                "reasoning_effort": "high",
            },
        )

        self.assertEqual(True, rewritten["reasoning_split"])
        self.assertEqual({"type": "adaptive"}, rewritten["thinking"])
        self.assertNotIn("stream_options", rewritten)
        self.assertNotIn("reasoning_effort", rewritten)

    def test_minimax_hook_keeps_non_m3_thinking_control_off(self) -> None:
        module = self._load_hook_module("minimax_openai_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="minimax",
            upstream_model="abab6.5s-chat",
            provider_target_format="claude_chat",
        )

        rewritten = hook.request_guard(
            ctx,
            {
                "model": "abab6.5s-chat",
                "messages": [],
                "reasoning_effort": "none",
            },
        )

        self.assertEqual(True, rewritten["reasoning_split"])
        self.assertNotIn("thinking", rewritten)
        self.assertNotIn("reasoning_effort", rewritten)

    def test_deepseek_hook_maps_xhigh_to_max(self) -> None:
        module = self._load_hook_module("deepseek_openai_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="deepseek",
            upstream_model="deepseek-v4-pro",
            provider_target_format="claude_chat",
        )

        rewritten = hook.request_guard(
            ctx,
            {
                "model": "deepseek-v4-pro",
                "messages": [],
                "reasoning_effort": "xhigh",
            },
        )

        self.assertEqual({"type": "enabled"}, rewritten["thinking"])
        self.assertEqual("max", rewritten["reasoning_effort"])

    def test_deepseek_hook_keeps_compat_model_thinking_control_off(self) -> None:
        module = self._load_hook_module("deepseek_openai_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="deepseek",
            upstream_model="deepseek-reasoner",
            provider_target_format="claude_chat",
        )

        rewritten = hook.request_guard(
            ctx,
            {
                "model": "deepseek-reasoner",
                "messages": [],
                "reasoning_effort": "high",
            },
        )

        self.assertNotIn("thinking", rewritten)
        self.assertNotIn("reasoning_effort", rewritten)

    def test_glm_hook_sets_preserved_thinking_when_reasoning_history_exists(self) -> None:
        module = self._load_hook_module("glm_openai_compat.py")
        hook = module.Hook()
        ctx = self._ctx(provider_name="zai", upstream_model="glm-4.5", provider_target_format="claude_chat")

        rewritten = hook.request_guard(
            ctx,
            {
                "model": "glm-4.5",
                "messages": [{"role": "assistant", "content": "", "reasoning_content": "plan"}],
                "reasoning_effort": "medium",
            },
        )

        self.assertEqual({"type": "enabled", "clear_thinking": False}, rewritten["thinking"])
        self.assertEqual("high", rewritten["reasoning_effort"])

    def test_qwen_hook_maps_budget_and_keeps_vendor_parameters(self) -> None:
        module = self._load_hook_module("qwen_openai_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="dashscope",
            upstream_model="qwen3-coder-plus",
            provider_target_format="claude_chat",
        )

        rewritten = hook.request_guard(
            ctx,
            {
                "model": "qwen3-coder-plus",
                "messages": [{"role": "assistant", "content": "", "reasoning_content": "plan"}],
                "reasoning_effort": "medium",
                "top_k": 20,
            },
        )

        self.assertEqual(True, rewritten["enable_thinking"])
        self.assertEqual(4096, rewritten["thinking_budget"])
        self.assertEqual(True, rewritten["preserve_thinking"])
        self.assertEqual(20, rewritten["top_k"])
        self.assertNotIn("reasoning_effort", rewritten)

    def test_aggregate_reasoning_hook_dispatches_by_model(self) -> None:
        module = self._load_hook_module("openai_reasoning_compat.py")
        hook = module.Hook()
        ctx = self._ctx(provider_name="generic", upstream_model="qwen-plus", provider_target_format="claude_chat")

        rewritten = hook.request_guard(
            ctx,
            {
                "model": "qwen-plus",
                "messages": [],
                "reasoning": {"effort": "low"},
            },
        )

        self.assertEqual(True, rewritten["enable_thinking"])
        self.assertEqual(2048, rewritten["thinking_budget"])
        self.assertNotIn("reasoning", rewritten)

    def test_aggregate_reasoning_hook_does_not_match_provider_name(self) -> None:
        module = self._load_hook_module("openai_reasoning_compat.py")
        hook = module.Hook()
        ctx = self._ctx(provider_name="dashscope", upstream_model="plain-model")
        body = {"model": "plain-model", "messages": [], "reasoning_effort": "high"}

        self.assertEqual(body, hook.request_guard(ctx, body))

    def test_reasoning_hooks_follow_upstream_format_when_downstream_is_claude(self) -> None:
        module = self._load_hook_module("openai_reasoning_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="dashscope",
            upstream_model="qwen-plus",
            provider_source_format="openai_chat",
            provider_target_format="claude_chat",
        )
        body = {"model": "qwen-plus", "reasoning_effort": "high"}

        rewritten = hook.request_guard(ctx, body)

        self.assertEqual(True, rewritten["enable_thinking"])
        self.assertEqual(8192, rewritten["thinking_budget"])

    def test_reasoning_hooks_ignore_openai_chat_to_openai_chat(self) -> None:
        module = self._load_hook_module("openai_reasoning_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="dashscope",
            upstream_model="qwen-plus",
            provider_source_format="openai_chat",
            provider_target_format="openai_chat",
        )
        body = {"model": "qwen-plus", "reasoning_effort": "high"}

        self.assertEqual(body, hook.request_guard(ctx, body))

    def test_reasoning_hooks_ignore_non_openai_chat_upstream(self) -> None:
        module = self._load_hook_module("openai_reasoning_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="dashscope",
            upstream_model="qwen-plus",
            provider_source_format="claude_chat",
            provider_target_format="openai_chat",
        )
        body = {"model": "qwen-plus", "reasoning_effort": "high"}

        self.assertEqual(body, hook.request_guard(ctx, body))

    def test_responses_legacy_tools_hook_downgrades_namespaces_custom_tools_and_history(self) -> None:
        module = self._load_hook_module("responses_legacy_tools_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="legacy-responses",
            provider_source_format="openai_responses",
            provider_target_format="openai_responses",
        )

        rewritten = hook.request_guard(
            ctx,
            {
                "model": "gpt-5",
                "input": [
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_1",
                        "namespace": "ops",
                        "name": "shell",
                        "input": "pwd",
                    },
                    {"type": "custom_tool_call_output", "call_id": "call_1", "output": "ok"},
                    {
                        "type": "additional_tools",
                        "tools": [{"type": "custom", "name": "apply_patch", "description": "Apply patch"}],
                    },
                ],
                "tools": [
                    {"type": "function", "name": "lookup", "parameters": {"type": "object"}},
                    {
                        "type": "namespace",
                        "name": "ops",
                        "tools": [
                            {"type": "custom", "name": "shell", "description": "Run command"},
                            {"type": "function", "name": "read", "parameters": {"type": "object"}},
                        ],
                    },
                    {"type": "web_search"},
                ],
            },
        )

        self.assertEqual(
            ["function", "function", "function", "function"], [tool["type"] for tool in rewritten["tools"]]
        )
        self.assertEqual(
            ["lookup", "ops__shell", "ops__read", "apply_patch"],
            [tool["name"] for tool in rewritten["tools"]],
        )
        self.assertEqual("string", rewritten["tools"][1]["parameters"]["properties"]["input"]["type"])
        self.assertFalse(any(item.get("type") == "additional_tools" for item in rewritten["input"]))
        self.assertEqual("function_call", rewritten["input"][0]["type"])
        self.assertEqual("ops__shell", rewritten["input"][0]["name"])
        self.assertEqual('{"input":"pwd"}', rewritten["input"][0]["arguments"])
        self.assertEqual("function_call_output", rewritten["input"][1]["type"])

    def test_responses_legacy_tools_hook_downgrades_allowed_tools_choice(self) -> None:
        module = self._load_hook_module("responses_legacy_tools_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_source_format="openai_responses",
            provider_target_format="openai_responses",
        )

        rewritten = hook.request_guard(
            ctx,
            {
                "tools": [
                    {"type": "function", "name": "lookup", "parameters": {"type": "object"}},
                    {
                        "type": "namespace",
                        "name": "ops",
                        "tools": [{"type": "custom", "name": "shell"}],
                    },
                ],
                "tool_choice": {
                    "type": "allowed_tools",
                    "mode": "required",
                    "tools": [{"type": "custom", "namespace": "ops", "name": "shell"}],
                },
            },
        )

        self.assertEqual("required", rewritten["tool_choice"])
        self.assertEqual(["ops__shell"], [tool["name"] for tool in rewritten["tools"]])

    def test_responses_legacy_tools_hook_avoids_alias_collisions_and_long_names(self) -> None:
        module = self._load_hook_module("responses_legacy_tools_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_source_format="openai_responses",
            provider_target_format="openai_responses",
        )
        long_name = "long_tool_" + "x" * 80

        rewritten = hook.request_guard(
            ctx,
            {
                "tools": [
                    {"type": "function", "name": "ops__shell", "parameters": {"type": "object"}},
                    {
                        "type": "namespace",
                        "name": "ops",
                        "tools": [{"type": "custom", "name": "shell"}],
                    },
                    {"type": "custom", "name": long_name},
                ]
            },
        )

        aliases = [tool["name"] for tool in rewritten["tools"]]
        self.assertEqual(3, len(set(aliases)))
        self.assertTrue(all(len(alias) <= 64 for alias in aliases))
        self.assertNotEqual("ops__shell", aliases[1])

        restored = hook.response_guard(
            ctx,
            {
                "output": [
                    {
                        "id": "fc_1",
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": aliases[1],
                        "arguments": '{"input":"pwd"}',
                    }
                ]
            },
        )
        self.assertEqual("ops", restored["output"][0]["namespace"])
        self.assertEqual("shell", restored["output"][0]["name"])

    def test_responses_legacy_tools_hook_restores_nonstream_tool_calls(self) -> None:
        module = self._load_hook_module("responses_legacy_tools_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_source_format="openai_responses",
            provider_target_format="openai_responses",
        )
        hook.request_guard(
            ctx,
            {
                "tools": [
                    {
                        "type": "namespace",
                        "name": "ops",
                        "tools": [
                            {"type": "custom", "name": "shell"},
                            {"type": "function", "name": "read"},
                        ],
                    }
                ]
            },
        )

        restored = hook.response_guard(
            ctx,
            {
                "id": "resp_1",
                "object": "response",
                "output": [
                    {
                        "id": "fc_1",
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "ops__shell",
                        "arguments": '{"input":"pwd"}',
                    },
                    {
                        "id": "fc_2",
                        "type": "function_call",
                        "call_id": "call_2",
                        "name": "ops__read",
                        "arguments": '{"path":"README.md"}',
                    },
                ],
            },
        )

        self.assertEqual("custom_tool_call", restored["output"][0]["type"])
        self.assertEqual("ops", restored["output"][0]["namespace"])
        self.assertEqual("shell", restored["output"][0]["name"])
        self.assertEqual("pwd", restored["output"][0]["input"])
        self.assertEqual("function_call", restored["output"][1]["type"])
        self.assertEqual("ops", restored["output"][1]["namespace"])
        self.assertEqual("read", restored["output"][1]["name"])

    def test_responses_legacy_tools_hook_restores_stream_custom_tool_events(self) -> None:
        module = self._load_hook_module("responses_legacy_tools_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_source_format="openai_responses",
            provider_target_format="openai_responses",
            stream=True,
        )
        hook.request_guard(
            ctx,
            {
                "tools": [
                    {
                        "type": "namespace",
                        "name": "ops",
                        "tools": [{"type": "custom", "name": "shell"}],
                    }
                ]
            },
        )

        added = hook.response_guard(
            ctx,
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "ops__shell",
                    "arguments": "",
                },
            },
        )
        done = hook.response_guard(
            ctx,
            {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_1",
                "output_index": 0,
                "arguments": '{"input":"pwd"}',
            },
        )

        self.assertEqual("custom_tool_call", added["item"]["type"])
        self.assertEqual("ops", added["item"]["namespace"])
        self.assertIsInstance(done, DownstreamChunk)
        self.assertEqual("response.custom_tool_call_input.done", done.event)
        self.assertEqual("pwd", done.payload["input"])

    def test_responses_legacy_tools_hook_ignores_cross_protocol_requests(self) -> None:
        module = self._load_hook_module("responses_legacy_tools_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_source_format="openai_chat",
            provider_target_format="openai_responses",
        )
        body = {"tools": [{"type": "namespace", "name": "ops", "tools": []}]}

        self.assertEqual(body, hook.request_guard(ctx, body))


if __name__ == "__main__":
    unittest.main()
