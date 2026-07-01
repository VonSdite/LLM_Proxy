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

_THINKING_BUDGET_BY_EFFORT = {
    "minimal": 1024,
    "low": 2048,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
}


class QwenReasoningAdapter(VendorReasoningAdapter):
    """Qwen / DashScope OpenAI 兼容参数适配。"""

    match_terms = ("qwen",)

    def apply(
        self,
        ctx: HookContext,
        body: dict[str, Any],
        effort: str | None,
    ) -> dict[str, Any]:
        del ctx
        if effort is None:
            return body

        remove_generic_reasoning_fields(body)
        if effort == "none":
            body["enable_thinking"] = False
            body.pop("thinking_budget", None)
            body.pop("preserve_thinking", None)
        else:
            body["enable_thinking"] = True
            if body.get("thinking_budget") is None:
                body["thinking_budget"] = _THINKING_BUDGET_BY_EFFORT.get(
                    effort,
                    _THINKING_BUDGET_BY_EFFORT[OPENAI_REASONING_FALLBACK_EFFORT],
                )
            if has_assistant_reasoning_content(body) and body.get("preserve_thinking") is None:
                body["preserve_thinking"] = True
        return body


class QwenHook(SingleVendorReasoningHook):
    def __init__(self) -> None:
        super().__init__(QwenReasoningAdapter())


class Hook(QwenHook):
    """Qwen / DashScope OpenAI 兼容参数 Hook。"""
