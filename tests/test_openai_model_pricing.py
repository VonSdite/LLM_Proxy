from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.openai_model_pricing import estimate_openai_request_cost_usd


class OpenAIModelPricingTests(unittest.TestCase):
    def test_estimates_each_model_with_its_own_long_context_prices(self) -> None:
        usage = {
            "usage_status": "known",
            "prompt_tokens": 1_000_000,
            "completion_tokens": 100_000,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

        self.assertAlmostEqual(27.5, estimate_openai_request_cost_usd("gpt-6-astra", usage) or 0)
        self.assertAlmostEqual(5.5, estimate_openai_request_cost_usd("gpt-6-sol", usage) or 0)
        self.assertAlmostEqual(0.275, estimate_openai_request_cost_usd("gpt-6-luna", usage) or 0)
        self.assertAlmostEqual(11.0, estimate_openai_request_cost_usd("gpt-5.6-sol", usage) or 0)
        self.assertAlmostEqual(5.8, estimate_openai_request_cost_usd("gpt-5.6-terra", usage) or 0)
        self.assertAlmostEqual(0.58, estimate_openai_request_cost_usd("gpt-5.6-luna", usage) or 0)
        self.assertAlmostEqual(3.15, estimate_openai_request_cost_usd("gpt-5.3-codex", usage) or 0)
        self.assertAlmostEqual(14.5, estimate_openai_request_cost_usd("gpt-5.5", usage) or 0)
        self.assertAlmostEqual(87.0, estimate_openai_request_cost_usd("gpt-5.5-pro", usage) or 0)
        self.assertAlmostEqual(7.25, estimate_openai_request_cost_usd("gpt-5.4", usage) or 0)
        self.assertAlmostEqual(87.0, estimate_openai_request_cost_usd("gpt-5.4-pro", usage) or 0)

    def test_estimates_each_model_with_its_own_short_context_prices(self) -> None:
        usage = {
            "usage_status": "known",
            "prompt_tokens": 100_000,
            "completion_tokens": 10_000,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

        self.assertAlmostEqual(1.5, estimate_openai_request_cost_usd("gpt-6-astra", usage) or 0)
        self.assertAlmostEqual(0.3, estimate_openai_request_cost_usd("gpt-6-sol", usage) or 0)
        self.assertAlmostEqual(0.015, estimate_openai_request_cost_usd("gpt-6-luna", usage) or 0)
        self.assertAlmostEqual(0.6, estimate_openai_request_cost_usd("gpt-5.6", usage) or 0)
        self.assertAlmostEqual(0.6, estimate_openai_request_cost_usd("gpt-5.6-sol", usage) or 0)
        self.assertAlmostEqual(0.32, estimate_openai_request_cost_usd("gpt-5.6-terra", usage) or 0)
        self.assertAlmostEqual(0.032, estimate_openai_request_cost_usd("gpt-5.6-luna", usage) or 0)
        self.assertAlmostEqual(0.315, estimate_openai_request_cost_usd("gpt-5.3-codex", usage) or 0)
        self.assertAlmostEqual(0.8, estimate_openai_request_cost_usd("gpt-5.5", usage) or 0)
        self.assertAlmostEqual(4.8, estimate_openai_request_cost_usd("gpt-5.5-pro", usage) or 0)
        self.assertAlmostEqual(0.4, estimate_openai_request_cost_usd("gpt-5.4", usage) or 0)
        self.assertAlmostEqual(4.8, estimate_openai_request_cost_usd("gpt-5.4-pro", usage) or 0)
        self.assertAlmostEqual(2.0, estimate_openai_request_cost_usd("gpt-5.6-cyber", usage) or 0)
        self.assertAlmostEqual(2.0, estimate_openai_request_cost_usd("gpt-daybreak-red-latest", usage) or 0)

    def test_uses_fast_mode_prices_for_fast_and_priority_service_tiers(self) -> None:
        usage = {
            "usage_status": "known",
            "prompt_tokens": 100_000,
            "completion_tokens": 10_000,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "service_tier": "fast",
        }

        self.assertAlmostEqual(3.0, estimate_openai_request_cost_usd("gpt-6-astra", usage) or 0)
        self.assertAlmostEqual(0.6, estimate_openai_request_cost_usd("gpt-6-sol", usage) or 0)
        self.assertAlmostEqual(0.03, estimate_openai_request_cost_usd("gpt-6-luna", usage) or 0)
        self.assertAlmostEqual(1.2, estimate_openai_request_cost_usd("gpt-5.6-sol", usage) or 0)
        self.assertAlmostEqual(0.64, estimate_openai_request_cost_usd("gpt-5.6-terra", usage) or 0)
        self.assertAlmostEqual(0.064, estimate_openai_request_cost_usd("gpt-5.6-luna", usage) or 0)
        self.assertAlmostEqual(0.63, estimate_openai_request_cost_usd("gpt-5.3-codex", usage) or 0)
        self.assertAlmostEqual(2.0, estimate_openai_request_cost_usd("gpt-5.5", usage) or 0)
        self.assertAlmostEqual(
            0.8,
            estimate_openai_request_cost_usd("gpt-5.4", {**usage, "service_tier": "priority"}) or 0,
        )
        self.assertIsNone(estimate_openai_request_cost_usd("gpt-5.5-pro", usage))
        self.assertIsNone(
            estimate_openai_request_cost_usd(
                "gpt-5.4",
                {**usage, "prompt_tokens": 300_000},
            )
        )

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
        self.assertIsNone(estimate_openai_request_cost_usd("gpt-image-2", usage))
        self.assertIsNone(estimate_openai_request_cost_usd("gpt-image-1.5", usage))
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

    def test_estimates_image_models_from_text_and_image_token_details(self) -> None:
        usage = {
            "usage_status": "known",
            "prompt_tokens": 150_000,
            "completion_tokens": 10_000,
            "input_tokens_details": {"text_tokens": 100_000, "image_tokens": 50_000},
            "output_tokens_details": {"text_tokens": 0, "image_tokens": 10_000},
        }

        self.assertAlmostEqual(1.2, estimate_openai_request_cost_usd("gpt-image-2.5-sunburst", usage) or 0)
        self.assertAlmostEqual(1.2, estimate_openai_request_cost_usd("gpt-image-2.5-flare", usage) or 0)
        self.assertAlmostEqual(
            1.2,
            estimate_openai_request_cost_usd("gpt-image-2-2026-09-23", usage) or 0,
        )

    def test_image_model_requires_complete_modality_details(self) -> None:
        usage = {
            "usage_status": "known",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "input_tokens_details": {"text_tokens": 10, "image_tokens": 0},
        }

        self.assertIsNone(estimate_openai_request_cost_usd("gpt-image-2.5-sunburst", usage))
        self.assertIsNone(
            estimate_openai_request_cost_usd(
                "gpt-image-2.5-sunburst",
                {
                    **usage,
                    "output_tokens_details": {"text_tokens": 0, "image_tokens": 4},
                },
            )
        )

    def test_image_model_uses_cached_rate_for_single_modality_input(self) -> None:
        usage = {
            "usage_status": "known",
            "prompt_tokens": 100_000,
            "completion_tokens": 0,
            "input_tokens_details": {
                "text_tokens": 100_000,
                "image_tokens": 0,
                "cached_tokens": 25_000,
            },
            "output_tokens_details": {"text_tokens": 0, "image_tokens": 0},
        }

        self.assertAlmostEqual(0.40625, estimate_openai_request_cost_usd("gpt-image-2.5-sunburst", usage) or 0)

        mixed_input = {
            **usage,
            "input_tokens_details": {
                "text_tokens": 50_000,
                "image_tokens": 50_000,
                "cached_tokens": 25_000,
            },
        }
        self.assertIsNone(estimate_openai_request_cost_usd("gpt-image-2.5-sunburst", mixed_input))


if __name__ == "__main__":
    unittest.main()
