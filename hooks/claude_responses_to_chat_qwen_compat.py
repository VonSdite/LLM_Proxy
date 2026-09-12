from __future__ import annotations

from typing import Any

from src.hooks import HookContext

from claude_responses_to_chat_compat_common import (
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
    """Qwen / DashScope OpenAI Chat 兼容参数适配。"""

    match_terms = ("qwen",)

    def apply(
        self,
        ctx: HookContext,
        body: dict[str, Any],
        effort: str | None,
    ) -> dict[str, Any]:
        if _is_qwen_vl_responses_request(ctx, body):
            _normalize_qwen_vl_instruction_messages(body)
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


def _is_qwen_vl_responses_request(ctx: HookContext, body: dict[str, Any]) -> bool:
    """判断请求是否为 Responses 转入 Qwen VL Chat 上游。"""
    target_format = str(ctx.provider_target_format or "").strip().lower()
    model_text = " ".join(
        str(value or "").strip().lower()
        for value in (
            ctx.upstream_model,
            body.get("model"),
        )
    )
    return target_format == "openai_responses" and "qwen3.6-27b-vl" in model_text


def _normalize_qwen_vl_instruction_messages(body: dict[str, Any]) -> None:
    """把 Qwen VL 的连续纯文本 system 消息压平并合并。"""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return

    normalized_messages: list[Any] = []
    for message in messages:
        if not isinstance(message, dict) or str(message.get("role") or "").strip().lower() != "system":
            normalized_messages.append(message)
            continue

        flattened_content = _flatten_chat_text_content(message.get("content"))
        if flattened_content is None:
            normalized_messages.append(message)
            continue

        normalized_message = dict(message)
        normalized_message["content"] = flattened_content
        if (
            set(normalized_message) <= {"role", "content"}
            and normalized_messages
            and isinstance(normalized_messages[-1], dict)
            and set(normalized_messages[-1]) <= {"role", "content"}
            and str(normalized_messages[-1].get("role") or "").strip().lower() == "system"
            and isinstance(normalized_messages[-1].get("content"), str)
        ):
            previous_message = dict(normalized_messages[-1])
            previous_message["content"] = "\n\n".join(
                part for part in (previous_message["content"], flattened_content) if part
            )
            normalized_messages[-1] = previous_message
        else:
            normalized_messages.append(normalized_message)

    body["messages"] = normalized_messages


def _flatten_chat_text_content(content: Any) -> str | None:
    """把纯文本 Chat 内容块压平成字符串，其他内容保持原结构。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None

    text_parts: list[str] = []
    for part in content:
        if (
            not isinstance(part, dict)
            or str(part.get("type") or "").strip().lower() != "text"
            or not isinstance(part.get("text"), str)
        ):
            return None
        text_parts.append(part["text"])
    return "\n\n".join(text_parts)


class QwenHook(SingleVendorReasoningHook):
    def __init__(self) -> None:
        super().__init__(QwenReasoningAdapter())


class Hook(QwenHook):
    """Qwen / DashScope OpenAI Chat 兼容参数 Hook。"""
