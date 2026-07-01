from __future__ import annotations

from typing import Any

from src.hooks import HookContext

from openai_reasoning_common import (
    OPENAI_REASONING_FALLBACK_EFFORT,
    SingleVendorReasoningHook,
    VendorReasoningAdapter,
    remove_generic_reasoning_fields,
)


class DeepSeekReasoningAdapter(VendorReasoningAdapter):
    """DeepSeek OpenAI 兼容参数适配。"""

    match_terms = ("deepseek",)
    thinking_control_terms = ("deepseek-v4",)

    def apply(
        self,
        ctx: HookContext,
        body: dict[str, Any],
        effort: str | None,
    ) -> dict[str, Any]:
        if effort is None:
            return body

        if not self.supports_thinking_control(ctx, body):
            remove_generic_reasoning_fields(body)
            return body

        remove_generic_reasoning_fields(body, keep_reasoning_effort=True, keep_thinking=True)
        if effort == "none":
            body["thinking"] = {"type": "disabled"}
            body.pop("reasoning_effort", None)
        else:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = "max" if effort == OPENAI_REASONING_FALLBACK_EFFORT else "high"
        return body


class DeepSeekHook(SingleVendorReasoningHook):
    def __init__(self) -> None:
        super().__init__(DeepSeekReasoningAdapter())


class Hook(DeepSeekHook):
    """DeepSeek OpenAI 兼容参数 Hook。"""
