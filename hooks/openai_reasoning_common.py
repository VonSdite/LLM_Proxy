from __future__ import annotations

from typing import Any

from src.hooks import BaseHook, HookContext
from src.translators.reasoning_utils import (
    OPENAI_REASONING_FALLBACK_EFFORT,
    normalize_openai_reasoning_effort,
    openai_reasoning_effort_from_claude_thinking,
    openai_reasoning_effort_from_responses_reasoning,
)


class VendorReasoningAdapter:
    """把统一思考意图改写成单个厂商的 OpenAI 兼容参数。"""

    # 子类填写上游模型关键词，汇总 Hook 用这些关键词选择对应处理器。
    match_terms: tuple[str, ...] = ()
    # 子类填写支持 thinking 控制参数的模型关键词；匹配前统一转小写。
    thinking_control_terms: tuple[str, ...] = ()

    def matches(self, ctx: HookContext, body: dict[str, Any]) -> bool:
        if not should_apply_openai_chat_vendor_adapter(ctx):
            return False
        # 自动识别只看模型信号，避免 Provider 命名或下游路由别名把匹配范围放大；匹配前统一转小写。
        # upstream_model：进入 hook 前由路由 key 解析出的真实上游模型名。
        # body["model"]：当前请求体里的上游模型字段，可能已被 translator 或前置 hook 改写。
        candidate_values = (
            ctx.upstream_model,
            body.get("model"),
        )
        return _matches_any(candidate_values, self.match_terms)

    def supports_thinking_control(self, ctx: HookContext, body: dict[str, Any]) -> bool:
        # thinking 控制只看模型信号，不看 provider 名，避免整家厂商被误判为支持。
        # upstream_model：进入 hook 前由路由 key 解析出的真实上游模型名。
        # body["model"]：当前请求体里的上游模型字段，可能已被 translator 或前置 hook 改写。
        candidate_values = (
            ctx.upstream_model,
            body.get("model"),
        )
        return _matches_any(candidate_values, self.thinking_control_terms)

    def request_guard(self, ctx: HookContext, body: dict[str, Any]) -> dict[str, Any]:
        if not should_apply_openai_chat_vendor_adapter(ctx):
            return body
        updated = dict(body)
        effort = extract_reasoning_effort(updated)
        return self.apply(ctx, updated, effort)

    def apply(
        self,
        ctx: HookContext,
        body: dict[str, Any],
        effort: str | None,
    ) -> dict[str, Any]:
        del ctx, effort
        return body


class SingleVendorReasoningHook(BaseHook):
    """只应用一个厂商处理器的 Hook 基类。"""

    def __init__(self, adapter: VendorReasoningAdapter):
        self._adapter = adapter

    def request_guard(self, ctx: HookContext, body: dict[str, Any]) -> dict[str, Any]:
        return self._adapter.request_guard(ctx, body)


def should_apply_openai_chat_vendor_adapter(ctx: HookContext) -> bool:
    # 厂商兼容参数用于跨协议进入 OpenAI Chat 上游；OpenAI Chat 原生下游保持原样透传。
    source_format = str(ctx.provider_source_format or "").strip().lower()
    target_format = str(ctx.provider_target_format or "").strip().lower()
    return source_format == "openai_chat" and target_format != "openai_chat"


def _matches_any(values: tuple[Any, ...], terms: tuple[str, ...]) -> bool:
    text = " ".join(str(value or "").lower() for value in values)
    return any(term in text for term in terms)


def extract_reasoning_effort(body: dict[str, Any]) -> str | None:
    effort = normalize_openai_reasoning_effort(body.get("reasoning_effort"))
    if effort is not None:
        return effort

    effort = openai_reasoning_effort_from_responses_reasoning(body.get("reasoning"))
    if effort is not None:
        return effort

    effort = extract_reasoning_effort_from_thinking(body.get("thinking"))
    if effort is not None:
        return effort

    if "enable_thinking" in body:
        return OPENAI_REASONING_FALLBACK_EFFORT if bool(body.get("enable_thinking")) else "none"
    return None


def extract_reasoning_effort_from_thinking(thinking: Any) -> str | None:
    if isinstance(thinking, dict):
        effort = openai_reasoning_effort_from_claude_thinking(thinking)
        if effort is not None:
            return effort
        return normalize_openai_reasoning_effort(thinking.get("type"))
    return normalize_openai_reasoning_effort(thinking)


def remove_generic_reasoning_fields(
    body: dict[str, Any],
    *,
    keep_reasoning_effort: bool = False,
    keep_thinking: bool = False,
) -> None:
    body.pop("reasoning", None)
    if not keep_reasoning_effort:
        body.pop("reasoning_effort", None)
    if not keep_thinking:
        body.pop("thinking", None)


def has_assistant_reasoning_content(body: dict[str, Any]) -> bool:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        if has_reasoning_text(message.get("reasoning_content")):
            return True
        if has_reasoning_text(message.get("reasoning_details")):
            return True
    return False


def has_reasoning_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(has_reasoning_text(item) for item in value)
    if isinstance(value, dict):
        return any(has_reasoning_text(item) for item in value.values())
    return False
