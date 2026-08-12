import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.proxy_response_builder import ProxyResponseBuilder
from src.translators import ClaudeChatTranslator, OpenAIChatClaudeTranslator, build_default_translator_registry


class TokenUsageTests(unittest.TestCase):
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
