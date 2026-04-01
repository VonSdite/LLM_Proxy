#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置访问层。"""

import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml


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

        self._write_config(config)
        self._config = deepcopy(config)

    def reload(self) -> None:
        self._config = self._load_config(self._config_path)

    @staticmethod
    def _load_config(config_path: Union[str, Path]) -> Dict[str, Any]:
        path = Path(config_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f'Configuration file not found: {path}')

        with open(path, 'r', encoding='utf-8') as file:
            data = yaml.safe_load(file)

        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError('Configuration file must contain a top-level mapping')
        return data

    def _write_config(self, config: Dict[str, Any]) -> None:
        temp_file_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                'w',
                encoding='utf-8',
                dir=self._config_path.parent,
                delete=False,
            ) as temp_file:
                yaml.safe_dump(config, temp_file, allow_unicode=True, sort_keys=False)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_file_path = temp_file.name

            os.replace(temp_file_path, self._config_path)
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
