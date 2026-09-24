#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取 OpenAI 与 Codex 模型目录和价格配置。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).with_name("openai_model_catalog.json")
with _CATALOG_PATH.open(encoding="utf-8") as catalog_file:
    _CATALOG: dict[str, Any] = json.load(catalog_file)

_CODEX_CATALOG = _CATALOG["codex"]
CODEX_MODEL_REFERENCE_URLS: tuple[str, ...] = tuple(_CODEX_CATALOG["model_reference_urls"])
DEFAULT_CODEX_MODEL_IDS: tuple[str, ...] = tuple(_CODEX_CATALOG["default_text_models"])
DEFAULT_CODEX_IMAGE_MODEL_IDS: tuple[str, ...] = tuple(_CODEX_CATALOG["default_image_models"])
DEFAULT_CODEX_IMAGE_MODEL_ID: str = _CODEX_CATALOG["default_image_model"]

_PRICING_CATALOG = _CATALOG["pricing"]
OPENAI_MODEL_PRICING_SOURCE: str = _PRICING_CATALOG["source"]
OPENAI_MODEL_PRICING_SNAPSHOT_DATE: str = _PRICING_CATALOG["snapshot_date"]
LONG_CONTEXT_THRESHOLD_TOKENS: int = _PRICING_CATALOG["long_context_threshold_tokens"]


@dataclass(frozen=True)
class OpenAIModelPrice:
    """描述一个模型按 Token 计费的美元单价。"""

    input_cost_per_token: float
    cache_read_input_token_cost: float | None
    output_cost_per_token: float
    cache_creation_input_token_cost: float | None = None
    input_cost_per_token_above_272k_tokens: float | None = None
    cache_read_input_token_cost_above_272k_tokens: float | None = None
    cache_creation_input_token_cost_above_272k_tokens: float | None = None
    output_cost_per_token_above_272k_tokens: float | None = None
    long_context_pricing_available: bool = True


@dataclass(frozen=True)
class OpenAIImageModelPrice:
    """描述图片模型按文本和图片 Token 计费的美元单价。"""

    text_input_cost_per_token: float
    cached_text_input_cost_per_token: float
    image_input_cost_per_token: float
    cached_image_input_cost_per_token: float
    image_output_cost_per_token: float
    text_output_cost_per_token: float | None = None


def _load_model_prices(prices: dict[str, dict[str, Any]]) -> dict[str, OpenAIModelPrice]:
    return {model_id: OpenAIModelPrice(**price) for model_id, price in prices.items()}


def _load_image_model_prices(
    prices: dict[str, dict[str, Any]],
) -> dict[str, OpenAIImageModelPrice]:
    return {model_id: OpenAIImageModelPrice(**price) for model_id, price in prices.items()}


OPENAI_MODEL_PRICES = _load_model_prices(_PRICING_CATALOG["text"])
OPENAI_FAST_MODEL_PRICES = _load_model_prices(_PRICING_CATALOG["fast"])
OPENAI_IMAGE_MODEL_PRICES = _load_image_model_prices(_PRICING_CATALOG["image"])
