#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理风险确认页识别与自动确认。"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import requests

PROXY_WARNING_CONFIRM_TIMEOUT_SECONDS = 20
PROXY_WARNING_ERROR_CODE = "proxy_warning_required"
PROXY_WARNING_STATUS_CODE = 511
PROXY_WARNING_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
PROXY_WARNING_RETRY_DELAYS_SECONDS = (0.35,)


class ProxyWarningRequired(RuntimeError):
    """代理风险确认失败，或确认后仍被拦截。"""

    def __init__(
        self,
        confirmation_url: str,
        upstream_status: int,
        *,
        auto_confirm_error: str | None = None,
    ) -> None:
        self.confirmation_url = confirmation_url
        self.upstream_status = upstream_status
        self.auto_confirm_error = auto_confirm_error
        message = (
            "Network proxy confirmation required before accessing upstream. "
            "Open the confirmation URL in a browser on the same network path, "
            f"click continue, then retry: {confirmation_url}"
        )
        if auto_confirm_error:
            message = f"{message} Auto-confirm failed: {auto_confirm_error}"
        super().__init__(message)

    def to_details(self) -> dict[str, Any]:
        """返回可下发给客户端的结构化错误详情。"""
        details: dict[str, Any] = {
            "confirmation_url": self.confirmation_url,
            "upstream_status": self.upstream_status,
        }
        if self.auto_confirm_error:
            details["auto_confirm_error"] = self.auto_confirm_error
        return details


class _ProxyWarningInputParser(HTMLParser):
    """提取风险确认页隐藏字段。"""

    def __init__(self) -> None:
        super().__init__()
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        values = {str(key).lower(): value for key, value in attrs}
        input_key = str(values.get("id") or values.get("name") or "").strip().lower()
        if input_key:
            self.inputs[input_key] = str(values.get("value") or "")


def request_with_proxy_warning_retry(
    send_request: Callable[[], Any],
    *,
    request_options: dict[str, Any] | None = None,
    confirm_session: requests.Session | None = None,
    session_factory: Callable[[], requests.Session] | None = None,
    logger: Any = None,
    log_context: str = "",
    retry_delays_seconds: tuple[float, ...] = PROXY_WARNING_RETRY_DELAYS_SECONDS,
) -> Any:
    """执行请求；遇到代理风险页时自动确认并按短退避重试。"""
    response = send_request()
    confirmation_url = extract_proxy_warning_confirmation_url(response)
    if not confirmation_url:
        return response

    upstream_status = _get_status_code(response)
    close_response(response)
    auto_confirm_error = confirm_proxy_warning(
        confirmation_url,
        request_options=request_options,
        session=confirm_session,
        session_factory=session_factory,
    )
    if auto_confirm_error:
        raise ProxyWarningRequired(
            confirmation_url,
            upstream_status,
            auto_confirm_error=auto_confirm_error,
        )

    if logger is not None:
        logger.info(
            "Network proxy warning auto-confirmed: %s confirmation_url=%s",
            log_context,
            confirmation_url,
        )
    retry_response = None
    retry_confirmation_url = ""
    retry_status = upstream_status
    for delay_seconds in retry_delays_seconds:
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        retry_response = send_request()
        retry_confirmation_url = extract_proxy_warning_confirmation_url(retry_response) or ""
        if not retry_confirmation_url:
            return retry_response
        retry_status = _get_status_code(retry_response)
        close_response(retry_response)
        if logger is not None:
            logger.warning(
                "Network proxy warning remained after auto-confirm: %s "
                "status=%s confirmation_url=%s",
                log_context,
                retry_status,
                retry_confirmation_url,
            )

    raise ProxyWarningRequired(
        retry_confirmation_url or confirmation_url,
        retry_status,
        auto_confirm_error="retry still blocked after auto-confirm",
    )


def confirm_proxy_warning(
    confirmation_url: str,
    *,
    request_options: dict[str, Any] | None = None,
    session: requests.Session | None = None,
    session_factory: Callable[[], requests.Session] | None = None,
    timeout_seconds: int = PROXY_WARNING_CONFIRM_TIMEOUT_SECONDS,
) -> str | None:
    """确认代理风险页，成功返回 None，失败返回错误摘要。"""
    owns_session = session is None
    active_session = session or (session_factory or requests.Session)()
    warning_response: Any = None
    check_response: Any = None
    try:
        options = dict(request_options or {})
        warning_options = build_proxy_warning_request_options(
            options,
            build_proxy_warning_browser_headers(),
        )
        warning_response = active_session.get(
            confirmation_url,
            timeout=timeout_seconds,
            allow_redirects=False,
            **warning_options,
        )
        status_code = _get_status_code(warning_response)
        if 300 <= status_code < 400 and not extract_proxy_warning_confirmation_url(warning_response):
            return None
        if status_code >= 400:
            return f"warning page returned {status_code}"
        hidden_inputs = parse_proxy_warning_inputs(
            getattr(warning_response, "text", "") or "",
            confirmation_url=confirmation_url,
        )
        check_url = build_proxy_warning_check_url(confirmation_url, hidden_inputs)
        check_options = build_proxy_warning_request_options(
            options,
            build_proxy_warning_browser_headers(referer=confirmation_url),
        )
        check_response = active_session.get(
            check_url,
            timeout=timeout_seconds,
            allow_redirects=False,
            **check_options,
        )
        status_code = _get_status_code(check_response)
        if 300 <= status_code < 400 and not extract_proxy_warning_confirmation_url(check_response):
            return None
        if status_code >= 400:
            return f"confirm check returned {status_code}"
        return None
    except requests.exceptions.RequestException as exc:
        return str(exc)
    except ValueError as exc:
        return str(exc)
    finally:
        close_response(warning_response)
        close_response(check_response)
        if owns_session:
            close_response(active_session)


