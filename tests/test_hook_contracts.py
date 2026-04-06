import sys
import unittest
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.external import LLMProvider
from src.hooks import BaseHook, HookContext


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
    def _ctx(self) -> HookContext:
        return HookContext(
            retry=0,
            root_path=Path(__file__).resolve().parents[1],
            logger=FakeLogger(),
            provider_name="demo",
            request_model="demo/model",
            upstream_model="model",
        )

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


if __name__ == "__main__":
    unittest.main()
