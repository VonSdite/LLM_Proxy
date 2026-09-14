from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.openai_model_pricing import estimate_openai_request_cost_usd


class OpenAIModelPricingTests(unittest.TestCase):
    def test_estimates_each_model_with_its_own_prices(self) -> None:
        usage = {
            "usage_status": "known",
            "prompt_tokens": 1_000_000,
            "completion_tokens": 100_000,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

        self.assertAlmostEqual(27.5, estimate_openai_request_cost_usd("gpt-6-astra", usage) or 0)
        self.assertAlmostEqual(14.5, estimate_openai_request_cost_usd("gpt-5.6-sol", usage) or 0)
        self.assertAlmostEqual(5.8, estimate_openai_request_cost_usd("gpt-5.6-terra", usage) or 0)
        self.assertAlmostEqual(0.58, estimate_openai_request_cost_usd("gpt-5.6-luna", usage) or 0)
        self.assertAlmostEqual(14.5, estimate_openai_request_cost_usd("gpt-5.5", usage) or 0)

    def test_uses_cache_and_long_context_prices(self) -> None:
        usage = {
            "usage_status": "known",
            "prompt_tokens": 300_000,
            "completion_tokens": 10_000,
            "cache_read_input_tokens": 100_000,
            "cache_creation_input_tokens": 20_000,
        }

        cost = estimate_openai_request_cost_usd("gpt-6-astra-2026-09-14", usage)

        self.assertAlmostEqual(5.05, cost or 0)

    def test_unknown_model_or_incomplete_usage_is_not_zero_cost(self) -> None:
        usage = {
            "usage_status": "known",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

        self.assertIsNone(estimate_openai_request_cost_usd("unknown-model", usage))
        self.assertIsNone(
            estimate_openai_request_cost_usd(
                "gpt-6-astra",
                {**usage, "usage_status": "partial"},
            )
        )

    def test_rejects_cache_tokens_larger_than_prompt_tokens(self) -> None:
        usage = {
            "usage_status": "known",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cache_read_input_tokens": 8,
            "cache_creation_input_tokens": 3,
        }

        self.assertIsNone(estimate_openai_request_cost_usd("gpt-6-astra", usage))


if __name__ == "__main__":
    unittest.main()
