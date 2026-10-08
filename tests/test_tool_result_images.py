import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.translators.claude_bridge import convert_claude_request_to_openai_chat_request
from src.translators.tool_result_utils import (
    normalize_tool_result_content,
    split_tool_result_content,
)

B64 = "A" * 64


def image_block(media_type: str = "image/jpeg") -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": B64}}


class SplitToolResultContentTest(unittest.TestCase):
    def test_string_content_is_passed_through(self):
        self.assertEqual(split_tool_result_content("hello"), ("hello", []))

    def test_block_list_without_binary_blocks_keeps_legacy_json_dump(self):
        content = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        text, binary = split_tool_result_content(content)
        self.assertEqual(text, json.dumps(content, ensure_ascii=False))
        self.assertEqual(binary, [])

    def test_image_block_is_hoisted_and_excluded_from_text(self):
        content = [{"type": "text", "text": "图来了"}, image_block()]
        text, binary = split_tool_result_content(content)
        self.assertEqual(text, "图来了")
        self.assertEqual(len(binary), 1)
        self.assertNotIn(B64, text)

    def test_image_only_content_gets_a_placeholder(self):
        text, binary = split_tool_result_content([image_block()])
        self.assertEqual(len(binary), 1)
        self.assertTrue(text)
        self.assertNotIn(B64, text)

    def test_document_block_is_hoisted(self):
        content = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": B64}}]
        text, binary = split_tool_result_content(content)
        self.assertEqual(len(binary), 1)
        self.assertNotIn(B64, text)


class NormalizeToolResultContentTest(unittest.TestCase):
    def test_string_and_plain_json_unchanged(self):
        self.assertEqual(normalize_tool_result_content("hi"), "hi")
        payload = {"a": 1}
        self.assertEqual(normalize_tool_result_content(payload), json.dumps(payload, ensure_ascii=False))

    def test_binary_blocks_are_not_inlined(self):
        content = [{"type": "text", "text": "看到没"}, image_block()]
        text = normalize_tool_result_content(content)
        self.assertNotIn(B64, text)
        self.assertIn("未内联", text)


class ConvertClaudeRequestHoistingTest(unittest.TestCase):
    def _translate(self, tool_result_content, second_tool_result=None):
        body = {
            "model": "m",
            "max_tokens": 16,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "读三张图"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x/a.jpg"}},
                        {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/x/b.jpg"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": tool_result_content},
                        {
                            "type": "tool_result",
                            "tool_use_id": "t2",
                            "content": second_tool_result if second_tool_result is not None else [image_block()],
                        },
                    ],
                },
            ],
        }
        return convert_claude_request_to_openai_chat_request("m", body, False)["messages"]

    def test_no_base64_leaks_into_tool_messages(self):
        messages = self._translate([{"type": "text", "text": "图来了"}, image_block()])
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 2)
        for message in tool_messages:
            self.assertIsInstance(message["content"], str)
            self.assertNotIn(B64, message["content"])

    def test_images_are_hoisted_into_a_following_user_message(self):
        messages = self._translate([{"type": "text", "text": "图来了"}, image_block()])
        # assistant(tool_calls) -> tool -> tool -> user(images)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool", "tool", "user"])
        hoisted = messages[-1]["content"]
        self.assertIsInstance(hoisted, list)
        self.assertEqual(len(hoisted), 2)
        for item in hoisted:
            self.assertEqual(item["type"], "image_url")
            self.assertTrue(item["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_text_only_tool_result_keeps_its_own_user_message(self):
        messages = self._translate("就是一段文本", "另一段文本")
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool", "tool"])
        self.assertEqual(messages[-1]["content"], "另一段文本")

    def test_tool_call_ids_stay_paired(self):
        messages = self._translate([image_block()])
        assistant = next(m for m in messages if m.get("role") == "assistant")
        called = {call["id"] for call in assistant["tool_calls"]}
        answered = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
        self.assertEqual(called, answered)


if __name__ == "__main__":
    unittest.main()
