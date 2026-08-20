from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.external import LLMProvider
from src.hooks import BaseHook, HookAbortError, HookContext
from src.translators import build_default_translator_registry


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
        module = self._load_hook_module("claude_responses_to_chat_minimax_compat.py")
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
        module = self._load_hook_module("claude_responses_to_chat_minimax_compat.py")
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
        module = self._load_hook_module("claude_responses_to_chat_deepseek_compat.py")
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
        module = self._load_hook_module("claude_responses_to_chat_deepseek_compat.py")
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
        module = self._load_hook_module("claude_responses_to_chat_glm_compat.py")
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
        module = self._load_hook_module("claude_responses_to_chat_qwen_compat.py")
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

    def test_vendor_hooks_map_developer_messages_without_reasoning_parameters(self) -> None:
        cases = (
            ("claude_responses_to_chat_minimax_compat.py", "minimax-m3"),
            ("claude_responses_to_chat_deepseek_compat.py", "deepseek-v4-pro"),
            ("claude_responses_to_chat_glm_compat.py", "glm-4.5"),
            ("claude_responses_to_chat_qwen_compat.py", "qwen-plus"),
            ("claude_responses_to_chat_compat.py", "deepseek-v4-pro"),
        )
        for hook_file, upstream_model in cases:
            with self.subTest(hook_file=hook_file):
                module = self._load_hook_module(hook_file)
                hook = module.Hook()
                ctx = self._ctx(
                    upstream_model=upstream_model,
                    provider_source_format="openai_chat",
                    provider_target_format="openai_responses",
                )
                body = {
                    "model": upstream_model,
                    "messages": [
                        {"role": "developer", "content": "Follow project rules", "name": "policy"},
                        {"role": "user", "content": "Hello"},
                    ],
                }

                rewritten = hook.request_guard(ctx, body)

                self.assertEqual(["system", "user"], [message["role"] for message in rewritten["messages"]])
                self.assertEqual("policy", rewritten["messages"][0]["name"])
                self.assertEqual("developer", body["messages"][0]["role"])

    def test_aggregate_openai_chat_hook_dispatches_by_model(self) -> None:
        module = self._load_hook_module("claude_responses_to_chat_compat.py")
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

    def test_aggregate_hook_applies_common_roles_without_vendor_reasoning(self) -> None:
        module = self._load_hook_module("claude_responses_to_chat_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="dashscope",
            upstream_model="plain-model",
            provider_target_format="openai_responses",
        )
        body = {
            "model": "plain-model",
            "messages": [{"role": "developer", "content": "Keep this role"}],
            "reasoning_effort": "high",
        }

        rewritten = hook.request_guard(ctx, body)

        self.assertEqual("system", rewritten["messages"][0]["role"])
        self.assertEqual("high", rewritten["reasoning_effort"])
        self.assertNotIn("thinking", rewritten)
        self.assertEqual("developer", body["messages"][0]["role"])

    def test_openai_chat_hooks_follow_upstream_format_when_downstream_is_claude(self) -> None:
        module = self._load_hook_module("claude_responses_to_chat_compat.py")
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

    def test_openai_chat_hooks_map_roles_without_adapting_chat_reasoning(self) -> None:
        module = self._load_hook_module("claude_responses_to_chat_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="dashscope",
            upstream_model="qwen-plus",
            provider_source_format="openai_chat",
            provider_target_format="openai_chat",
        )
        body = {
            "model": "qwen-plus",
            "messages": [{"role": "developer", "content": "Keep this role"}],
            "reasoning_effort": "high",
        }

        rewritten = hook.request_guard(ctx, body)

        self.assertEqual("system", rewritten["messages"][0]["role"])
        self.assertEqual("high", rewritten["reasoning_effort"])
        self.assertNotIn("enable_thinking", rewritten)
        self.assertEqual("developer", body["messages"][0]["role"])

    def test_openai_chat_hooks_ignore_non_openai_chat_upstream(self) -> None:
        module = self._load_hook_module("claude_responses_to_chat_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="dashscope",
            upstream_model="qwen-plus",
            provider_source_format="claude_chat",
            provider_target_format="openai_chat",
        )
        body = {"model": "qwen-plus", "reasoning_effort": "high"}

        self.assertEqual(body, hook.request_guard(ctx, body))

    def test_responses_upstream_hook_normalizes_developer_for_all_downstream_formats(self) -> None:
        module = self._load_hook_module("responses_upstream_compat.py")
        hook = module.Hook()
        registry = build_default_translator_registry()

        responses_body = registry.get("openai_responses", "openai_responses").translate_request(
            "upstream-model",
            {
                "input": [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "Follow project rules"}],
                        "id": "msg_policy",
                    },
                    {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
                ],
                "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
            },
            False,
        )
        responses_ctx = self._ctx(
            provider_source_format="openai_responses",
            provider_target_format="openai_responses",
        )
        rewritten_responses = hook.request_guard(responses_ctx, responses_body)

        self.assertEqual("system", rewritten_responses["input"][0]["role"])
        self.assertEqual("msg_policy", rewritten_responses["input"][0]["id"])
        self.assertEqual("function_call_output", rewritten_responses["input"][1]["type"])
        self.assertEqual("function", rewritten_responses["tools"][0]["type"])
        self.assertEqual("lookup", rewritten_responses["tools"][0]["name"])
        self.assertEqual("developer", responses_body["input"][0]["role"])

        claude_body = registry.get("openai_responses", "claude_chat").translate_request(
            "upstream-model",
            {
                "system": [
                    {
                        "type": "text",
                        "text": "Follow project rules",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "messages": [{"role": "user", "content": "Hello"}],
            },
            False,
        )
        claude_ctx = self._ctx(
            provider_source_format="openai_responses",
            provider_target_format="claude_chat",
        )
        rewritten_claude = hook.request_guard(claude_ctx, claude_body)

        self.assertEqual("system", rewritten_claude["input"][0]["role"])
        self.assertNotIn("cache_control", rewritten_claude["input"][0]["content"][0])
        self.assertEqual("developer", claude_body["input"][0]["role"])

        chat_body = registry.get("openai_responses", "openai_chat").translate_request(
            "upstream-model",
            {
                "messages": [
                    {"role": "developer", "content": "Follow project rules"},
                    {"role": "user", "content": "Hello"},
                ]
            },
            False,
        )
        chat_ctx = self._ctx(
            provider_source_format="openai_responses",
            provider_target_format="openai_chat",
        )
        rewritten_chat = hook.request_guard(chat_ctx, chat_body)

        self.assertEqual("Follow project rules", rewritten_chat["instructions"])
        self.assertFalse(any(item.get("role") == "developer" for item in rewritten_chat["input"]))

    def test_responses_upstream_hook_downgrades_namespace_tools(self) -> None:
        module = self._load_hook_module("responses_upstream_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_source_format="openai_responses",
            provider_target_format="openai_responses",
        )

        rewritten = hook.request_guard(
            ctx,
            {
                "tools": [
                    {
                        "type": "namespace",
                        "name": "ops",
                        "tools": [
                            {"type": "custom", "name": "shell", "description": "Run command"},
                            {"type": "function", "name": "read", "parameters": {"type": "object"}},
                        ],
                    }
                ],
                "input": [
                    {
                        "type": "custom_tool_call",
                        "namespace": "ops",
                        "name": "shell",
                        "input": "pwd",
                    }
                ],
            },
        )

        self.assertEqual(["function", "function"], [tool["type"] for tool in rewritten["tools"]])
        self.assertEqual(["ops__shell", "ops__read"], [tool["name"] for tool in rewritten["tools"]])
        self.assertEqual("function_call", rewritten["input"][0]["type"])
        self.assertEqual("ops__shell", rewritten["input"][0]["name"])
        self.assertEqual('{"input":"pwd"}', rewritten["input"][0]["arguments"])

    def test_responses_upstream_hook_restores_namespace_and_custom_tool_response(self) -> None:
        module = self._load_hook_module("responses_upstream_compat.py")
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
                        "tools": [{"type": "custom", "name": "shell"}],
                    }
                ]
            },
        )

        restored = hook.response_guard(
            ctx,
            {
                "output": [
                    {
                        "id": "fc_1",
                        "type": "function_call",
                        "name": "ops__shell",
                        "arguments": '{"input":"pwd"}',
                    }
                ]
            },
        )

        self.assertEqual("custom_tool_call", restored["output"][0]["type"])
        self.assertEqual("ops", restored["output"][0]["namespace"])
        self.assertEqual("shell", restored["output"][0]["name"])
        self.assertEqual("pwd", restored["output"][0]["input"])

    def test_responses_upstream_hook_ignores_other_upstreams_and_string_input(self) -> None:
        module = self._load_hook_module("responses_upstream_compat.py")
        hook = module.Hook()
        responses_ctx = self._ctx(provider_source_format="openai_responses")
        chat_ctx = self._ctx(provider_source_format="openai_chat")
        string_body = {"input": "Hello"}
        message_body = {"input": [{"type": "message", "role": "developer", "content": []}]}

        self.assertIs(string_body, hook.request_guard(responses_ctx, string_body))
        self.assertIs(message_body, hook.request_guard(chat_ctx, message_body))


if __name__ == "__main__":
    unittest.main()
