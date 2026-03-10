#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络相关工具函数。"""

import ipaddress
from typing import Optional


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
