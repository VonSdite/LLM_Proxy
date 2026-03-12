#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 管理器：负责加载配置、构建运行时对象并维护模型映射。"""

import hashlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..application.app_context import AppContext, Logger
from ..external import LLMProvider
from ..hooks import HookModule
from .provider_config import normalize_runtime_provider_config


class ProviderManager:
    """管理模型到 provider 的映射关系。"""

    def __init__(self, ctx: AppContext):
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
        normalized = normalize_runtime_provider_config(config)
        name = normalized['name']
        model_list = normalized['model_list']

        if name in self._provider_names:
            raise ValueError(f'Duplicate provider name detected: {name}')

        if not model_list:
            self._logger.warning("Provider '%s' skipped: model_list is empty", name)
            return

        provider = LLMProvider(
            name=name,
            api=normalized['api'],
            api_key=normalized['api_key'],
            model_list=model_list,
            proxy=normalized['proxy'],
            timeout_seconds=normalized['timeout_seconds'],
            max_retries=normalized['max_retries'],
            verify_ssl=normalized['verify_ssl'],
            hook=self._load_hook(normalized['hook']),
        )

        for model in model_list:
            key = f'{name}/{model}'
            if key in self._provider_by_model:
                raise ValueError(f'Duplicate provider model mapping detected: {key}')
            self._provider_by_model[key] = provider

        self._provider_names.add(name)
        self._logger.info('Loaded provider: %s, models: %s', name, model_list)

    def _load_hook(self, hook_path: Optional[str]) -> Optional[HookModule]:
        """按路径加载 hook 模块，并做缓存复用。"""
        if not hook_path:
            return None

        if Path(hook_path).is_absolute():
            hook_file = Path(hook_path).resolve()
        else:
            hook_file = (self._base_dir / 'hooks' / hook_path).resolve()

        cache_key = str(hook_file)
        if cache_key in self._hook_cache:
            return self._hook_cache[cache_key]

        if not hook_file.exists():
            self._logger.warning('Hook file not found: %s', hook_file)
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
                    self._logger.error('Hook file must export a class named Hook: %s', hook_file)
                    self._hook_cache[cache_key] = None
                    return None

                hook_instance = hook_class()
                self._hook_cache[cache_key] = hook_instance
                self._logger.info('Hook loaded successfully: %s', hook_file)
                return hook_instance
        except Exception as exc:
            self._logger.error('Failed to load hook %s: %s', hook_file, exc)
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
