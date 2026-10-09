"""Codex 加密推理历史在跨协议消息中的无损载体。"""

from __future__ import annotations

import base64
import json
from typing import Any

CODEX_REASONING_PREFIX = "codex#"


def encode_codex_reasoning(item: dict[str, Any]) -> str:
    """把原始推理项编码为 Claude 客户端可回传的签名。"""
    raw = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return CODEX_REASONING_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")


def decode_codex_reasoning(value: Any) -> dict[str, Any] | None:
    """只接收明确标记为 Codex 的推理项，不转发其他 Provider 的签名。"""
    if not isinstance(value, str) or not value.startswith(CODEX_REASONING_PREFIX) or len(value) > 32 * 1024 * 1024:
        return None
    try:
        raw = base64.b64decode(value[len(CODEX_REASONING_PREFIX) :], altchars=b"-_", validate=True)
        item = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return None
    if not isinstance(item.get("encrypted_content"), str) or not item["encrypted_content"]:
        return None
    return item
