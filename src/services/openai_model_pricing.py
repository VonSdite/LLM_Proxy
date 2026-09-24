#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 OpenAI 模型价格快照估算单次请求的美元等价费用。"""

from __future__ import annotations

import re
from typing import Any

from ..config.openai_model_catalog import (
    LONG_CONTEXT_THRESHOLD_TOKENS,
    OPENAI_FAST_MODEL_PRICES,
    OPENAI_IMAGE_MODEL_PRICES,
    OPENAI_MODEL_PRICES,
    OPENAI_MODEL_PRICING_SNAPSHOT_DATE,
    OPENAI_MODEL_PRICING_SOURCE,
    OpenAIImageModelPrice,
    OpenAIModelPrice,
)

MODEL_SNAPSHOT_SUFFIX_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}(?:$|[-.].*)")
__all__ = [
    "LONG_CONTEXT_THRESHOLD_TOKENS",
    "OPENAI_FAST_MODEL_PRICES",
    "OPENAI_IMAGE_MODEL_PRICES",
    "OPENAI_MODEL_PRICES",
    "OPENAI_MODEL_PRICING_SNAPSHOT_DATE",
    "OPENAI_MODEL_PRICING_SOURCE",
    "OpenAIImageModelPrice",
    "OpenAIModelPrice",
    "estimate_openai_request_cost_usd",
]


def estimate_openai_request_cost_usd(model_name: Any, usage: dict[str, Any]) -> float | None:
    """根据实际模型和规范化 usage 返回单次请求的美元等价费用。"""
    image_price = _find_image_model_price(model_name)
    if image_price is not None:
        return _estimate_image_request_cost_usd(image_price, usage)

    price_table = _select_model_price_table(usage.get("service_tier"))
    if price_table is None:
        return None
    price = _find_model_price(model_name, price_table)
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
    if long_context and not price.long_context_pricing_available:
        return None
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
        price.cache_creation_input_token_cost,
        price.cache_creation_input_token_cost_above_272k_tokens,
        long_context,
    )
    output_rate = _select_rate(
        price.output_cost_per_token,
        price.output_cost_per_token_above_272k_tokens,
        long_context,
    )
    if (cache_read_tokens and cache_read_rate is None) or (cache_creation_tokens and cache_creation_rate is None):
        return None
    uncached_input_tokens = prompt_tokens - cache_read_tokens - cache_creation_tokens
    return (
        uncached_input_tokens * input_rate
        + cache_read_tokens * (cache_read_rate or 0.0)
        + cache_creation_tokens * (cache_creation_rate or 0.0)
        + completion_tokens * output_rate
    )


def _estimate_image_request_cost_usd(
    price: OpenAIImageModelPrice,
    usage: dict[str, Any],
) -> float | None:
    """根据图片响应中的模态明细计算图片模型费用。"""
    if str(usage.get("usage_status") or "").strip().lower() != "known":
        return None

    prompt_tokens = _non_negative_int(usage.get("prompt_tokens"))
    completion_tokens = _non_negative_int(usage.get("completion_tokens"))
    input_split = _extract_modality_tokens(usage.get("input_tokens_details"), prompt_tokens)
    output_split = _extract_modality_tokens(usage.get("output_tokens_details"), completion_tokens)
    if input_split is None or output_split is None:
        return None

    text_input_tokens, image_input_tokens = input_split
    text_output_tokens, image_output_tokens = output_split
    cached_input_split = _extract_cached_modality_tokens(
        usage.get("input_tokens_details"),
        text_input_tokens,
        image_input_tokens,
    )
    if cached_input_split is None:
        return None
    cached_text_input_tokens, cached_image_input_tokens = cached_input_split
    if text_output_tokens and price.text_output_cost_per_token is None:
        return None
    return (
        (text_input_tokens - cached_text_input_tokens) * price.text_input_cost_per_token
        + cached_text_input_tokens * price.cached_text_input_cost_per_token
        + (image_input_tokens - cached_image_input_tokens) * price.image_input_cost_per_token
        + cached_image_input_tokens * price.cached_image_input_cost_per_token
        + text_output_tokens * (price.text_output_cost_per_token or 0.0)
        + image_output_tokens * price.image_output_cost_per_token
    )


def _select_model_price_table(service_tier: Any) -> dict[str, OpenAIModelPrice] | None:
    normalized_tier = str(service_tier or "").strip().lower()
    if normalized_tier in {"fast", "priority"}:
        return OPENAI_FAST_MODEL_PRICES
    if normalized_tier in {"", "auto", "default", "standard"}:
        return OPENAI_MODEL_PRICES
    return None


def _find_model_price(
    model_name: Any,
    price_table: dict[str, OpenAIModelPrice],
) -> OpenAIModelPrice | None:
    normalized_model = str(model_name or "").strip().lower()
    matching_ids = [model_id for model_id in price_table if _matches_priced_model_id(normalized_model, model_id)]
    if not matching_ids:
        return None
    return price_table[max(matching_ids, key=len)]


def _find_image_model_price(model_name: Any) -> OpenAIImageModelPrice | None:
    normalized_model = str(model_name or "").strip().lower()
    matching_ids = [
        model_id for model_id in OPENAI_IMAGE_MODEL_PRICES if _matches_priced_model_id(normalized_model, model_id)
    ]
    if not matching_ids:
        return None
    return OPENAI_IMAGE_MODEL_PRICES[max(matching_ids, key=len)]


def _matches_priced_model_id(normalized_model: str, model_id: str) -> bool:
    if normalized_model == model_id:
        return True
    prefix = f"{model_id}-"
    if not normalized_model.startswith(prefix):
        return False
    return MODEL_SNAPSHOT_SUFFIX_PATTERN.fullmatch(normalized_model[len(prefix) :]) is not None


def _extract_modality_tokens(details: Any, total_tokens: int | None) -> tuple[int, int] | None:
    if total_tokens is None or not isinstance(details, dict):
        return None
    has_modality_details = "text_tokens" in details or "image_tokens" in details
    if not has_modality_details:
        return (0, 0) if total_tokens == 0 else None
    text_tokens = _non_negative_int(details.get("text_tokens", 0))
    image_tokens = _non_negative_int(details.get("image_tokens", 0))
    if text_tokens is None or image_tokens is None or text_tokens + image_tokens != total_tokens:
        return None
    return text_tokens, image_tokens


def _extract_cached_modality_tokens(
    details: Any,
    text_tokens: int,
    image_tokens: int,
) -> tuple[int, int] | None:
    if not isinstance(details, dict):
        return None
    cached_tokens = _non_negative_int(details.get("cached_tokens", 0))
    if cached_tokens is None:
        return None
    if cached_tokens == 0:
        return 0, 0
    if text_tokens and image_tokens:
        return None
    if text_tokens:
        return (cached_tokens, 0) if cached_tokens <= text_tokens else None
    return (0, cached_tokens) if cached_tokens <= image_tokens else None


def _select_rate(
    standard_rate: float | None,
    long_context_rate: float | None,
    long_context: bool,
) -> float | None:
    if long_context and long_context_rate is not None:
        return long_context_rate
    return standard_rate


def _non_negative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
