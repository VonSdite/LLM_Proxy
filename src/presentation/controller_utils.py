#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制器层通用辅助函数。"""

from __future__ import annotations

from typing import Any

from flask import jsonify, request
from flask.typing import ResponseReturnValue


def get_json_object() -> dict[str, Any]:
    """读取 JSON 对象；非对象或空请求体统一返回空字典。"""
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return dict(payload)
    return {}


def require_json_object() -> dict[str, Any]:
    """读取 JSON 对象；如果请求体不是对象则抛出异常。"""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return dict(payload)


def build_value_error_response(exc: ValueError) -> ResponseReturnValue:
    """统一把业务校验错误映射为 JSON 响应。"""
    message = str(exc)
    status_code = 404 if "not found" in message.lower() else 400
    return jsonify({"error": message}), status_code


def coerce_string_list(value: Any, *, error_message: str) -> list[str]:
    """将请求中的列表字段转成字符串列表。"""
    if not isinstance(value, list):
        raise ValueError(error_message)
    return [str(item) for item in value]
