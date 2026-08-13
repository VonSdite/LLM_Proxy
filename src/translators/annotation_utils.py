#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI 与 Claude 引用标注结构转换辅助。"""

from __future__ import annotations

import copy
from typing import Any


def chat_annotations_to_responses(annotations: Any) -> list[dict[str, Any]]:
    """把 Chat URL citation 转成 Responses annotation。"""
    if not isinstance(annotations, list):
        return []
    translated: list[dict[str, Any]] = []
    for annotation in annotations:
        if not isinstance(annotation, dict):
            continue
        if str(annotation.get("type") or "").strip().lower() != "url_citation":
            continue
        citation = annotation.get("url_citation")
        if not isinstance(citation, dict) or not citation.get("url"):
            continue
        translated.append(
            {
                "type": "url_citation",
                "url": str(citation["url"]),
                "title": str(citation.get("title") or citation["url"]),
                "start_index": _nonnegative_int(citation.get("start_index")),
                "end_index": _nonnegative_int(citation.get("end_index")),
            }
        )
    return translated


def responses_annotations_to_chat(annotations: Any) -> list[dict[str, Any]]:
    """把 Responses URL citation 转成 Chat annotation。"""
    if not isinstance(annotations, list):
        return []
    translated: list[dict[str, Any]] = []
    for annotation in annotations:
        if not isinstance(annotation, dict):
            continue
        if str(annotation.get("type") or "").strip().lower() != "url_citation":
            continue
        if not annotation.get("url"):
            continue
        translated.append(
            {
                "type": "url_citation",
                "url_citation": {
                    "url": str(annotation["url"]),
                    "title": str(annotation.get("title") or annotation["url"]),
                    "start_index": _nonnegative_int(annotation.get("start_index")),
                    "end_index": _nonnegative_int(annotation.get("end_index")),
                },
            }
        )
    return translated


def claude_citations_to_responses(citations: Any, text: str) -> list[dict[str, Any]]:
    """把 Claude Web citation 转成 Responses URL citation。"""
    if not isinstance(citations, list):
        return []
    translated: list[dict[str, Any]] = []
    for citation in citations:
        if not isinstance(citation, dict) or not citation.get("url"):
            continue
        citation_type = str(citation.get("type") or "").strip().lower()
        if citation_type not in {"web_search_result_location", "search_result_location", "url_citation"}:
            continue
        cited_text = str(citation.get("cited_text") or "")
        start_index = text.rfind(cited_text) if cited_text else 0
        if start_index < 0:
            start_index = 0
        end_index = start_index + len(cited_text) if cited_text else len(text)
        translated.append(
            {
                "type": "url_citation",
                "url": str(citation["url"]),
                "title": str(citation.get("title") or citation["url"]),
                "start_index": start_index,
                "end_index": end_index,
            }
        )
    return translated


def responses_annotations_to_claude(annotations: Any, text: str) -> list[dict[str, Any]]:
    """把 Responses URL citation 转成 Claude Web citation。"""
    if not isinstance(annotations, list):
        return []
    translated: list[dict[str, Any]] = []
    for annotation in annotations:
        if not isinstance(annotation, dict):
            continue
        if str(annotation.get("type") or "").strip().lower() != "url_citation":
            continue
        if not annotation.get("url"):
            continue
        start_index = min(_nonnegative_int(annotation.get("start_index")), len(text))
        end_index = min(max(_nonnegative_int(annotation.get("end_index")), start_index), len(text))
        translated.append(
            {
                "type": "web_search_result_location",
                "url": str(annotation["url"]),
                "title": str(annotation.get("title") or annotation["url"]),
                "cited_text": text[start_index:end_index],
            }
        )
    return translated


def copy_logprobs(value: Any) -> list[dict[str, Any]]:
    """复制目标协议可直接复用的 token logprob 列表。"""
    if not isinstance(value, list):
        return []
    return [copy.deepcopy(item) for item in value if isinstance(item, dict)]


def _nonnegative_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0