def extract_proxy_warning_confirmation_url(response: Any) -> str | None:
    """从 HTTP 响应中提取代理风险确认页地址。"""
    status_code = _get_status_code(response)
    if status_code < 300 or status_code >= 400:
        return None
    headers = getattr(response, "headers", {}) or {}
    location = str(headers.get("Location") or headers.get("location") or "").strip()
    if not location:
        return None
    if "proxycontrolwarn" not in location.lower():
        return None
    return location


def parse_proxy_warning_inputs(html: str, *, confirmation_url: str = "") -> dict[str, str]:
    """解析风险确认页隐藏字段。"""
    parser = _ProxyWarningInputParser()
    parser.feed(str(html or ""))
    uid = get_raw_query_value(confirmation_url, "uid") if confirmation_url else ""
    if uid:
        parser.inputs.setdefault("uid", uid)
    required = ("sessionid", "pid", "uid")
    missing = [key for key in required if not parser.inputs.get(key)]
    if missing:
        raise ValueError(f"warning page missing hidden field: {', '.join(missing)}")
    return {key: parser.inputs[key] for key in required}


def build_proxy_warning_browser_headers(*, referer: str = "") -> dict[str, str]:
    """构造接近浏览器点击确认页的请求头。"""
    headers = {
        "User-Agent": PROXY_WARNING_BROWSER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def build_proxy_warning_request_options(
    request_options: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """合并确认请求选项，避免 headers 参数重复。"""
    options = dict(request_options)
    existing_headers = options.pop("headers", {}) or {}
    merged_headers = dict(headers)
    if isinstance(existing_headers, dict):
        merged_headers.update({str(key): str(value) for key, value in existing_headers.items()})
    options["headers"] = merged_headers
    return options


def build_proxy_warning_check_url(
    confirmation_url: str,
    hidden_inputs: dict[str, str],
) -> str:
    """构造风险页确认接口地址。"""
    parsed = urlparse(confirmation_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("warning confirmation URL must be absolute")
    raw_ori_url = get_raw_query_value(confirmation_url, "ori_url")
    if not raw_ori_url:
        raise ValueError("warning confirmation URL missing ori_url")
    raw_params = (
        f"ori_url={raw_ori_url}"
        f"&sessionid={hidden_inputs['sessionid']}"
        f"&pid={hidden_inputs['pid']}"
        f"&uid={hidden_inputs['uid']}"
    )
    signed_value = proxy_warning_md6(base64_encode(raw_params))
    return f"{parsed.scheme}://{parsed.netloc}/proxycontrolwarn/check?{base64_encode(signed_value)}"


def get_raw_query_value(url: str, name: str) -> str:
    """读取未 URL decode 的 query 参数值。"""
    query = urlparse(url).query
    search = f"{name}="
    pos = query.find(search)
    if pos < 0:
        return ""
    start = pos + len(search)
    end = query.find("&", start)
    if end < 0:
        return query[start:]
    return query[start:end]


def base64_encode(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def proxy_warning_md6(value: str) -> str:
    result = []
    for index, char in enumerate(value):
        code = 53 ^ reverse_byte_bits(ord(char)) ^ (255 & index)
        result.append(proxy_warning_escape_char(code))
    return "".join(result)


def reverse_byte_bits(value: int) -> int:
    return (
        ((1 & value) << 7)
        | ((2 & value) << 5)
        | ((4 & value) << 3)
        | ((8 & value) << 1)
        | ((16 & value) >> 1)
        | ((32 & value) >> 3)
        | ((64 & value) >> 5)
        | ((128 & value) >> 7)
    )


def proxy_warning_escape_char(value: int) -> str:
    if value == ord(" "):
        return "+"
    if (
        (value < ord("0") and value not in {ord("-"), ord(".")})
        or (ord("9") < value < ord("A"))
        or (ord("Z") < value < ord("a") and value != ord("_"))
        or value > ord("z")
    ):
        return f"%{value >> 4:X}{value & 15:X}"
    return chr(value)


def close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


def _get_status_code(response: Any) -> int:
    try:
        return int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return 0
