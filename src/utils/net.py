#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络相关工具函数。"""

import ipaddress
from typing import Dict, Optional
from urllib.parse import urlparse


def normalize_ip(ip_value: Optional[str]) -> str:
    """规范化客户端 IP，并去除 IPv6 映射前缀。"""
    if not ip_value:
        return ''

    value = ip_value.strip()
    if value.startswith('::ffff:'):
        value = value[7:]

    try:
        parsed = ipaddress.ip_address(value)
        return str(parsed)
    except ValueError:
        return value


def is_valid_ip(ip_value: Optional[str]) -> bool:
    """校验 IPv4/IPv6 地址格式。"""
    if not ip_value:
        return False

    try:
        ipaddress.ip_address(ip_value.strip().removeprefix('::ffff:'))
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
