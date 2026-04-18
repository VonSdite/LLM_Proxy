#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置访问层。"""

import logging
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import yaml

from .provider_config import normalize_provider_target_formats

LOGGER = logging.getLogger("app")


class ConfigManager:
    """提供配置项的类型化读取方法。"""

    def __init__(self, config_path: Path, root_path: Path):
        self._config_path = Path(config_path).resolve()
        self._config = self._load_config(self._config_path)
        self._root_path = Path(root_path).resolve()

    def get(self, key: str, default: Any = None) -> Any:
        value: Any = self._config
        for part in key.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    def get_server_port(self) -> int:
        return int(self.get("server.port", 8080))

    def get_server_host(self) -> str:
        return str(self.get("server.host", "127.0.0.1"))

    def get_admin_config(self) -> Optional[Dict[str, str]]:
        admin = self.get("admin")
        return dict(admin) if isinstance(admin, dict) else None

    def is_auth_enabled(self) -> bool:
        admin = self.get_admin_config()
        return bool(admin and admin.get("username") and admin.get("password"))

    def is_chat_whitelist_enabled(self) -> bool:
        return self._read_bool("chat.whitelist_enabled", default=False)

    def is_llm_request_debug_enabled(self) -> bool:
        return self._read_bool("logging.llm_request_debug_enabled", default=False)

    def get_database_path(self) -> str:
        return self.get("database.path", self._root_path / "data/requests.db")

    def get_log_path(self) -> str:
        return self.get("logging.path", self._root_path / "logs")

    def get_log_level(self) -> str:
        return self.get("logging.level", "INFO").upper()

    def get_raw_config(self) -> Dict[str, Any]:
        return deepcopy(self._config)

    def write_raw_config(self, config: Dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise ValueError("Configuration file must contain a top-level mapping")

        normalized, _ = self._normalize_config(config)
        self._write_config(normalized)
        self._config = deepcopy(normalized)

    def reload(self) -> None:
        self._config = self._load_config(self._config_path)

    @classmethod
    def _load_config(cls, config_path: Union[str, Path]) -> Dict[str, Any]:
        path = Path(config_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)

        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError("Configuration file must contain a top-level mapping")
        normalized, changed = cls._normalize_config(data)
        if changed:
            cls._write_config_file(path, normalized)
            LOGGER.info(
                "Normalized legacy provider config fields: %s",
                path,
            )
        return normalized

    def _write_config(self, config: Dict[str, Any]) -> None:
        self._write_config_file(self._config_path, config)

    @staticmethod
    def _write_config_file(config_path: Path, config: Dict[str, Any]) -> None:
        temp_file_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=config_path.parent,
                delete=False,
            ) as temp_file:
                yaml.safe_dump(config, temp_file, allow_unicode=True, sort_keys=False)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_file_path = temp_file.name

            os.replace(temp_file_path, config_path)
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    @classmethod
    def _normalize_config(cls, config: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        normalized = deepcopy(config)
        providers = normalized.get("providers")
        if not isinstance(providers, list):
            return normalized, False

        changed = False
        normalized_providers = []
        for provider in providers:
            if not isinstance(provider, dict):
                normalized_providers.append(provider)
                continue

            normalized_provider = dict(provider)
            # TODO: 兼容历史配置里仍在使用 `target_format` 的旧版本。
            # 当前公开配置与 API 只保留 `target_formats`，待完成几个版本的迁移窗口后删除这段兼容逻辑。
            legacy_target_format = normalized_provider.pop("target_format", None)
            if legacy_target_format is not None:
                changed = True
                if not cls._has_target_formats_value(
                    normalized_provider.get("target_formats")
                ):
                    normalized_provider["target_formats"] = [legacy_target_format]

            # TODO: 兼容历史本地配置里仍在使用已移除的 `codex` 协议值。
            # 先在启动加载阶段自动修正并落盘，让旧版本配置可以平滑启动；待完成几个版本的迁移窗口后删除这段兼容逻辑。
            source_format = normalized_provider.get("source_format")
            if isinstance(source_format, str) and source_format.strip().lower() == "codex":
                normalized_provider["source_format"] = "openai_responses"
                changed = True

            if "target_formats" in normalized_provider:
                raw_target_formats = normalized_provider.get("target_formats")
                if isinstance(raw_target_formats, str):
                    candidate_target_formats = raw_target_formats.replace(",", "\n").splitlines()
                elif isinstance(raw_target_formats, (list, tuple)):
                    candidate_target_formats = list(raw_target_formats)
                else:
                    candidate_target_formats = raw_target_formats

                if isinstance(candidate_target_formats, list):
                    candidate_target_formats = [
                        "openai_responses"
                        if isinstance(item, str) and item.strip().lower() == "codex"
                        else item
                        for item in candidate_target_formats
                    ]
                elif isinstance(candidate_target_formats, str) and candidate_target_formats.strip().lower() == "codex":
                    candidate_target_formats = ["openai_responses"]

                normalized_target_formats = list(
                    normalize_provider_target_formats(
                        candidate_target_formats,
                        field_name="target_formats",
                    )
                )
                if normalized_target_formats != list(normalized_provider.get("target_formats") or []):
                    normalized_provider["target_formats"] = normalized_target_formats
                    changed = True
            normalized_providers.append(normalized_provider)

        if changed:
            normalized["providers"] = normalized_providers
        return normalized, changed

    @staticmethod
    def _has_target_formats_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple)):
            return any(str(item or "").strip() for item in value)
        return True

    def _read_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(value, int):
            return value != 0
        return bool(default)
