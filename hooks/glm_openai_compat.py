from __future__ import annotations

from typing import Any

from src.hooks import HookContext

from openai_reasoning_common import (
    OPENAI_REASONING_FALLBACK_EFFORT,
    SingleVendorReasoningHook,
    VendorReasoningAdapter,
    has_assistant_reasoning_content,
    remove_generic_reasoning_fields,
)


class GlmReasoningAdapter(VendorReasoningAdapter):
    """GLM / Z.AI OpenAI 兼容参数适配。"""

    match_terms = ("glm",)

    def apply(
        self,
        ctx: HookContext,
        body: dict[str, Any],
        effort: str | None,
    ) -> dict[str, Any]:
        del ctx
        if effort is None:
            return body

        remove_generic_reasoning_fields(body, keep_reasoning_effort=True, keep_thinking=True)
        if effort in {"none", "minimal"}:
            body["thinking"] = {"type": "disabled"}
            body.pop("reasoning_effort", None)
        else:
            thinking = {"type": "enabled"}
            if has_assistant_reasoning_content(body):
                thinking["clear_thinking"] = False
            body["thinking"] = thinking
            body["reasoning_effort"] = "max" if effort == OPENAI_REASONING_FALLBACK_EFFORT else "high"
        return body


class GlmHook(SingleVendorReasoningHook):
    def __init__(self) -> None:
        super().__init__(GlmReasoningAdapter())


class Hook(GlmHook):
    """GLM / Z.AI OpenAI 兼容参数 Hook。"""
