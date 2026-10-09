"""按调用方、模型、账号和历史前缀隔离的 Codex 推理重放缓存。"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any


def _history_token(item: dict[str, Any]) -> bytes:
    """忽略响应状态和消息 ID，保留会话内容与工具调用关联。"""
    kind = item.get("type", "message")
    if kind == "message":
        content = item.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            content = [
                {"type": "text", "text": part.get("text")}
                if isinstance(part, dict) and part.get("type") in {"input_text", "output_text", "text"}
                else part
                for part in content
            ]
        value = {"type": kind, "role": item.get("role"), "content": content}
    elif kind in {"function_call", "custom_tool_call"}:
        value = {key: item.get(key) for key in ("type", "call_id", "name", "namespace")}
        arguments = item.get("arguments") if kind == "function_call" else item.get("input")
        if kind == "function_call" and isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                pass
        value["arguments"] = arguments
    else:
        value = {key: val for key, val in item.items() if key not in {"id", "status"}}
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


class CodexReasoningCache:
    """只缓存成功响应的推理项，最多 512 项、8 MiB，保留一小时。"""

    def __init__(self) -> None:
        self._entries: OrderedDict[tuple[str, str], tuple[float, int, list[dict[str, Any]]]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()

    def _get(self, scope: str, fingerprint: str) -> list[dict[str, Any]]:
        key = (scope, fingerprint)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return []
            if time.monotonic() - entry[0] >= 3600:
                self._bytes -= self._entries.pop(key)[1]
                return []
            self._entries.move_to_end(key)
            return copy.deepcopy(entry[2])

    def _put(self, scope: str, fingerprint: str, items: list[dict[str, Any]]) -> None:
        size = len(json.dumps(items, ensure_ascii=False).encode("utf-8"))
        if size > 1024 * 1024:
            return
        key = (scope, fingerprint)
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
            self._entries[key] = (time.monotonic(), size, copy.deepcopy(items))
            self._bytes += size
            while len(self._entries) > 512 or self._bytes > 8 * 1024 * 1024:
                self._bytes -= self._entries.popitem(last=False)[1][1]

    def restore(self, scope: str, body: dict[str, Any]) -> None:
        """仅在完整历史前缀和输出锚点一致时插入缺失的推理项。"""
        digest = self._digest(body)
        restored: list[Any] = []
        seen: set[str] = set()
        items = body.get("input")
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                restored.append(item)
                continue
            if item.get("type") == "reasoning":
                seen.add(str(item.get("encrypted_content") or ""))
            else:
                digest.update(_history_token(item))
                for reasoning in self._get(scope, digest.hexdigest()):
                    encrypted = str(reasoning.get("encrypted_content") or "")
                    if encrypted and encrypted not in seen:
                        restored.append(reasoning)
                        seen.add(encrypted)
            restored.append(item)
        body["input"] = restored

    def record(self, scope: str, body: dict[str, Any], output: list[Any]) -> None:
        """保存每段推理及其随后输出项对应的完整历史指纹。"""
        digest = self._digest(body)
        for item in body.get("input") or []:
            if isinstance(item, dict) and item.get("type") != "reasoning":
                digest.update(_history_token(item))
        pending: list[dict[str, Any]] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "reasoning":
                if (
                    isinstance(item.get("encrypted_content"), str)
                    and item["encrypted_content"]
                    and len(str(item.get("id") or "")) <= 64
                ):
                    pending.append(item)
                continue
            digest.update(_history_token(item))
            if pending:
                self._put(scope, digest.hexdigest(), pending)
                pending = []

    @staticmethod
    def _digest(body: dict[str, Any]) -> Any:
        """系统指令和工具定义也属于历史匹配范围。"""
        context = {key: body.get(key) for key in ("instructions", "tools", "tool_choice")}
        return hashlib.sha256(
            json.dumps(context, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )

    def clear(self, scope: str) -> None:
        """上游拒绝推理签名时清除对应隔离范围。"""
        with self._lock:
            for key in list(self._entries):
                if key[0] == scope:
                    self._bytes -= self._entries.pop(key)[1]
