#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for bridging Codex request bodies into shared OpenAI responses/chat shapes."""

from __future__ import annotations

from typing import Any, Dict, List

from .responses_bridge import convert_openai_responses_request_to_chat_request as _convert_openai_responses_request_to_chat_request


def convert_codex_request_to_openai_responses_request(
    model_name: str,
    body: Dict[str, Any],
    stream: bool,
) -> Dict[str, Any]:
    translated = dict(body)
    translated["model"] = model_name
    translated["stream"] = bool(stream)
    translated.pop("store", None)
    translated.pop("include", None)

    input_items = translated.get("input")
    if isinstance(input_items, list):
        rewritten_input: List[Any] = []
        for item in input_items:
            if not isinstance(item, dict):
                rewritten_input.append(item)
                continue
            rewritten = dict(item)
            if (
                str(rewritten.get("type") or "").strip().lower() == "message"
                and str(rewritten.get("role") or "").strip().lower() == "developer"
            ):
                rewritten["role"] = "system"
            rewritten_input.append(rewritten)
        translated["input"] = rewritten_input

    return translated


def convert_codex_request_to_openai_chat_request(
    model_name: str,
    body: Dict[str, Any],
    stream: bool,
) -> Dict[str, Any]:
    responses_request = convert_codex_request_to_openai_responses_request(model_name, body, stream)
    return _convert_openai_responses_request_to_chat_request(model_name, responses_request, stream)
