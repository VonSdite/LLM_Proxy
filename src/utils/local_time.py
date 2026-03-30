#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地时间工具。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional


LOCAL_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
LEGACY_LOCAL_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
LOCAL_DATE_FORMAT = "%Y-%m-%d"


def now_local_datetime() -> datetime:
    """返回服务器当前本地时间。"""
    return datetime.now()


def format_local_datetime(value: datetime) -> str:
    """格式化为统一的本地时间文本。"""
    return value.strftime(LOCAL_DATETIME_FORMAT)


def now_local_datetime_text() -> str:
    """返回当前本地时间文本。"""
    return format_local_datetime(now_local_datetime())


def parse_local_datetime(value: object) -> Optional[datetime]:
    """解析数据库/API 使用的本地时间文本。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    if not text:
        return None

    for pattern in (LOCAL_DATETIME_FORMAT, LEGACY_LOCAL_DATETIME_FORMAT):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def ensure_local_datetime(value: object | None) -> datetime:
    """确保得到一个可用的本地时间对象。"""
    parsed = parse_local_datetime(value)
    if parsed is None:
        if value is None:
            return now_local_datetime()
        raise ValueError(f"Unsupported local datetime value: {value!r}")
    return parsed


def normalize_local_datetime_text(value: object | None) -> Optional[str]:
    """把时间值整理成统一格式。"""
    if value is None:
        return None

    parsed = parse_local_datetime(value)
    if parsed is None:
        return str(value)
    return format_local_datetime(parsed)


def format_local_date(value: datetime) -> str:
    """格式化为本地日期文本。"""
    return value.strftime(LOCAL_DATE_FORMAT)
