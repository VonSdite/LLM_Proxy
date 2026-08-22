from __future__ import annotations

import json
from typing import Any

from src.hooks import HookContext

from claude_responses_to_chat_compat_common import (
    OPENAI_REASONING_FALLBACK_EFFORT,
    SingleVendorReasoningHook,
    VendorReasoningAdapter,
    is_openai_chat_upstream,
    remove_generic_reasoning_fields,
)


class DeepSeekReasoningAdapter(VendorReasoningAdapter):
    """DeepSeek OpenAI Chat 兼容参数适配。"""

    match_terms = ("deepseek",)
    thinking_control_terms = ("deepseek-v4",)

    def request_guard(self, ctx: HookContext, body: dict[str, Any]) -> dict[str, Any]:
        updated = super().request_guard(ctx, body)
        if not _is_responses_to_openai_chat(ctx):
            return updated
        return _downgrade_responses_custom_tools(updated)

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
    """DeepSeek OpenAI Chat 兼容参数 Hook。"""


def _is_responses_to_openai_chat(ctx: HookContext) -> bool:
    target_format = str(ctx.provider_target_format or "").strip().lower()
    return is_openai_chat_upstream(ctx) and target_format == "openai_responses"


def _downgrade_responses_custom_tools(body: dict[str, Any]) -> dict[str, Any]:
    """把 DeepSeek 不支持的 Chat custom 工具降级为 function。"""
    updated = dict(body)

    tools = body.get("tools")
    if isinstance(tools, list):
        updated["tools"] = [_downgrade_custom_tool(tool) for tool in tools]

    messages = body.get("messages")
    if isinstance(messages, list):
        updated["messages"] = [_downgrade_message_custom_calls(message) for message in messages]

    tool_choice = body.get("tool_choice")
    downgraded_tool_choice = _downgrade_custom_tool_choice(tool_choice)
    if downgraded_tool_choice is not tool_choice:
        updated["tool_choice"] = downgraded_tool_choice

    return updated


def _downgrade_custom_tool(tool: Any) -> Any:
    if not isinstance(tool, dict) or str(tool.get("type") or "").strip().lower() != "custom":
        return tool
    custom = tool.get("custom")
    if not isinstance(custom, dict):
        return tool
    name = str(custom.get("name") or "").strip()
    if not name:
        return tool

    function: dict[str, Any] = {
        "name": name,
        "parameters": {
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "required": ["input"],
        },
    }
    if custom.get("description") is not None:
        function["description"] = str(custom["description"])
    return {"type": "function", "function": function}


def _downgrade_message_custom_calls(message: Any) -> Any:
    if not isinstance(message, dict) or not isinstance(message.get("tool_calls"), list):
        return message

    changed = False
    tool_calls = []
    for tool_call in message["tool_calls"]:
        downgraded = _downgrade_custom_tool_call(tool_call)
        tool_calls.append(downgraded)
        changed = changed or downgraded is not tool_call
    if not changed:
        return message

    updated = dict(message)
    updated["tool_calls"] = tool_calls
    return updated


def _downgrade_custom_tool_call(tool_call: Any) -> Any:
    if not isinstance(tool_call, dict) or str(tool_call.get("type") or "").strip().lower() != "custom":
        return tool_call
    custom = tool_call.get("custom")
    if not isinstance(custom, dict):
        return tool_call
    name = str(custom.get("name") or "").strip()
    if not name:
        return tool_call

    input_value = custom.get("input")
    if input_value is None:
        input_text = ""
    elif isinstance(input_value, str):
        input_text = input_value
    else:
        input_text = json.dumps(input_value, ensure_ascii=False, separators=(",", ":"))

    updated = {key: value for key, value in tool_call.items() if key not in {"type", "custom", "function"}}
    updated["type"] = "function"
    updated["function"] = {
        "name": name,
        "arguments": json.dumps({"input": input_text}, ensure_ascii=False, separators=(",", ":")),
    }
    return updated


def _downgrade_custom_tool_choice(tool_choice: Any) -> Any:
    if not isinstance(tool_choice, dict) or str(tool_choice.get("type") or "").strip().lower() != "custom":
        return tool_choice
    custom = tool_choice.get("custom")
    if not isinstance(custom, dict):
        return tool_choice
    name = str(custom.get("name") or "").strip()
    if not name:
        return tool_choice
    return {"type": "function", "function": {"name": name}}
