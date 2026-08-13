import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.proxy_core.usage import extract_canonical_usage
from src.services.proxy_response_builder import ProxyResponseBuilder
from src.translators import ClaudeChatTranslator, OpenAIChatClaudeTranslator, build_default_translator_registry


class TokenUsageTests(unittest.TestCase):
    def test_openai_cache_write_aliases_are_normalized_and_marked_known(self) -> None:
        chat_usage = extract_canonical_usage(
            {
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "prompt_tokens_details": {
                        "cached_tokens": 4,
                        "cache_write_tokens": 2,
                    },
                }
            },
            "openai_chat",
        )
        responses_usage = extract_canonical_usage(
            {
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "input_tokens_details": {
                        "cached_tokens": 0,
                        "cached_creation_tokens": 2,
                    },
                }
            },
            "openai_responses",
        )

        self.assertEqual(4, chat_usage["cache_read_input_tokens"])
        self.assertEqual(2, chat_usage["cache_creation_input_tokens"])
        self.assertEqual("known", chat_usage["cache_usage_status"])
        self.assertEqual(0, responses_usage["cache_read_input_tokens"])
        self.assertEqual(2, responses_usage["cache_creation_input_tokens"])
        self.assertEqual("known", responses_usage["cache_usage_status"])

    def test_top_level_cache_read_alias_and_total_only_usage_are_preserved(self) -> None:
        usage = extract_canonical_usage(
            {
                "prompt_tokens": 10,
                "completion_tokens": 1,
                "cache_read_input_tokens": 5,
            },
            "openai_chat",
        )
        total_only = extract_canonical_usage({"total_tokens": 7}, "openai_chat")

        self.assertEqual(5, usage["cache_read_input_tokens"])
        self.assertEqual(7, total_only["total_tokens"])
        self.assertTrue(total_only["_total_explicit"])

    def test_usage_without_cache_details_marks_cache_usage_unknown(self) -> None:
        usage = extract_canonical_usage(
            {"usage": {"prompt_tokens": 12, "completion_tokens": 3}},
            "openai_chat",
        )

        self.assertEqual("unknown", usage["cache_usage_status"])

    def test_claude_stream_usage_merges_message_start_and_delta(self) -> None:
        meta = ProxyResponseBuilder._create_empty_meta()

        ProxyResponseBuilder._update_meta_from_payload(
            meta,
            {
                "type": "message_start",
                "message": {
                    "model": "claude-sonnet",
                    "usage": {
                        "input_tokens": 11,
                        "cache_read_input_tokens": 2,
                        "output_tokens": 0,
                    },
                },
            },
            source_format="claude_chat",
        )
        ProxyResponseBuilder._update_meta_from_payload(
            meta,
            {
                "type": "message_delta",
                "usage": {"output_tokens": 3},
            },
            source_format="claude_chat",
        )

        self.assertEqual("claude-sonnet", meta["response_model"])
        self.assertEqual(13, meta["prompt_tokens"])
        self.assertEqual(3, meta["completion_tokens"])
        self.assertEqual(16, meta["total_tokens"])
        self.assertEqual("known", meta["usage_status"])

    def test_claude_stream_usage_uses_latest_explicit_cache_values(self) -> None:
        meta = ProxyResponseBuilder._create_empty_meta()

        ProxyResponseBuilder._update_meta_from_payload(
            meta,
            {
                "type": "message_start",
                "message": {
                    "usage": {
                        "input_tokens": 13,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 100,
                        "cache_creation_input_tokens": 7,
                    }
                },
            },
            source_format="claude_chat",
        )
        ProxyResponseBuilder._update_meta_from_payload(
            meta,
            {
                "type": "message_delta",
                "usage": {
                    "output_tokens": 4,
                    "cache_read_input_tokens": 22000,
                    "cache_creation_input_tokens": 31,
                },
            },
            source_format="claude_chat",
        )

        self.assertEqual(22044, meta["prompt_tokens"])
        self.assertEqual(4, meta["completion_tokens"])
        self.assertEqual(22048, meta["total_tokens"])
        self.assertEqual(22000, meta["cache_read_input_tokens"])
        self.assertEqual(31, meta["cache_creation_input_tokens"])

    def test_claude_nested_thinking_tokens_are_preserved(self) -> None:
        usage = extract_canonical_usage(
            {
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 244,
                    "cache_creation_input_tokens": 831,
                    "cache_read_input_tokens": 44225,
                    "output_tokens_details": {"thinking_tokens": 40},
                }
            },
            "claude_chat",
        )

        self.assertEqual(45058, usage["prompt_tokens"])
        self.assertEqual(244, usage["completion_tokens"])
        self.assertEqual(45302, usage["total_tokens"])
        self.assertEqual(40, usage["reasoning_tokens"])

        details_only = extract_canonical_usage(
            {"output_tokens_details": {"reasoning_tokens": 0}},
            "claude_chat",
        )
        self.assertEqual(0, details_only["reasoning_tokens"])
        self.assertTrue(details_only["_reasoning_present"])

    def test_translated_responses_usage_is_not_reinterpreted_as_claude_usage(self) -> None:
        meta = ProxyResponseBuilder._create_empty_meta()
        ProxyResponseBuilder._update_meta_from_payload(
            meta,
            {
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 3,
                    "cache_read_input_tokens": 4,
                    "cache_creation_input_tokens": 2,
                }
            },
            source_format="claude_chat",
        )
        ProxyResponseBuilder._update_meta_from_payload(
            meta,
            {
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 3,
                    "total_tokens": 14,
                    "input_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 2},
                }
            },
            source_format="openai_responses",
        )

        self.assertEqual(11, meta["prompt_tokens"])
        self.assertEqual(3, meta["completion_tokens"])
        self.assertEqual(14, meta["total_tokens"])

    def test_openai_usage_without_total_recomputes_total(self) -> None:
        meta = ProxyResponseBuilder._create_empty_meta()
        ProxyResponseBuilder._update_meta_from_payload(
            meta,
            {"model": "gpt-4.1", "usage": {"prompt_tokens": 11, "completion_tokens": 3}},
            source_format="openai_chat",
        )

        self.assertEqual(14, meta["total_tokens"])
        self.assertEqual("known", meta["usage_status"])

    def test_openai_chat_to_claude_splits_cached_input(self) -> None:
        translator = OpenAIChatClaudeTranslator()
        response = translator.translate_nonstream_response(
            "gpt-4.1",
            {},
            {"model": "gpt-4.1"},
            {
                "id": "chatcmpl-1",
                "model": "gpt-4.1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 4},
                },
            },
        )

        self.assertEqual(
            {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 4},
            response["usage"],
        )

    def test_claude_to_openai_preserves_cache_categories_and_total(self) -> None:
        translator = ClaudeChatTranslator()
        response = translator.translate_nonstream_response(
            "claude-sonnet",
            {},
            {"model": "claude-sonnet"},
            {
                "id": "msg-1",
                "model": "claude-sonnet",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 5,
                    "cache_creation_input_tokens": 2,
                    "cache_read_input_tokens": 4,
                    "output_tokens": 3,
                },
            },
        )

        usage = response["usage"]
        self.assertEqual(11, usage["prompt_tokens"])
        self.assertEqual(3, usage["completion_tokens"])
        self.assertEqual(14, usage["total_tokens"])
        self.assertEqual(4, usage["prompt_tokens_details"]["cached_tokens"])
        self.assertEqual(2, usage["prompt_tokens_details"]["cache_creation_tokens"])

    def test_responses_to_claude_keeps_usage_details(self) -> None:
        translator = build_default_translator_registry().get("openai_responses", "claude_chat")
        response = translator.translate_nonstream_response(
            "gpt-5.4",
            {},
            {"model": "gpt-5.4"},
            {
                "id": "resp-1",
                "model": "gpt-5.4",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 3,
                    "total_tokens": 14,
                    "input_tokens_details": {"cached_tokens": 4},
                    "output_tokens_details": {"reasoning_tokens": 1},
                },
            },
        )

        self.assertEqual(7, response["usage"]["input_tokens"])
        self.assertEqual(4, response["usage"]["cache_read_input_tokens"])
        self.assertEqual(3, response["usage"]["output_tokens"])
        self.assertEqual(1, response["usage"]["thinking_tokens"])

    def test_responses_to_claude_preserves_known_zero_cache_usage(self) -> None:
        translator = build_default_translator_registry().get("openai_responses", "claude_chat")
        response = translator.translate_nonstream_response(
            "gpt-5.4",
            {},
            {"model": "gpt-5.4"},
            {
                "id": "resp-zero-cache",
                "output": [],
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 3,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                },
            },
        )

        self.assertEqual(0, response["usage"]["cache_read_input_tokens"])
        self.assertEqual(0, response["usage"]["cache_creation_input_tokens"])
        normalized = extract_canonical_usage(response, "claude_chat")
        self.assertEqual("known", normalized["cache_usage_status"])

    def test_openai_direct_bridges_preserve_known_zero_cache_usage(self) -> None:
        registry = build_default_translator_registry()
        responses_response = registry.get("openai_chat", "openai_responses").translate_nonstream_response(
            "gpt-4.1",
            {},
            {"model": "gpt-4.1"},
            {
                "id": "chatcmpl-zero-cache",
                "model": "gpt-4.1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                },
            },
        )
        chat_response = registry.get("openai_responses", "openai_chat").translate_nonstream_response(
            "gpt-4.1",
            {},
            {"model": "gpt-4.1"},
            {
                "id": "resp-zero-cache",
                "model": "gpt-4.1",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 3,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                },
            },
        )

        self.assertEqual(
            {"cached_tokens": 0, "cache_write_tokens": 0},
            responses_response["usage"]["input_tokens_details"],
        )
        self.assertEqual(
            {"cached_tokens": 0, "cache_write_tokens": 0},
            chat_response["usage"]["prompt_tokens_details"],
        )

    def test_responses_to_chat_preserves_explicit_all_zero_usage(self) -> None:
        response = (
            build_default_translator_registry()
            .get("openai_responses", "openai_chat")
            .translate_nonstream_response(
                "gpt-4.1",
                {},
                {"model": "gpt-4.1"},
                {
                    "id": "resp-zero-usage",
                    "model": "gpt-4.1",
                    "output": [],
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "input_tokens_details": {"cached_tokens": 0},
                    },
                },
            )
        )

        self.assertEqual(0, response["usage"]["total_tokens"])
        self.assertEqual(0, response["usage"]["prompt_tokens_details"]["cached_tokens"])

    def test_output_token_limit_aliases_are_forwarded(self) -> None:
        claude_request = OpenAIChatClaudeTranslator().translate_request(
            "gpt-4.1",
            {"messages": [], "max_completion_tokens": 128},
            False,
        )
        self.assertEqual(128, claude_request["max_tokens"])

        responses_request = (
            build_default_translator_registry()
            .get("openai_chat", "openai_responses")
            .translate_request(
                "gpt-4.1",
                {"input": "hi", "max_completion_tokens": 128},
                False,
            )
        )
        self.assertEqual(128, responses_request["max_tokens"])


if __name__ == "__main__":
    unittest.main()
