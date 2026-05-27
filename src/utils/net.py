#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络相关工具函数。"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

DEFAULT_REAL_CLIENT_IP_HEADER = "X-Forwarded-For"
DEFAULT_PROXY_MODE = "direct"
PROXY_MODE_DIRECT = "direct"
PROXY_MODE_SYSTEM = "system"
PROXY_MODE_CUSTOM = "custom"
SUPPORTED_PROXY_MODES = {
    PROXY_MODE_DIRECT,
    PROXY_MODE_SYSTEM,
    PROXY_MODE_CUSTOM,
}


def _normalize_ip_text(ip_value: str | None) -> str:
    """预处理 IP 文本，去除空白与 IPv6 映射前缀。"""
    if not ip_value:
        return ""

    normalized_value = ip_value.strip()
    if normalized_value.startswith("::ffff:"):
        normalized_value = normalized_value[7:]
    return normalized_value


def normalize_ip(ip_value: str | None) -> str:
    """规范化客户端 IP，并去除 IPv6 映射前缀。"""
    value = _normalize_ip_text(ip_value)
    if not value:
        return ""

    try:
        parsed = ipaddress.ip_address(value)
        return str(parsed)
    except ValueError:
        return value


def is_valid_ip(ip_value: str | None) -> bool:
    """校验 IPv4/IPv6 地址格式。"""
    value = _normalize_ip_text(ip_value)
    if not value:
        return False

    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def resolve_client_ip(
    headers: Mapping[str, Any] | None,
    remote_addr: str | None,
    *,
    real_ip_enabled: bool = False,
    real_ip_header: str | None = None,
) -> str:
    """根据配置解析客户端 IP，必要时从可信上游 header 读取真实源 IP。"""
    fallback_ip = normalize_ip(remote_addr)
    if not real_ip_enabled:
        return fallback_ip

    header_name = normalize_real_client_ip_header(real_ip_header)
    header_value = _get_header_value(headers, header_name)
    candidate_ip = _extract_forwarded_ip(header_value)
    if not is_valid_ip(candidate_ip):
        return fallback_ip
    return normalize_ip(candidate_ip)


def normalize_real_client_ip_header(value: Any) -> str:
    """规范化真实源 IP header 名称，空值回退到默认 header。"""
    header_name = str(value or "").strip()
    return header_name or DEFAULT_REAL_CLIENT_IP_HEADER


@dataclass(frozen=True)
class RequestsProxySettings:
    """requests 出站代理设置。"""

    mode: str
    proxy_url: str | None
    proxies: dict[str, str] | None
    trust_env: bool


def normalize_proxy_mode(
    value: Any,
    *,
    proxy_value: Any = None,
    default: str = DEFAULT_PROXY_MODE,
    error_message: str = "Proxy mode must be one of: direct, system, custom",
) -> str:
    """规范化代理模式，兼容旧配置中只有 proxy 的写法。"""
    text = str(value or "").strip().lower()
    if not text:
        return PROXY_MODE_CUSTOM if _has_proxy_value(proxy_value) else default

    aliases = {
        "none": PROXY_MODE_DIRECT,
        "off": PROXY_MODE_DIRECT,
        "false": PROXY_MODE_DIRECT,
        "0": PROXY_MODE_DIRECT,
        "env": PROXY_MODE_SYSTEM,
        "environment": PROXY_MODE_SYSTEM,
    }
    normalized = aliases.get(text, text)
    if normalized not in SUPPORTED_PROXY_MODES:
        raise ValueError(error_message)
    return normalized


def normalize_proxy_url(
    proxy_value: Any,
    *,
    required: bool = False,
    error_message: str = "Provider proxy must be a valid absolute URL",
) -> str | None:
    """规范化代理地址，要求为绝对 URL，并自动编码 userinfo。"""
    if proxy_value is None:
        if required:
            raise ValueError(error_message)
        return None

    value = str(proxy_value).strip()
    if not value:
        if required:
            raise ValueError(error_message)
        return None

    value = _encode_proxy_url_userinfo(value)
    parsed = urlparse(value)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(error_message) from exc

    if not parsed.scheme or not parsed.netloc or not parsed.hostname:
        raise ValueError(error_message)
    return value


def build_requests_proxies(
    proxy_value: Any,
    *,
    error_message: str = "Provider proxy must be a valid absolute URL",
) -> dict[str, str] | None:
    """将单个代理地址转换为 requests 可用的 proxies 映射。"""
    normalized = normalize_proxy_url(proxy_value, error_message=error_message)
    if normalized is None:
        return None
    return {
        "http": normalized,
        "https": normalized,
    }


