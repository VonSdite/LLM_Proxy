from __future__ import annotations

from typing import Any

from src.hooks import BaseHook, HookAbortError, HookContext, HookErrorType


class Hook(BaseHook):
    """演示 header_hook、request_guard、response_guard 和 fetch_models 扩展点。"""

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

    def fetch_models(self, ctx: HookContext, payload: dict[str, Any]) -> Any | None:
        ctx.logger.info(
            "fetch_models invoked: provider=%s api=%s candidates=%s",
            ctx.provider_name,
            payload.get("api"),
            payload.get("candidate_urls"),
        )
        # 返回 None 表示继续使用系统内置的 /v1/models 和 /models 候选端点探测。
        # 如需由 Hook 接管模型拉取，可以直接返回模型名列表：
        # return ["demo-model-a", "demo-model-b"]
        # 也可以返回 OpenAI 风格响应，系统会从 data[].id 或 data[].name 中提取模型名：
        # return {"data": [{"id": "demo-model-a"}, {"id": "demo-model-b"}]}
        return None
