from __future__ import annotations

from typing import Any

from src.hooks import BaseHook, HookContext

from deepseek_openai_compat import DeepSeekReasoningAdapter
from glm_openai_compat import GlmReasoningAdapter
from minimax_openai_compat import MiniMaxReasoningAdapter
from openai_reasoning_common import VendorReasoningAdapter
from qwen_openai_compat import QwenReasoningAdapter


class Hook(BaseHook):
    """按 provider / model 识别厂商并应用对应 OpenAI 兼容参数。"""

    def __init__(self) -> None:
        self._adapters: tuple[VendorReasoningAdapter, ...] = (
            MiniMaxReasoningAdapter(),
            DeepSeekReasoningAdapter(),
            GlmReasoningAdapter(),
            QwenReasoningAdapter(),
        )

    def request_guard(self, ctx: HookContext, body: dict[str, Any]) -> dict[str, Any]:
        for adapter in self._adapters:
            if adapter.matches(ctx, body):
                return adapter.request_guard(ctx, body)
        return body
