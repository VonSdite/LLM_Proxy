from __future__ import annotations

from typing import Any

from src.hooks import HookContext

from openai_reasoning_common import (
    SingleVendorReasoningHook,
    VendorReasoningAdapter,
    remove_generic_reasoning_fields,
)


class MiniMaxReasoningAdapter(VendorReasoningAdapter):
    """MiniMax M3 OpenAI 兼容参数适配。"""

    match_terms = ("minimax",)
    thinking_control_terms = ("minimax-m3",)

    def apply(
        self,
        ctx: HookContext,
        body: dict[str, Any],
        effort: str | None,
    ) -> dict[str, Any]:
        body["reasoning_split"] = True

        if effort is None:
            return body

        if self.supports_thinking_control(ctx, body):
            remove_generic_reasoning_fields(body, keep_thinking=True)
            body["thinking"] = {"type": "disabled"} if effort == "none" else {"type": "adaptive"}
        else:
            remove_generic_reasoning_fields(body)
        return body


class MiniMaxHook(SingleVendorReasoningHook):
    def __init__(self) -> None:
        super().__init__(MiniMaxReasoningAdapter())


class Hook(MiniMaxHook):
    """MiniMax OpenAI 兼容参数 Hook。"""
