#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Anthropic billing header 兼容辅助。"""

from __future__ import annotations

import json
import re
from typing import Any

CLAUDE_CCH_SEED = 0x6E52736AC806831E
CLAUDE_BILLING_HEADER_PREFIX = "x-anthropic-billing-header:"
CLAUDE_BILLING_CCH_PATTERN = re.compile(r"\bcch=([0-9a-f]{5});")
_XXH64_MASK = 0xFFFFFFFFFFFFFFFF
_XXH64_PRIME_1 = 11400714785074694791
_XXH64_PRIME_2 = 14029467366897019727
_XXH64_PRIME_3 = 1609587929392839161
_XXH64_PRIME_4 = 9650029242287828579
_XXH64_PRIME_5 = 2870177450012600261


def resign_anthropic_messages_body_cch(body: dict[str, Any]) -> bool:
    """对已有 Claude Code billing header 重新计算 cch。"""
    system = body.get("system")
    if not isinstance(system, list) or not system or not isinstance(system[0], dict):
        return False

    billing_header = system[0].get("text")
    if not isinstance(billing_header, str) or not billing_header.startswith(CLAUDE_BILLING_HEADER_PREFIX):
        return False
    if not CLAUDE_BILLING_CCH_PATTERN.search(billing_header):
        return False

    unsigned_billing_header = CLAUDE_BILLING_CCH_PATTERN.sub("cch=00000;", billing_header, count=1)
    system[0]["text"] = unsigned_billing_header
    unsigned_body = _json_body_bytes_for_requests(body)
    cch = f"{_xxhash64(unsigned_body, CLAUDE_CCH_SEED) & 0xFFFFF:05x}"
    system[0]["text"] = CLAUDE_BILLING_CCH_PATTERN.sub(f"cch={cch};", unsigned_billing_header, count=1)
    return True


def _json_body_bytes_for_requests(body: dict[str, Any]) -> bytes:
    return json.dumps(body, allow_nan=False).encode("utf-8")


def _xxhash64(data: bytes, seed: int = 0) -> int:
    """返回 xxHash64，避免为 CPA 的 cch 签名额外增加运行时依赖。"""
    length = len(data)
    index = 0
    seed &= _XXH64_MASK

    if length >= 32:
        v1 = (seed + _XXH64_PRIME_1 + _XXH64_PRIME_2) & _XXH64_MASK
        v2 = (seed + _XXH64_PRIME_2) & _XXH64_MASK
        v3 = seed
        v4 = (seed - _XXH64_PRIME_1) & _XXH64_MASK
        limit = length - 32
        while index <= limit:
            v1 = _xxhash64_round(v1, int.from_bytes(data[index : index + 8], "little"))
            index += 8
            v2 = _xxhash64_round(v2, int.from_bytes(data[index : index + 8], "little"))
            index += 8
            v3 = _xxhash64_round(v3, int.from_bytes(data[index : index + 8], "little"))
            index += 8
            v4 = _xxhash64_round(v4, int.from_bytes(data[index : index + 8], "little"))
            index += 8
        value = (
            _xxhash64_rotl(v1, 1)
            + _xxhash64_rotl(v2, 7)
            + _xxhash64_rotl(v3, 12)
            + _xxhash64_rotl(v4, 18)
        ) & _XXH64_MASK
        value = _xxhash64_merge_round(value, v1)
        value = _xxhash64_merge_round(value, v2)
        value = _xxhash64_merge_round(value, v3)
        value = _xxhash64_merge_round(value, v4)
    else:
        value = (seed + _XXH64_PRIME_5) & _XXH64_MASK

    value = (value + length) & _XXH64_MASK
    while index + 8 <= length:
        lane = int.from_bytes(data[index : index + 8], "little")
        value ^= _xxhash64_round(0, lane)
        value = (_xxhash64_rotl(value, 27) * _XXH64_PRIME_1 + _XXH64_PRIME_4) & _XXH64_MASK
        index += 8
    if index + 4 <= length:
        value ^= (int.from_bytes(data[index : index + 4], "little") * _XXH64_PRIME_1) & _XXH64_MASK
        value = (_xxhash64_rotl(value, 23) * _XXH64_PRIME_2 + _XXH64_PRIME_3) & _XXH64_MASK
        index += 4
    while index < length:
        value ^= (data[index] * _XXH64_PRIME_5) & _XXH64_MASK
        value = (_xxhash64_rotl(value, 11) * _XXH64_PRIME_1) & _XXH64_MASK
        index += 1

    value ^= value >> 33
    value = (value * _XXH64_PRIME_2) & _XXH64_MASK
    value ^= value >> 29
    value = (value * _XXH64_PRIME_3) & _XXH64_MASK
    value ^= value >> 32
    return value & _XXH64_MASK


def _xxhash64_rotl(value: int, count: int) -> int:
    return ((value << count) | (value >> (64 - count))) & _XXH64_MASK


def _xxhash64_round(acc: int, lane: int) -> int:
    acc = (acc + lane * _XXH64_PRIME_2) & _XXH64_MASK
    acc = _xxhash64_rotl(acc, 31)
    return (acc * _XXH64_PRIME_1) & _XXH64_MASK


def _xxhash64_merge_round(acc: int, value: int) -> int:
    acc ^= _xxhash64_round(0, value)
    return (acc * _XXH64_PRIME_1 + _XXH64_PRIME_4) & _XXH64_MASK
