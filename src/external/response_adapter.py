#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游响应适配：执行 provider 输出钩子并构造 Flask 响应。"""

import json
from typing import Any, Callable, Dict, Iterator, Optional

from flask import Response

from ..application.app_context import Logger
from ..hooks import HookContext, HookModule


def build_proxy_response(
    hook: Optional[HookModule],
    ctx: HookContext,
    response: Any,
    is_stream: bool,
    filter_headers_func: Any,
    stream_context_func: Any,
    logger: Logger,
    on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Response:
    """基于上游响应与输出钩子构建 Flask 响应。"""
    hook_func = getattr(hook, "output_body_hook", None) if hook else None
    meta: Dict[str, Any] = {
        "response_model": None,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    completed = False

    def update_meta_from_payload(payload: Dict[str, Any]) -> None:
        model = payload.get("model")
        if model:
            meta["response_model"] = model
        usage = payload.get("usage")
        if isinstance(usage, dict):
            if "total_tokens" in usage and usage.get("total_tokens") is not None:
                meta["total_tokens"] = int(usage.get("total_tokens") or 0)
            if "prompt_tokens" in usage and usage.get("prompt_tokens") is not None:
                meta["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
            if "completion_tokens" in usage and usage.get("completion_tokens") is not None:
                meta["completion_tokens"] = int(usage.get("completion_tokens") or 0)

    def safe_on_complete() -> None:
        nonlocal completed
        if completed:
            return
        completed = True
        if on_complete:
            try:
                on_complete(meta)
            except Exception as exc:
                logger.error(f"Error in on_complete callback: {exc}")

    def parse_sse_line(line: str) -> Optional[str]:
        stripped = line.strip()
        if not stripped or not stripped.startswith("data:"):
            return None
        return stripped[5:].strip()

    def process_sse_event(event_text: str) -> Iterator[bytes]:
        data_lines = []
        for raw_line in event_text.splitlines():
            if raw_line.startswith("data:"):
                data_lines.append(raw_line[5:].strip())

        if not data_lines:
            if event_text.strip():
                yield (event_text + "\n\n").encode("utf-8")
            return

        data_str = "\n".join(data_lines).strip()
        if not data_str:
            return

        if data_str == "[DONE]":
            yield b"data: [DONE]\n\n"
            return

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            yield f"data: {data_str}\n\n".encode("utf-8")
            return

        if isinstance(data, dict):
            update_meta_from_payload(data)

        if hook_func:
            modified = hook_func(ctx, data)
            if modified is not None:
                yield f"data: {json.dumps(modified)}\n\n".encode("utf-8")
            else:
                yield f"data: {data_str}\n\n".encode("utf-8")
        else:
            yield f"data: {data_str}\n\n".encode("utf-8")

    if not is_stream:
        body = response.content
        response_body = body
        try:
            payload = json.loads(body.decode("utf-8"))
            if isinstance(payload, dict):
                update_meta_from_payload(payload)
            if hook_func:
                modified = hook_func(ctx, payload)
                response_body = json.dumps(modified).encode("utf-8")
        except json.JSONDecodeError as exc:
            logger.warning(
                "Non-stream response is not valid JSON, skip output_body_hook: status=%s content_type=%s error=%s",
                response.status_code,
                response.headers.get("Content-Type"),
                exc,
            )
        finally:
            safe_on_complete()

        return Response(
            response_body,
            status=response.status_code,
            headers=filter_headers_func(response.headers),
            mimetype=response.headers.get("Content-Type", "application/json"),
        )

    def generate() -> Iterator[bytes]:
        buffer = ""
        try:
            for chunk in response.iter_content(chunk_size=None):
                if not chunk:
                    continue

                try:
                    buffer += chunk.decode("utf-8")
                except UnicodeDecodeError:
                    yield chunk
                    continue

                while "\n\n" in buffer:
                    event_text, buffer = buffer.split("\n\n", 1)
                    yield from process_sse_event(event_text)

            if buffer:
                for line in buffer.splitlines():
                    data_str = parse_sse_line(line)
                    if data_str is None:
                        continue
                    if data_str == "[DONE]":
                        yield b"data: [DONE]\n\n"
                        continue
                    try:
                        data = json.loads(data_str)
                        if isinstance(data, dict):
                            update_meta_from_payload(data)
                    except Exception:
                        continue
        finally:
            safe_on_complete()

    return Response(
        stream_context_func(generate()),
        status=response.status_code,
        headers=filter_headers_func(response.headers),
        mimetype=response.headers.get("Content-Type", "text/event-stream"),
    )
