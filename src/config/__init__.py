#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置层导出。"""

from .config_manager import ConfigManager
from .provider_config import (
    ProviderConfigSchema,
    ProviderRuntimeView,
    RuntimeProviderSpec,
    build_provider_schemas,
)

__all__ = [
    "ConfigManager",
    "ProviderConfigSchema",
    "RuntimeProviderSpec",
    "ProviderRuntimeView",
    "build_provider_schemas",
    "ProviderManager",
]


def __getattr__(name: str):
    if name == "ProviderManager":
        from .provider_manager import ProviderManager

        return ProviderManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
