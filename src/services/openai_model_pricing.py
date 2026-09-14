#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 OpenAI 模型价格快照估算单次请求的美元等价费用。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OPENAI_MODEL_PRICING_SOURCE = (
    "https://raw.githubusercontent.com/Wei-Shaw/model-price-repo/refs/heads/main/model_prices_and_context_window.json"
)
OPENAI_MODEL_PRICING_SNAPSHOT_DATE = "2026-09-14"
LONG_CONTEXT_THRESHOLD_TOKENS = 272_000


@dataclass(frozen=True)
class OpenAIModelPrice:
    """描述一个模型按 Token 计费的美元单价。"""

    input_cost_per_token: float
    cache_read_input_token_cost: float
    output_cost_per_token: float
    cache_creation_input_token_cost: float | None = None
    input_cost_per_token_above_272k_tokens: float | None = None
    cache_read_input_token_cost_above_272k_tokens: float | None = None
    cache_creation_input_token_cost_above_272k_tokens: float | None = None
    output_cost_per_token_above_272k_tokens: float | None = None


# 价格是 API 零售价的等价估算，不表示 Codex OAuth 账号会产生对应账单。
OPENAI_MODEL_PRICES: dict[str, OpenAIModelPrice] = {
    "gpt-6-astra": OpenAIModelPrice(
        input_cost_per_token=0.00001,
        cache_read_input_token_cost=0.000001,
        cache_creation_input_token_cost=0.0000125,
        output_cost_per_token=0.00005,
        input_cost_per_token_above_272k_tokens=0.00002,
        cache_read_input_token_cost_above_272k_tokens=0.000002,
        cache_creation_input_token_cost_above_272k_tokens=0.000025,
        output_cost_per_token_above_272k_tokens=0.000075,
    ),
    "gpt-5.6-sol": OpenAIModelPrice(
        input_cost_per_token=0.000005,
        cache_read_input_token_cost=0.0000005,
        cache_creation_input_token_cost=0.00000625,
        output_cost_per_token=0.00003,
        input_cost_per_token_above_272k_tokens=0.00001,
        cache_read_input_token_cost_above_272k_tokens=0.000001,
        cache_creation_input_token_cost_above_272k_tokens=0.0000125,
        output_cost_per_token_above_272k_tokens=0.000045,
    ),
    "gpt-5.6-terra": OpenAIModelPrice(
        input_cost_per_token=0.000002,
        cache_read_input_token_cost=0.0000002,
        cache_creation_input_token_cost=0.0000025,
        output_cost_per_token=0.000012,
        input_cost_per_token_above_272k_tokens=0.000004,
        cache_read_input_token_cost_above_272k_tokens=0.0000004,
        cache_creation_input_token_cost_above_272k_tokens=0.000005,
        output_cost_per_token_above_272k_tokens=0.000018,
    ),
    "gpt-5.6-luna": OpenAIModelPrice(
        input_cost_per_token=0.0000002,
        cache_read_input_token_cost=0.00000002,
        cache_creation_input_token_cost=0.00000025,
        output_cost_per_token=0.0000012,
        input_cost_per_token_above_272k_tokens=0.0000004,
        cache_read_input_token_cost_above_272k_tokens=0.00000004,
        cache_creation_input_token_cost_above_272k_tokens=0.0000005,
        output_cost_per_token_above_272k_tokens=0.0000018,
    ),
    "gpt-5.5": OpenAIModelPrice(
        input_cost_per_token=0.000005,
        cache_read_input_token_cost=0.0000005,
        output_cost_per_token=0.00003,
        input_cost_per_token_above_272k_tokens=0.00001,
        cache_read_input_token_cost_above_272k_tokens=0.000001,
        output_cost_per_token_above_272k_tokens=0.000045,
    ),
}


def estimate_openai_request_cost_usd(model_name: Any, usage: dict[str, Any]) -> float | None:
    """根据实际模型和规范化 usage 返回单次请求的美元等价费用。"""
    price = _find_model_price(model_name)
    if price is None or str(usage.get("usage_status") or "").strip().lower() != "known":
        return None

    prompt_tokens = _non_negative_int(usage.get("prompt_tokens"))
    completion_tokens = _non_negative_int(usage.get("completion_tokens"))
    cache_read_tokens = _non_negative_int(usage.get("cache_read_input_tokens"))
    cache_creation_tokens = _non_negative_int(usage.get("cache_creation_input_tokens"))
    if None in (prompt_tokens, completion_tokens, cache_read_tokens, cache_creation_tokens):
        return None
    assert prompt_tokens is not None
    assert completion_tokens is not None
    assert cache_read_tokens is not None
    assert cache_creation_tokens is not None
    if cache_read_tokens + cache_creation_tokens > prompt_tokens:
        return None

    long_context = prompt_tokens > LONG_CONTEXT_THRESHOLD_TOKENS
    input_rate = _select_rate(
        price.input_cost_per_token,
        price.input_cost_per_token_above_272k_tokens,
        long_context,
    )
    cache_read_rate = _select_rate(
        price.cache_read_input_token_cost,
        price.cache_read_input_token_cost_above_272k_tokens,
        long_context,
    )
    cache_creation_rate = _select_rate(
        price.cache_creation_input_token_cost or price.input_cost_per_token,
        price.cache_creation_input_token_cost_above_272k_tokens or price.input_cost_per_token_above_272k_tokens,
        long_context,
    )
    output_rate = _select_rate(
        price.output_cost_per_token,
        price.output_cost_per_token_above_272k_tokens,
        long_context,
    )
    uncached_input_tokens = prompt_tokens - cache_read_tokens - cache_creation_tokens
    return (
        uncached_input_tokens * input_rate
        + cache_read_tokens * cache_read_rate
        + cache_creation_tokens * cache_creation_rate
        + completion_tokens * output_rate
    )


def _find_model_price(model_name: Any) -> OpenAIModelPrice | None:
    normalized_model = str(model_name or "").strip().lower()
    matching_ids = [
        model_id
        for model_id in OPENAI_MODEL_PRICES
        if normalized_model == model_id or normalized_model.startswith(f"{model_id}-")
    ]
    if not matching_ids:
        return None
    return OPENAI_MODEL_PRICES[max(matching_ids, key=len)]


def _select_rate(standard_rate: float, long_context_rate: float | None, long_context: bool) -> float:
    if long_context and long_context_rate is not None:
        return long_context_rate
    return standard_rate


def _non_negative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
