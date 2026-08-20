from __future__ import annotations

from typing import Any

from src.hooks import BaseHook, HookContext

from claude_responses_to_chat_compat_common import (
    VendorReasoningAdapter,
    normalize_openai_chat_message_roles,
)
from claude_responses_to_chat_deepseek_compat import DeepSeekReasoningAdapter
from claude_responses_to_chat_glm_compat import GlmReasoningAdapter
from claude_responses_to_chat_minimax_compat import MiniMaxReasoningAdapter
from claude_responses_to_chat_qwen_compat import QwenReasoningAdapter


class Hook(BaseHook):
    """按上游模型识别厂商并适配 OpenAI Chat 请求字段。"""

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
        return normalize_openai_chat_message_roles(ctx, body)
