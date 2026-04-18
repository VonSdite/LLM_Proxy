#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络相关工具函数。"""

import ipaddress
import ssl
from typing import Any, Dict, Optional
from urllib.parse import urlparse


def _normalize_ip_text(ip_value: Optional[str]) -> str:
    """预处理 IP 文本，去除空白与 IPv6 映射前缀。"""
    if not ip_value:
        return ""

    normalized_value = ip_value.strip()
    if normalized_value.startswith("::ffff:"):
        normalized_value = normalized_value[7:]
    return normalized_value


def normalize_ip(ip_value: Optional[str]) -> str:
    """规范化客户端 IP，并去除 IPv6 映射前缀。"""
    value = _normalize_ip_text(ip_value)
    if not value:
        return ""

    try:
        parsed = ipaddress.ip_address(value)
        return str(parsed)
    except ValueError:
        return value


def is_valid_ip(ip_value: Optional[str]) -> bool:
    """校验 IPv4/IPv6 地址格式。"""
    value = _normalize_ip_text(ip_value)
    if not value:
        return False

    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def normalize_proxy_url(proxy_value: Optional[str]) -> Optional[str]:
    """规范化代理地址，要求为绝对 URL。"""
    if proxy_value is None:
        return None

    value = str(proxy_value).strip()
    if not value:
        return None

    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Provider proxy must be a valid absolute URL")
    return value


def build_requests_proxies(proxy_value: Optional[str]) -> Optional[Dict[str, str]]:
    """将单个代理地址转换为 requests 可用的 proxies 映射。"""
    normalized = normalize_proxy_url(proxy_value)
    if normalized is None:
        return None
    return {
        "http": normalized,
        "https": normalized,
    }


def build_websocket_connect_options(
    proxy_value: Optional[str],
    verify_ssl: bool,
) -> Dict[str, Any]:
    """构造 websocket-client 连接参数。"""
    options: Dict[str, Any] = {
        "enable_multithread": True,
        "sslopt": {
            "cert_reqs": ssl.CERT_REQUIRED if verify_ssl else ssl.CERT_NONE,
        },
    }

    normalized = normalize_proxy_url(proxy_value)
    if normalized is None:
        return options

    parsed = urlparse(normalized)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("WebSocket upstream currently only supports http/https proxy")

    options["http_proxy_host"] = parsed.hostname
    options["http_proxy_port"] = parsed.port or (443 if scheme == "https" else 80)
    if parsed.username:
        options["http_proxy_auth"] = (parsed.username, parsed.password or "")

    return options
