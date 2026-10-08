from __future__ import annotations

import base64
import copy
import json
import unittest
from typing import Any

from src.translators import build_default_translator_registry
from src.translators.tool_result_utils import UnsupportedToolResultContent

FORMATS = ("openai_chat", "openai_responses", "claude_chat")
IMAGE_DATA = "c2NyZWVuc2hvdA=="
PDF_DATA = "JVBERi0xLjQK"
IMAGE_URL = f"data:image/png;base64,{IMAGE_DATA}"
PDF_URL = f"data:application/pdf;base64,{PDF_DATA}"


def media_content(protocol: str) -> list[dict[str, Any]]:
    """构造三个协议中内容相同的工具输出。"""
    if protocol == "openai_chat":
        return [
            {"type": "text", "text": "tool text"},
            {"type": "image_url", "image_url": {"url": IMAGE_URL}},
            {"type": "file", "file": {"file_data": PDF_URL, "filename": "report.pdf"}},
        ]
    if protocol == "openai_responses":
        return [
            {"type": "input_text", "text": "tool text"},
            {"type": "input_image", "image_url": IMAGE_URL},
            {"type": "input_file", "file_data": PDF_URL, "filename": "report.pdf"},
        ]
    return [
        {"type": "text", "text": "tool text"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": IMAGE_DATA}},
        {
            "type": "document",
            "title": "report.pdf",
            "source": {"type": "base64", "media_type": "application/pdf", "data": PDF_DATA},
        },
    ]


def tool_request(protocol: str, contents: list[Any]) -> dict[str, Any]:
    """构造已配对的单个或并行工具调用历史。"""
    call_ids = [f"call_{index}" for index in range(len(contents))]
    if protocol == "openai_responses":
        return {
            "input": [
                {"type": "function_call", "call_id": call_id, "name": "capture", "arguments": "{}"}
                for call_id in call_ids
            ]
            + [
                {"type": "function_call_output", "call_id": call_id, "output": content}
                for call_id, content in zip(call_ids, contents, strict=True)
            ]
        }
    if protocol == "openai_chat":
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": call_id, "type": "function", "function": {"name": "capture", "arguments": "{}"}}
                        for call_id in call_ids
                    ],
                },
                *[
                    {"role": "tool", "tool_call_id": call_id, "content": content}
                    for call_id, content in zip(call_ids, contents, strict=True)
                ],
            ]
        }
    return {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": call_id, "name": "capture", "input": {}} for call_id in call_ids
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call_id, "content": content}
                    for call_id, content in zip(call_ids, contents, strict=True)
                ],
            },
        ]
    }


def tool_outputs(body: dict[str, Any], protocol: str) -> list[Any]:
    if protocol == "openai_responses":
        return [item["output"] for item in body["input"] if item["type"].endswith("call_output")]
    if protocol == "openai_chat":
        return [message["content"] for message in body["messages"] if message["role"] == "tool"]
    return [
        part["content"] for message in body["messages"] for part in message["content"] if part["type"] == "tool_result"
    ]


