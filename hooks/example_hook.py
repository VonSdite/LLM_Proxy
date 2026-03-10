from typing import Any

from src.hooks import BaseHook, HookContext


class Hook(BaseHook):
    """Example hook that demonstrates all available extension points."""

    def header_hook(self, ctx: HookContext, headers: dict[str, str]) -> dict[str, str]:
        headers["X-Custom-Header"] = "custom-value"
        headers["X-User-Id"] = str(ctx.get("user_id", "default"))
        return headers

    def input_body_hook(self, ctx: HookContext, body: dict[str, Any]) -> dict[str, Any]:
        messages = body.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    original = str(msg.get("content", ""))
                    msg["content"] = f"[PREFIX] {original}"
        return body

    def output_body_hook(self, ctx: HookContext, body: Any) -> Any:
        if not isinstance(body, dict):
            return body

        if body.get("type") == "done":
            return body

        choices = body.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, dict) and "content" in delta:
                    original = str(delta.get("content", ""))
                    delta["content"] = f"[MODIFIED] {original}"

        return body
