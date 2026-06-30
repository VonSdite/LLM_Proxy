#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨协议 reasoning / thinking 语义转换辅助。"""

from __future__ import annotations

from typing import Any


OPENAI_REASONING_FALLBACK_EFFORT = "xhigh"
OPENAI_REASONING_EFFORTS = {
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
}
_CLAUDE_BUDGET_BY_EFFORT = {
    "minimal": 1024,
    "low": 2048,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
}


def normalize_openai_reasoning_effort(value: Any) -> str | None:
    """把外部 reasoning effort 规整成 OpenAI 风格档位。"""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return OPENAI_REASONING_FALLBACK_EFFORT if value else "none"

    normalized = str(value).strip().lower().replace("-", "_")
    aliases = {
        "0": "none",
        "false": "none",
        "off": "none",
        "disabled": "none",
        "disable": "none",
        "no": "none",
        "1": OPENAI_REASONING_FALLBACK_EFFORT,
        "true": OPENAI_REASONING_FALLBACK_EFFORT,
        "on": OPENAI_REASONING_FALLBACK_EFFORT,
        "enabled": OPENAI_REASONING_FALLBACK_EFFORT,
        "enable": OPENAI_REASONING_FALLBACK_EFFORT,
        "auto": OPENAI_REASONING_FALLBACK_EFFORT,
        "adaptive": OPENAI_REASONING_FALLBACK_EFFORT,
        "max": OPENAI_REASONING_FALLBACK_EFFORT,
        "maximum": OPENAI_REASONING_FALLBACK_EFFORT,
        "extra_high": OPENAI_REASONING_FALLBACK_EFFORT,
        "extra": OPENAI_REASONING_FALLBACK_EFFORT,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in OPENAI_REASONING_EFFORTS:
        return normalized
    return OPENAI_REASONING_FALLBACK_EFFORT


def openai_reasoning_effort_from_claude_thinking(thinking: Any) -> str | None:
    """把 Claude thinking 请求规整成 OpenAI reasoning_effort。"""
    if not isinstance(thinking, dict):
        return None

    thinking_type = str(thinking.get("type") or "").strip().lower()
    if thinking_type == "disabled":
        return "none"
    if thinking_type in {"enabled", "adaptive", "auto"}:
        return openai_reasoning_effort_from_budget(thinking.get("budget_tokens"))
    if thinking_type:
        return OPENAI_REASONING_FALLBACK_EFFORT
    return None


def openai_reasoning_effort_from_budget(budget_tokens: Any) -> str:
    """把 token budget 粗略映射成 OpenAI reasoning_effort。"""
    try:
        budget = int(budget_tokens)
    except (TypeError, ValueError):
        return OPENAI_REASONING_FALLBACK_EFFORT
    if budget <= 0:
        return "none"
    if budget < 1024:
        return "minimal"
    if budget < 4096:
        return "low"
    if budget < 8192:
        return "medium"
    if budget < 16384:
        return "high"
    return OPENAI_REASONING_FALLBACK_EFFORT


def openai_reasoning_effort_from_responses_reasoning(reasoning: Any) -> str | None:
    """从 Responses reasoning 参数中提取 OpenAI 风格 effort。"""
    if isinstance(reasoning, dict):
        return normalize_openai_reasoning_effort(reasoning.get("effort"))
    return normalize_openai_reasoning_effort(reasoning)


def openai_reasoning_effort_to_responses_reasoning(effort: Any) -> dict[str, Any] | None:
    """把 OpenAI Chat reasoning_effort 转成 Responses reasoning 参数。"""
    normalized = normalize_openai_reasoning_effort(effort)
    if normalized is None:
        return None
    return {"effort": normalized}


def openai_reasoning_effort_to_claude_thinking(
    effort: Any,
    *,
    max_tokens: Any,
) -> dict[str, Any] | None:
    """把 OpenAI Chat reasoning_effort 转成 Claude thinking 参数。"""
    normalized = normalize_openai_reasoning_effort(effort)
    if normalized is None:
        return None
    if normalized == "none":
        return {"type": "disabled"}

    budget_tokens = _CLAUDE_BUDGET_BY_EFFORT.get(normalized, _CLAUDE_BUDGET_BY_EFFORT[OPENAI_REASONING_FALLBACK_EFFORT])
    try:
        max_token_count = int(max_tokens)
    except (TypeError, ValueError):
        max_token_count = 0
    if max_token_count > 1024:
        budget_tokens = min(budget_tokens, max_token_count - 1)
    return {"type": "enabled", "budget_tokens": max(1024, budget_tokens)}


def extract_openai_reasoning_text(payload: Any) -> str:
    """从 OpenAI 兼容 reasoning 字段中提取文本。"""
    if not isinstance(payload, dict):
        return ""
    reasoning_content = payload.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        return reasoning_content
    return _extract_reasoning_details_text(payload.get("reasoning_details"))


def extract_openai_reasoning_delta(payload: Any, state: dict[str, Any], key: str) -> str:
    """从流式 delta 中提取 reasoning 增量，兼容累计式 reasoning_details。"""
    if not isinstance(payload, dict):
        return ""
    reasoning_content = payload.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        return reasoning_content

    details_text = _extract_reasoning_details_text(payload.get("reasoning_details"))
    if not details_text:
        return ""

    previous = str(state.get(key) or "")
    state[key] = details_text
    if previous and details_text.startswith(previous):
        return details_text[len(previous) :]
    return details_text


def _extract_reasoning_details_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_extract_reasoning_details_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "reasoning_content", "content", "delta", "summary"):
            text = _extract_reasoning_details_text(value.get(key))
            if text:
                return text
    return ""
