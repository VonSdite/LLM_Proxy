#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置访问层。"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml


class ConfigManager:
    """提供配置项的类型化读取方法。"""

    def __init__(self, config_path: Path, root_path: Path):
        self._config = self._load_config(config_path)
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
        return int(self.get('server.port', 22026))

    def get_server_host(self) -> str:
        return str(self.get('server.host', '0.0.0.0'))

    def get_admin_config(self) -> Optional[Dict[str, str]]:
        admin = self.get('admin')
        return admin if isinstance(admin, dict) else None

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
        return self._config.copy()

    @staticmethod
    def _load_config(config_path: Union[str, Path]) -> Dict[str, Any]:
        path = Path(config_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f'Configuration file not found: {path}')

        with open(path, 'r', encoding='utf-8') as file:
            return yaml.safe_load(file) or {}
