from __future__ import annotations

from typing import Any

from src.hooks import BaseHook, HookAbortError, HookContext, HookErrorType


class Hook(BaseHook):
    """演示 header_hook、request_guard 和 response_guard 扩展点。"""

    def header_hook(self, ctx: HookContext, headers: dict[str, str]) -> dict[str, str]:
        ctx.logger.info(
            "header_hook invoked: provider=%s model=%s stream=%s last_status=%s last_error=%s",
            ctx.provider_name,
            ctx.request_model,
            ctx.stream,
            ctx.last_status_code,
            ctx.last_error_type,
        )
        if ctx.last_error_type == HookErrorType.TIMEOUT:
            ctx.logger.warning("Previous attempt timed out for provider=%s", ctx.provider_name)
        headers["X-Custom-Header"] = "custom-value"
        return headers

    def request_guard(self, ctx: HookContext, body: dict[str, Any]) -> dict[str, Any]:
        ctx.logger.info(
            "request_guard invoked: provider=%s source=%s target=%s",
            ctx.provider_name,
            ctx.provider_source_format,
            ctx.provider_target_format,
        )
        messages = body.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    original = str(msg.get("content", ""))
                    # 业务需要主动拒绝请求时，可以抛出 HookAbortError 并指定下游状态码。
                    if original.strip() == "[HOOK_ABORT_EXAMPLE]":
                        raise HookAbortError(
                            "Request blocked by example hook",
                            status_code=400,
                            error_type="example_hook_abort",
                        )
                    msg["content"] = f"[PREFIX] {original}"
        return body

    def response_guard(self, ctx: HookContext, body: Any) -> Any:
        if not isinstance(body, dict):
            return body

        choices = body.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or choice.get("message")
                if isinstance(delta, dict) and "content" in delta and isinstance(delta.get("content"), str):
                    original = str(delta.get("content", ""))
                    delta["content"] = f"[MODIFIED] {original}"

        return body