def build_requests_proxy_settings(
    proxy_mode: Any,
    proxy_value: Any,
    *,
    proxy_mode_error_message: str = "Proxy mode must be one of: direct, system, custom",
    proxy_url_error_message: str = "Provider proxy must be a valid absolute URL",
) -> RequestsProxySettings:
    """根据代理模式构造 requests 代理设置。"""
    mode = normalize_proxy_mode(
        proxy_mode,
        proxy_value=proxy_value,
        error_message=proxy_mode_error_message,
    )
    if mode == PROXY_MODE_SYSTEM:
        return RequestsProxySettings(
            mode=mode,
            proxy_url=None,
            proxies=None,
            trust_env=True,
        )
    if mode == PROXY_MODE_DIRECT:
        return RequestsProxySettings(
            mode=mode,
            proxy_url=None,
            proxies=None,
            trust_env=False,
        )

    proxy_url = normalize_proxy_url(
        proxy_value,
        error_message=proxy_url_error_message,
    )
    if proxy_url is None:
        return RequestsProxySettings(
            mode=mode,
            proxy_url=None,
            proxies=None,
            trust_env=False,
        )
    return RequestsProxySettings(
        mode=mode,
        proxy_url=proxy_url,
        proxies=build_requests_proxies(
            proxy_url,
            error_message=proxy_url_error_message,
        ),
        trust_env=False,
    )


def apply_requests_proxy_settings(session: Any, settings: RequestsProxySettings) -> None:
    """把代理模式应用到 requests session。"""
    try:
        session.trust_env = settings.trust_env
    except Exception:
        pass


def build_requests_request_proxies(settings: RequestsProxySettings) -> dict[str, str | None] | None:
    """为单次 requests 调用构造代理参数，并显式屏蔽 direct 模式的环境代理。"""
    if settings.trust_env:
        return None
    if settings.proxies is None:
        return _disabled_environment_proxies()
    proxies: dict[str, str | None] = dict(settings.proxies)
    proxies["all"] = None
    return proxies


def build_module_request_proxies(settings: RequestsProxySettings) -> dict[str, str | None] | None:
    """为 requests.get/post 这种模块级调用构造代理参数。"""
    return build_requests_request_proxies(settings)


def _disabled_environment_proxies() -> dict[str, str | None]:
    """阻止 requests 自动合并 HTTP_PROXY / HTTPS_PROXY。"""
    return {
        "http": None,
        "https": None,
        "all": None,
    }


def _has_proxy_value(value: Any) -> bool:
    return bool(str(value or "").strip())


def _get_header_value(headers: Mapping[str, Any] | None, header_name: str) -> str | None:
    if headers is None:
        return None
    if "\r" in header_name or "\n" in header_name or ":" in header_name:
        return None

    direct_value = headers.get(header_name)
    if direct_value is not None:
        return str(direct_value)

    normalized_name = header_name.lower()
    for key, value in headers.items():
        if str(key).lower() == normalized_name:
            return str(value)
    return None


def _extract_forwarded_ip(header_value: str | None) -> str:
    value = str(header_value or "").strip()
    if not value:
        return ""
    return value.split(",", 1)[0].strip()


def _encode_proxy_url_userinfo(value: str) -> str:
    """编码代理 URL 中的用户名和密码部分。"""
    scheme, separator, rest = value.partition("://")
    if not separator or "@" not in rest:
        return value

    at_index = rest.rfind("@")
    if at_index <= 0 or at_index >= len(rest) - 1:
        return value

    userinfo = rest[:at_index]
    authority_tail = rest[at_index + 1 :]
    if ":" in userinfo:
        username, password = userinfo.split(":", 1)
        encoded_userinfo = f"{_quote_userinfo_part(username)}:{_quote_userinfo_part(password)}"
    else:
        encoded_userinfo = _quote_userinfo_part(userinfo)
    return f"{scheme}{separator}{encoded_userinfo}@{authority_tail}"


def _quote_userinfo_part(value: str) -> str:
    """编码 userinfo 字段，并保留用户已经写好的有效百分号转义。"""
    output: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "%" and index + 2 < len(value) and _is_hex_pair(value[index + 1 : index + 3]):
            output.append(value[index : index + 3])
            index += 3
            continue
        output.append(quote(char, safe=""))
        index += 1
    return "".join(output)


def _is_hex_pair(value: str) -> bool:
    return len(value) == 2 and all(char in "0123456789abcdefABCDEF" for char in value)
