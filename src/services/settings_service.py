#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用配置项维护服务。"""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any, Callable, Optional

from ..application.app_context import AppContext
from ..config.provider_config import parse_optional_bool


class SettingsService:
    """负责非 provider 专属的配置项更新。"""

    def __init__(
        self,
        ctx: AppContext,
        reload_logging_callback: Optional[Callable[[], None]] = None,
    ):
        self._config_manager = ctx.config_manager
        self._reload_logging_callback = reload_logging_callback

    def get_system_settings(self) -> dict[str, Any]:
        admin_config = self._config_manager.get_admin_config() or {}
        return {
            "server": {
                "host": self._config_manager.get_server_host(),
                "port": self._config_manager.get_server_port(),
            },
            "admin": {
                "username": str(admin_config.get("username") or ""),
                "password": str(admin_config.get("password") or ""),
            },
            "logging": {
                "path": str(self._config_manager.get_log_path()),
                "level": self._config_manager.get_log_level(),
                "llm_request_debug_enabled": self._config_manager.is_llm_request_debug_enabled(),
            },
            "auth_enabled": self._config_manager.is_auth_enabled(),
        }

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

    def update_basic_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Request payload must be an object")

        server_payload = payload.get("server")
        admin_payload = payload.get("admin")

        if not isinstance(server_payload, dict):
            raise ValueError("Config field 'server' must be an object")
        if not isinstance(admin_payload, dict):
            raise ValueError("Config field 'admin' must be an object")

        current_settings = self.get_system_settings()
        host = self._parse_server_host(server_payload.get("host"))
        port = self._parse_server_port(server_payload.get("port"))
        username = self._normalize_admin_value(admin_payload.get("username"))
        password = self._normalize_admin_secret(admin_payload.get("password"))
        server_restart_required = (
            str(current_settings["server"]["host"]) != host
            or int(current_settings["server"]["port"]) != port
        )

        config = self._config_manager.get_raw_config()
        server_config = self._ensure_mapping(config, "server")
        admin_config = self._ensure_mapping(config, "admin")

        server_config["host"] = host
        server_config["port"] = port
        admin_config["username"] = username
        admin_config["password"] = password

        self._config_manager.write_raw_config(config)

        updated_settings = self.get_system_settings()
        return {
            "settings": updated_settings,
            "auth_config_changed": (
                current_settings["admin"]["username"] != username
                or current_settings["admin"]["password"] != password
            ),
            "server_restart_required": server_restart_required,
        }

    def update_debug_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Request payload must be an object")

        logging_payload = payload.get("logging")
        if not isinstance(logging_payload, dict):
            raise ValueError("Config field 'logging' must be an object")

        current_settings = self.get_system_settings()
        log_path = self._parse_log_path(logging_payload.get("path"))
        log_level = self._parse_log_level(logging_payload.get("level"))
        logging_settings_changed = (
            str(current_settings["logging"]["path"]) != log_path
            or str(current_settings["logging"]["level"]).upper() != log_level
        )

        llm_request_debug_enabled = parse_optional_bool(
            logging_payload.get("llm_request_debug_enabled")
        )
        if llm_request_debug_enabled is None:
            raise ValueError("LLM request debug flag is required")

        config = self._config_manager.get_raw_config()
        logging_config = self._ensure_mapping(config, "logging")
        logging_config["path"] = log_path
        logging_config["level"] = log_level
        logging_config["llm_request_debug_enabled"] = llm_request_debug_enabled

        self._config_manager.write_raw_config(config)
        if logging_settings_changed and self._reload_logging_callback is not None:
            self._reload_logging_callback()

        return {
            "settings": self.get_system_settings(),
        }

    def update_system_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Request payload must be an object")

        basic_result = self.update_basic_settings(payload)
        debug_result = self.update_debug_settings(payload)
        basic_result["settings"] = debug_result["settings"]
        return basic_result

    @staticmethod
    def _ensure_mapping(config: dict[str, Any], key: str) -> dict[str, Any]:
        section = config.get(key)
        if section is None:
            section = {}
            config[key] = section
        if not isinstance(section, dict):
            raise ValueError(f"Config field '{key}' must be an object")
        return section

    @staticmethod
    def _parse_server_host(value: Any) -> str:
        host = str(value or "").strip()
        if not host:
            raise ValueError("Server host is required")
        try:
            ip_address(host)
        except ValueError as exc:
            raise ValueError("Server host must be a valid IP address") from exc
        return host

    @staticmethod
    def _parse_server_port(value: Any) -> int:
        try:
            port = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Server port must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Server port must be between 1 and 65535")
        return port

    @staticmethod
    def _parse_log_path(value: Any) -> str:
        path = str(value or "").strip()
        if not path:
            raise ValueError("Log path is required")
        return path

    @staticmethod
    def _parse_log_level(value: Any) -> str:
        log_level = str(value or "").strip().upper()
        allowed_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if log_level not in allowed_levels:
            raise ValueError(
                "Log level must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL"
            )
        return log_level

    @staticmethod
    def _normalize_admin_value(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_admin_secret(value: Any) -> str:
        if value is None:
            return ""
        secret = str(value)
        if not secret.strip():
            return ""
        return secret
