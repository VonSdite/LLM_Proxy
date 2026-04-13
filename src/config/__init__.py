#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置层导出。"""

from typing import TYPE_CHECKING

from .config_manager import ConfigManager
from .provider_config import (
    AuthEntrySchema,
    AuthGroupSchema,
    ProviderConfigSchema,
    ProviderRuntimeView,
    RuntimeProviderSpec,
    build_auth_group_schemas,
    build_provider_schemas,
    validate_auth_group_definitions,
    validate_auth_group_provider_definitions,
)
from .provider_runtime_factory import ProviderRuntimeFactory

if TYPE_CHECKING:
    from .auth_group_manager import AuthGroupManager
    from .provider_manager import ProviderManager

__all__ = [
    "ConfigManager",
    "AuthEntrySchema",
    "AuthGroupSchema",
    "ProviderConfigSchema",
    "RuntimeProviderSpec",
    "ProviderRuntimeView",
    "ProviderRuntimeFactory",
    "build_auth_group_schemas",
    "build_provider_schemas",
    "validate_auth_group_definitions",
    "validate_auth_group_provider_definitions",
    "AuthGroupManager",
    "ProviderManager",
]


def __getattr__(name: str):
    if name == "AuthGroupManager":
        from .auth_group_manager import AuthGroupManager

        return AuthGroupManager
    if name == "ProviderManager":
        from .provider_manager import ProviderManager

        return ProviderManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
