from __future__ import annotations

from typing import Any

from src.hooks import BaseHook, HookContext


class Hook(BaseHook):
    """适配 OpenAI Responses 上游的消息角色。"""

    def request_guard(self, ctx: HookContext, body: dict[str, Any]) -> dict[str, Any]:
        if str(ctx.provider_source_format or "").strip().lower() != "openai_responses":
            return body

        input_items = body.get("input")
        if not isinstance(input_items, list):
            return body

        normalized_items: list[Any] = []
        changed = False
        for item in input_items:
            if not isinstance(item, dict) or str(item.get("role") or "").strip().lower() != "developer":
                normalized_items.append(item)
                continue
            normalized_item = dict(item)
            normalized_item["role"] = "system"
            normalized_items.append(normalized_item)
            changed = True

        if not changed:
            return body

        updated = dict(body)
        updated["input"] = normalized_items
        return updated
