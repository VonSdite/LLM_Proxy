#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据平面 CORS 支持。"""

from __future__ import annotations

from flask import Flask, Response, make_response, request
from flask.typing import ResponseReturnValue

DATA_PLANE_PATH_PREFIX = "/v1/"
DEFAULT_ALLOW_METHODS = "GET, POST, OPTIONS"
DEFAULT_ALLOW_HEADERS = ", ".join(
    [
        "Accept",
        "Authorization",
        "Cache-Control",
        "Content-Type",
        "OpenAI-Beta",
        "OpenAI-Organization",
        "OpenAI-Project",
        "Anthropic-Beta",
        "Anthropic-Version",
        "X-API-Key",
        "X-Requested-With",
    ]
)
MAX_AGE_SECONDS = "86400"


def register_data_plane_cors(app: Flask) -> None:
    """为 OpenAI 兼容数据平面注册跨域响应头和预检响应。"""

    @app.before_request
    def handle_data_plane_preflight() -> ResponseReturnValue | None:
        if request.method != "OPTIONS" or not _is_data_plane_path(request.path):
            return None
        return make_response("", 204)

    @app.after_request
    def add_data_plane_cors_headers(response: Response) -> Response:
        if not _is_data_plane_path(request.path):
            return response

        _apply_data_plane_cors_headers(response)
        return response


def _is_data_plane_path(path: str) -> bool:
    normalized_path = str(path or "")
    return normalized_path == "/v1" or normalized_path.startswith(DATA_PLANE_PATH_PREFIX)


def _apply_data_plane_cors_headers(response: Response) -> None:
    origin = request.headers.get("Origin")
    response.headers["Access-Control-Allow-Origin"] = origin or "*"
    response.headers["Access-Control-Allow-Methods"] = DEFAULT_ALLOW_METHODS
    response.headers["Access-Control-Allow-Headers"] = _get_requested_headers()
    response.headers["Access-Control-Max-Age"] = MAX_AGE_SECONDS

    if request.headers.get("Access-Control-Request-Private-Network") == "true":
        response.headers["Access-Control-Allow-Private-Network"] = "true"

    _append_vary_header(response, "Origin")
    _append_vary_header(response, "Access-Control-Request-Method")
    _append_vary_header(response, "Access-Control-Request-Headers")


def _get_requested_headers() -> str:
    requested_headers = request.headers.get("Access-Control-Request-Headers")
    if requested_headers:
        return requested_headers
    return DEFAULT_ALLOW_HEADERS


def _append_vary_header(response: Response, header_name: str) -> None:
    current_value = response.headers.get("Vary")
    if not current_value:
        response.headers["Vary"] = header_name
        return

    existing_names = {
        item.strip().lower()
        for item in current_value.split(",")
        if item.strip()
    }
    if header_name.lower() in existing_names:
        return

    response.headers["Vary"] = f"{current_value}, {header_name}"
