#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用配置项维护服务。"""

from typing import Any

from ..application.app_context import AppContext
from ..config.provider_config import parse_optional_bool


class SettingsService:
    """负责非 provider 专属的配置项更新。"""

    def __init__(self, ctx: AppContext):
        self._config_manager = ctx.config_manager

    def update_chat_whitelist_enabled(self, enabled: Any) -> bool:
        parsed_enabled = parse_optional_bool(enabled)
        if parsed_enabled is None:
            raise ValueError("Whitelist enabled flag is required")

        config = self._config_manager.get_raw_config()
        chat_config = config.get("chat")
        if chat_config is None:
            chat_config = {}
            config["chat"] = chat_config
        if not isinstance(chat_config, dict):
            raise ValueError("Config field 'chat' must be an object")

        chat_config["whitelist_enabled"] = parsed_enabled
        self._config_manager.write_raw_config(config)
        return parsed_enabled
