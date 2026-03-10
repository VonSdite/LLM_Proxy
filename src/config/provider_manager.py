#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 管理器：负责加载配置与可选钩子模块。"""

import hashlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..application.app_context import AppContext, Logger
from ..external import LLMProvider
from ..hooks import HookModule


class ProviderManager:
    """管理模型与 provider 的映射关系。"""

    def __init__(self, ctx: AppContext):
        self._ctx = ctx
        self._base_dir = ctx.root_path.resolve()
        self._logger: Logger = ctx.logger
        self._provider_by_model: Dict[str, LLMProvider] = {}
        self._provider_names: set[str] = set()
        self._hook_cache: Dict[str, Optional[HookModule]] = {}

    def load_providers(self, providers_config: List[Dict[str, Any]]) -> None:
        """重载 provider 配置。"""
        self._provider_by_model.clear()
        self._provider_names.clear()
        self._hook_cache.clear()

        for provider_cfg in providers_config:
            self._load_provider(provider_cfg)

    def _load_provider(self, config: Dict[str, Any]) -> None:
        """加载单个 provider，并注册其模型映射。"""
        name = config.get('name')
        api = config.get('api', '')
        api_key = config.get('api_key', '')
        model_list = config.get('model_list', [])
        timeout_seconds = self._to_positive_int(config.get('timeout_seconds', 300), default=300)
        max_retries = self._to_positive_int(config.get('max_retries', 3), default=3)
        verify_ssl = self._to_bool(config.get('verify_ssl', False), default=False)

        if name in self._provider_names:
            raise ValueError(f"Duplicate provider name detected: {name}")

        if not api:
            self._logger.warning(f"Provider '{name}' skipped: api is empty")
            return

        if not model_list:
            self._logger.warning(f"Provider '{name}' skipped: model_list is empty")
            return

        hook = self._load_hook(config.get('hook'))

        provider = LLMProvider(
            name=name,
            api=api,
            api_key=api_key,
            model_list=model_list,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            verify_ssl=verify_ssl,
            hook=hook,
        )

        for model in model_list:
            key = f'{name}/{model}'
            if key in self._provider_by_model:
                raise ValueError(f'Duplicate provider model mapping detected: {key}')
            self._provider_by_model[key] = provider

        self._provider_names.add(name)
        self._logger.info(f'Loaded provider: {name}, models: {model_list}')

    def _load_hook(self, hook_path: Optional[str]) -> Optional[HookModule]:
        """按路径加载钩子模块，并做缓存复用。"""
        if not hook_path:
            return None

        if Path(hook_path).is_absolute():
            hook_file = Path(hook_path).resolve()
        else:
            hooks_dir = self._base_dir / 'hooks'
            hook_file = (hooks_dir / hook_path).resolve()

        cache_key = str(hook_file)
        if cache_key in self._hook_cache:
            return self._hook_cache[cache_key]

        if not hook_file.exists():
            self._logger.warning(f'Hook file not found: {hook_file}')
            self._hook_cache[cache_key] = None
            return None

        try:
            hook_dir = str(hook_file.parent)
            path_inserted = False
            if hook_dir not in sys.path:
                sys.path.insert(0, hook_dir)
                path_inserted = True

            hook_name = hook_file.stem
            normalized_path = hook_file.resolve().as_posix().lower()
            path_hash = hashlib.sha1(normalized_path.encode('utf-8')).hexdigest()[:12]
            module_name = f'hook_{hook_name}_{path_hash}'
            spec = importlib.util.spec_from_file_location(module_name, hook_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hook_class = getattr(module, 'Hook', None)
                if hook_class is None or not inspect.isclass(hook_class):
                    self._logger.error(f'Hook file must export a class named Hook: {hook_file}')
                    self._hook_cache[cache_key] = None
                    return None
                hook_instance = hook_class()
                self._hook_cache[cache_key] = hook_instance
                self._logger.info(f'Hook loaded successfully: {hook_file}')
                return hook_instance
        except Exception as exc:
            self._logger.error(f'Failed to load hook {hook_file}: {exc}')
        finally:
            if path_inserted:
                try:
                    sys.path.remove(hook_dir)
                except ValueError:
                    pass

        self._hook_cache[cache_key] = None
        return None

    def find_provider_by_model(self, model_name: str) -> Optional[LLMProvider]:
        return self._provider_by_model.get(model_name)

    def get_all_models(self) -> List[str]:
        return sorted(self._provider_by_model.keys())

    @staticmethod
    def _to_positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {'true', '1', 'yes', 'on'}:
                return True
            if lowered in {'false', '0', 'no', 'off'}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return default
