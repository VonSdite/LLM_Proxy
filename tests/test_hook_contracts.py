from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.external import LLMProvider
from src.hooks import BaseHook, HookAbortError, HookContext


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


class HookContractsTests(unittest.TestCase):
    def _ctx(
        self,
        *,
        provider_name: str = "demo",
        request_model: str = "demo/model",
        upstream_model: str = "model",
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

    def test_provider_does_not_call_legacy_hook_methods(self) -> None:
        provider = LLMProvider(name="demo", api="https://example.com", hook=cast(Any, LegacyOnlyHook()))
        ctx = self._ctx()
        request_body = {"messages": [{"role": "user", "content": "hello"}]}
        response_body = {"message": "ok"}

        self.assertEqual(request_body, provider.apply_request_guard(ctx, request_body))
        self.assertEqual(response_body, provider.apply_response_guard(ctx, response_body))

    def test_provider_uses_request_and_response_guards(self) -> None:
        provider = LLMProvider(name="demo", api="https://example.com", hook=GuardHook())
        ctx = self._ctx()

        self.assertEqual(True, provider.apply_request_guard(ctx, {"messages": []})["guarded"])
        self.assertEqual(True, provider.apply_response_guard(ctx, {"message": "ok"})["checked"])

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
        with self.assertRaises(HookAbortError) as caught:
            hook.request_guard(ctx, {"messages": [{"role": "user", "content": "[HOOK_ABORT_EXAMPLE]"}]})
        self.assertEqual(400, caught.exception.status_code)
        self.assertEqual("example_hook_abort", caught.exception.error_type)

    def test_minimax_hook_adds_reasoning_split_and_thinking(self) -> None:
        module = self._load_hook_module("minimax_openai_compat.py")
        hook = module.Hook()
        ctx = self._ctx(provider_name="minimax", upstream_model="minimax-m3", stream=True)

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
        ctx = self._ctx(provider_name="minimax", upstream_model="abab6.5s-chat")

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
        ctx = self._ctx(provider_name="deepseek", upstream_model="deepseek-v4-pro")

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
        ctx = self._ctx(provider_name="deepseek", upstream_model="deepseek-reasoner")

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
        ctx = self._ctx(provider_name="zai", upstream_model="glm-4.5")

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
        ctx = self._ctx(provider_name="dashscope", upstream_model="qwen3-coder-plus")

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
        ctx = self._ctx(provider_name="generic", upstream_model="qwen-plus")

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

    def test_reasoning_hooks_ignore_non_openai_chat_upstream(self) -> None:
        module = self._load_hook_module("openai_reasoning_compat.py")
        hook = module.Hook()
        ctx = self._ctx(
            provider_name="dashscope",
            upstream_model="qwen-plus",
            provider_target_format="openai_responses",
        )
        body = {"model": "qwen-plus", "reasoning_effort": "high"}

        self.assertEqual(body, hook.request_guard(ctx, body))


if __name__ == "__main__":
    unittest.main()