class ToolResultMediaTests(unittest.TestCase):
    def translate(self, source: str, target: str, body: dict[str, Any], *, stream: bool = False) -> dict[str, Any]:
        return build_default_translator_registry().get(target, source).translate_request("example", body, stream)

    def test_six_directions_preserve_text_image_and_pdf(self) -> None:
        for source in FORMATS:
            for target in FORMATS:
                if source == target:
                    continue
                for stream in (False, True):
                    with self.subTest(source=source, target=target, stream=stream):
                        request = tool_request(source, [media_content(source)])
                        original = copy.deepcopy(request)
                        body = self.translate(source, target, request, stream=stream)
                        self.assertEqual(original, request)
                        self.assertEqual(stream, body["stream"])
                        serialized = json.dumps(body)
                        self.assertEqual(1, serialized.count(IMAGE_DATA))
                        self.assertEqual(1, serialized.count(PDF_DATA))
                        self.assertIn("tool text", serialized)
                        self.assertIn("report.pdf", serialized)
                        output = tool_outputs(body, target)[0]
                        if target == "openai_chat":
                            self.assertEqual("tool text", output)
                            self.assertEqual(["assistant", "tool", "user"], [m["role"] for m in body["messages"]])
                            relay = body["messages"][-1]["content"]
                            self.assertEqual(["text", "image_url", "file"], [part["type"] for part in relay])
                            self.assertIn("call_0", relay[0]["text"])
                        else:
                            expected = (
                                ["input_text", "input_image", "input_file"]
                                if target == "openai_responses"
                                else ["text", "image", "document"]
                            )
                            self.assertEqual(expected, [part["type"] for part in output])

    def test_parallel_chat_tool_results_precede_all_relayed_media(self) -> None:
        for source in ("openai_responses", "claude_chat"):
            with self.subTest(source=source):
                request = tool_request(source, [media_content(source), media_content(source)])
                if source == "openai_responses":
                    request["input"].insert(
                        3, {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "continue"}]}
                    )
                else:
                    request["messages"][1]["content"].append({"type": "text", "text": "continue"})
                body = self.translate(source, "openai_chat", request)
                self.assertEqual(["assistant", "tool", "tool", "user"], [m["role"] for m in body["messages"]])
                self.assertEqual(["call_0", "call_1"], [m["tool_call_id"] for m in body["messages"][1:3]])
                relay = body["messages"][-1]["content"]
                self.assertEqual(2, sum(part["type"] == "image_url" for part in relay))
                self.assertEqual(2, sum(part["type"] == "file" for part in relay))
                self.assertEqual("continue", relay[-1]["text"])
                self.assertIn("call_0", relay[0]["text"])
                self.assertIn("call_1", relay[3]["text"])

    def test_parallel_claude_results_in_separate_user_messages_precede_relayed_media(self) -> None:
        request = tool_request("claude_chat", [media_content("claude_chat"), media_content("claude_chat")])
        results = request["messages"].pop()["content"]
        request["messages"].extend({"role": "user", "content": [result]} for result in results)
        request["messages"].append({"role": "user", "content": "continue"})
        original = copy.deepcopy(request)
        body = self.translate("claude_chat", "openai_chat", request)
        self.assertEqual(original, request)
        self.assertEqual(["assistant", "tool", "tool", "user"], [message["role"] for message in body["messages"]])
        self.assertEqual(["call_0", "call_1"], [message["tool_call_id"] for message in body["messages"][1:3]])
        relay = body["messages"][-1]["content"]
        self.assertEqual(2, sum(part["type"] == "image_url" for part in relay))
        self.assertEqual(2, sum(part["type"] == "file" for part in relay))
        self.assertEqual("continue", relay[-1]["text"])

    def test_stringified_media_and_single_media_objects_are_recognized(self) -> None:
        for source in FORMATS:
            for target in FORMATS:
                if source == target:
                    continue
                parts = media_content(source)
                for content in (json.dumps(parts), parts[1]):
                    with self.subTest(source=source, target=target, content_type=type(content).__name__):
                        body = self.translate(source, target, tool_request(source, [content]))
                        self.assertEqual(1, json.dumps(body).count(IMAGE_DATA))
                        if target == "openai_chat":
                            self.assertNotIn(IMAGE_DATA, tool_outputs(body, target)[0])
                        else:
                            self.assertIsInstance(tool_outputs(body, target)[0], list)

    def test_plain_text_and_ordinary_json_strings_remain_verbatim(self) -> None:
        texts = ["done", '{"result": [{"name": "image", "value": 3}]}', "[1, 2, 3]", ""]
        for source in FORMATS:
            for target in FORMATS:
                if source == target:
                    continue
                with self.subTest(source=source, target=target):
                    body = self.translate(source, target, tool_request(source, texts))
                    self.assertEqual(texts, tool_outputs(body, target))

    def test_unknown_nonmedia_blocks_survive_mixed_results(self) -> None:
        unknown = {"type": "resource_reference", "uri": "resource://example", "value": 42}
        for source in FORMATS:
            for target in FORMATS:
                if source == target:
                    continue
                with self.subTest(source=source, target=target):
                    body = self.translate(source, target, tool_request(source, [media_content(source) + [unknown]]))
                    output = tool_outputs(body, target)[0]
                    text = output if isinstance(output, str) else "\n".join(part.get("text", "") for part in output)
                    self.assertIn(json.dumps(unknown), text)

    def test_claude_text_documents_become_text_in_both_targets(self) -> None:
        content = [
            {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "document body"}}
        ]
        for target in ("openai_chat", "openai_responses"):
            with self.subTest(target=target):
                body = self.translate("claude_chat", target, tool_request("claude_chat", [content]))
                output = tool_outputs(body, target)[0]
                self.assertEqual("document body", output if isinstance(output, str) else output[0]["text"])
                self.assertNotIn("base64", json.dumps(body))

    def test_url_documents_round_trip_between_claude_and_responses(self) -> None:
        url = "https://example.com/report.pdf"
        content = [{"type": "document", "source": {"type": "url", "url": url}}]
        body = self.translate("claude_chat", "openai_responses", tool_request("claude_chat", [content]))
        self.assertEqual(url, tool_outputs(body, "openai_responses")[0][0]["file_url"])
        back = self.translate("openai_responses", "claude_chat", body)
        self.assertEqual({"type": "url", "url": url}, tool_outputs(back, "claude_chat")[0][0]["source"])

    def test_url_images_survive_claude_tool_results(self) -> None:
        url = "https://example.com/screenshot.png"
        content = [{"type": "image", "source": {"type": "url", "url": url}}]
        for target in ("openai_chat", "openai_responses"):
            with self.subTest(target=target):
                body = self.translate("claude_chat", target, tool_request("claude_chat", [content]))
                self.assertIn(url, json.dumps(body))
                if target == "openai_chat":
                    self.assertNotIn(url, tool_outputs(body, target)[0])
                    self.assertEqual(url, body["messages"][-1]["content"][1]["image_url"]["url"])
                else:
                    self.assertEqual(url, tool_outputs(body, target)[0][0]["image_url"])

    def test_custom_tool_output_keeps_media_and_call_id(self) -> None:
        for source in ("openai_chat", "openai_responses"):
            for target in FORMATS:
                if source == target:
                    continue
                with self.subTest(source=source, target=target):
                    request = tool_request(source, [media_content(source)])
                    if source == "openai_chat":
                        request["messages"][0]["tool_calls"] = [
                            {"id": "call_0", "type": "custom", "custom": {"name": "capture", "input": "capture"}}
                        ]
                    else:
                        request["input"][0] = {
                            "type": "custom_tool_call",
                            "call_id": "call_0",
                            "name": "capture",
                            "input": "capture",
                        }
                        request["input"][1]["type"] = "custom_tool_call_output"
                    body = self.translate(source, target, request)
                    self.assertEqual(1, json.dumps(body).count(IMAGE_DATA))
                    self.assertEqual(1, json.dumps(body).count(PDF_DATA))
                    if target == "openai_chat":
                        self.assertEqual("call_0", body["messages"][1]["tool_call_id"])
                    elif target == "openai_responses":
                        output = next(item for item in body["input"] if item["type"] == "custom_tool_call_output")
                        self.assertEqual("call_0", output["call_id"])
                    else:
                        self.assertEqual("call_0", body["messages"][-1]["content"][0]["tool_use_id"])

    def test_missing_media_payload_is_rejected_even_beside_valid_text(self) -> None:
        cases = (
            ("openai_chat", "openai_responses", {"type": "file", "file": {"filename": "empty.pdf"}}),
            ("openai_responses", "openai_chat", {"type": "input_file", "filename": "empty.pdf"}),
            ("openai_responses", "claude_chat", {"type": "input_image", "image_url": ""}),
            ("claude_chat", "openai_responses", {"type": "image", "source": {"type": "base64", "data": ""}}),
        )
        for source, target, part in cases:
            with self.subTest(source=source, target=target):
                with self.assertRaises(UnsupportedToolResultContent):
                    self.translate(source, target, tool_request(source, [media_content(source) + [part]]))

    def test_unrepresentable_file_sources_raise_instead_of_disappearing(self) -> None:
        cases = (
            (
                "claude_chat",
                "openai_chat",
                {"type": "document", "source": {"type": "url", "url": "https://example.com/a.pdf"}},
            ),
            ("openai_responses", "openai_chat", {"type": "input_file", "file_url": "https://example.com/a.pdf"}),
            ("openai_responses", "claude_chat", {"type": "input_file", "file_id": "file-1"}),
            ("openai_chat", "claude_chat", {"type": "file", "file": {"file_id": "file-1"}}),
            (
                "openai_responses",
                "claude_chat",
                {"type": "input_file", "file_data": "data:application/zip;base64,UEsDBA==", "filename": "archive.zip"},
            ),
        )
        for source, target, part in cases:
            with self.subTest(source=source, target=target):
                with self.assertRaises(UnsupportedToolResultContent):
                    self.translate(source, target, tool_request(source, [media_content(source) + [part]]))

    def test_utf8_text_files_become_claude_text_documents(self) -> None:
        text = "文档正文"
        file_data = "data:text/plain;base64," + base64.b64encode(text.encode("utf-8")).decode("ascii")
        for source in ("openai_chat", "openai_responses"):
            with self.subTest(source=source):
                file_payload = {"file_data": file_data, "filename": "report.txt"}
                part = (
                    {"type": "file", "file": file_payload}
                    if source == "openai_chat"
                    else {"type": "input_file", **file_payload}
                )
                body = self.translate(source, "claude_chat", tool_request(source, [[part]]))
                document = tool_outputs(body, "claude_chat")[0][0]
                self.assertEqual({"type": "text", "media_type": "text/plain", "data": text}, document["source"])
                self.assertEqual("report.txt", document["title"])

    def test_cache_control_is_hoisted_onto_claude_tool_result(self) -> None:
        for source in ("openai_chat", "openai_responses"):
            with self.subTest(source=source):
                content = media_content(source)
                content[-1]["cache_control"] = {"type": "ephemeral"}
                request = tool_request(source, [content])
                original = copy.deepcopy(request)
                body = self.translate(source, "claude_chat", request)
                block = body["messages"][-1]["content"][0]
                self.assertEqual({"type": "ephemeral"}, block["cache_control"])
                self.assertTrue(all("cache_control" not in part for part in block["content"]))
                self.assertEqual(original, request)

    def test_ordinary_user_files_retain_existing_base64_fallback(self) -> None:
        file_payload = {"file_data": "data:application/zip;base64,UEsDBA==", "filename": "archive.zip"}
        for source in ("openai_chat", "openai_responses"):
            with self.subTest(source=source):
                part = (
                    {"type": "file", "file": file_payload}
                    if source == "openai_chat"
                    else {"type": "input_file", **file_payload}
                )
                message = {"role": "user", "content": [part]}
                request = {"messages": [message]} if source == "openai_chat" else {"input": [message]}
                body = self.translate(source, "claude_chat", request)
                self.assertEqual(
                    [
                        {
                            "type": "document",
                            "source": {"type": "base64", "media_type": "application/zip", "data": "UEsDBA=="},
                        }
                    ],
                    body["messages"][0]["content"],
                )

    def test_ordinary_responses_user_files_retain_raw_base64_fallback(self) -> None:
        request = {"input": [{"role": "user", "content": [{"type": "input_file", "file_data": "UEsDBA=="}]}]}
        body = self.translate("openai_responses", "claude_chat", request)
        self.assertEqual(
            [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/octet-stream", "data": "UEsDBA=="},
                }
            ],
            body["messages"][0]["content"],
        )

    def test_same_protocol_preserves_original_tool_content(self) -> None:
        for protocol in FORMATS:
            with self.subTest(protocol=protocol):
                request = tool_request(protocol, [media_content(protocol)])
                original = copy.deepcopy(request)
                body = self.translate(protocol, protocol, request)
                self.assertEqual(original, request)
                self.assertEqual(tool_outputs(original, protocol), tool_outputs(body, protocol))


if __name__ == "__main__":
    unittest.main()
