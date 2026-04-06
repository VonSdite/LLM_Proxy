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


LOGGER = logging.getLogger("app")


class ConfigManager:
    """提供配置项的类型化读取方法。"""

    def __init__(self, config_path: Path, root_path: Path):
        self._config_path = Path(config_path).resolve()
        self._config = self._load_config(self._config_path)
        self._root_path = Path(root_path).resolve()

    def get(self, key: str, default: Any = None) -> Any:
        value: Any = self._config
        for part in key.split('.'):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    def get_server_port(self) -> int:
        return int(self.get('server.port', 8080))

    def get_server_host(self) -> str:
        return str(self.get('server.host', '127.0.0.1'))

    def get_admin_config(self) -> Optional[Dict[str, str]]:
        admin = self.get('admin')
        return dict(admin) if isinstance(admin, dict) else None

    def is_auth_enabled(self) -> bool:
        admin = self.get_admin_config()
        return bool(admin and admin.get('username') and admin.get('password'))

    def is_chat_whitelist_enabled(self) -> bool:
        value = self.get('chat.whitelist_enabled', False)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'on'}
        if isinstance(value, int):
            return value != 0
        return False

    def get_database_path(self) -> str:
        return self.get('database.path', self._root_path / 'data/requests.db')

    def get_log_path(self) -> str:
        return self.get('logging.path', self._root_path / 'logs')

    def get_log_level(self) -> str:
        return self.get('logging.level', 'INFO').upper()

    def get_raw_config(self) -> Dict[str, Any]:
        return deepcopy(self._config)

    def write_raw_config(self, config: Dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise ValueError('Configuration file must contain a top-level mapping')

        normalized, _ = self._normalize_config(config)
        self._write_config(normalized)
        self._config = deepcopy(normalized)

    def reload(self) -> None:
        self._config = self._load_config(self._config_path)

    @classmethod
    def _load_config(cls, config_path: Union[str, Path]) -> Dict[str, Any]:
        path = Path(config_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f'Configuration file not found: {path}')

        with open(path, 'r', encoding='utf-8') as file:
            data = yaml.safe_load(file)

        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError('Configuration file must contain a top-level mapping')
        normalized, changed = cls._normalize_config(data)
        if changed:
            cls._write_config_file(path, normalized)
            LOGGER.info(
                "Migrated legacy provider config field 'target_format' to 'target_formats': %s",
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
                'w',
                encoding='utf-8',
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
        providers = normalized.get('providers')
        if not isinstance(providers, list):
            return normalized, False

        changed = False
        normalized_providers = []
        for provider in providers:
            if not isinstance(provider, dict):
                normalized_providers.append(provider)
                continue

            normalized_provider = dict(provider)
            # DEPRECATED compatibility path for legacy config files that still
            # use `target_format`. Public config/API support is now limited to
            # `target_formats`, and this migration block should be removed later.
            legacy_target_format = normalized_provider.pop('target_format', None)
            if legacy_target_format is not None:
                changed = True
                if not cls._has_target_formats_value(normalized_provider.get('target_formats')):
                    normalized_provider['target_formats'] = [legacy_target_format]
            normalized_providers.append(normalized_provider)

        if changed:
            normalized['providers'] = normalized_providers
        return normalized, changed

    @staticmethod
    def _has_target_formats_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple)):
            return any(str(item or '').strip() for item in value)
        return True
