#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 管理器：负责加载配置、构建运行时对象并暴露只读注册表接口。"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional, Sequence

from ..application.app_context import AppContext, Logger
from ..external import LLMProvider
from ..hooks import HookModule
from .auth_group_manager import AuthGroupManager
from .provider_config import ProviderConfigSchema, ProviderRuntimeView, RuntimeProviderSpec


class ProviderManager:
    """管理模型到 provider 的运行时映射关系。"""

    def __init__(self, ctx: AppContext, auth_group_manager: AuthGroupManager):
        self._base_dir = ctx.root_path.resolve()
        self._logger: Logger = ctx.logger
        self._auth_group_manager = auth_group_manager
        self._provider_by_model: Dict[str, LLMProvider] = {}
        self._provider_by_name: Dict[str, LLMProvider] = {}
        self._provider_views_by_name: Dict[str, ProviderRuntimeView] = {}
        self._hook_cache: Dict[str, Optional[HookModule]] = {}

    def load_providers(self, providers_config: Sequence[ProviderConfigSchema]) -> None:
        """重载 provider 配置。"""
        self._provider_by_model.clear()
        self._provider_by_name.clear()
        self._provider_views_by_name.clear()
        self._hook_cache.clear()

        for provider_cfg in providers_config:
            self._load_provider(provider_cfg)

    def get_provider_for_model(self, model_name: str) -> Optional[LLMProvider]:
        """按模型 key 查询运行时 provider。"""
        return self._provider_by_model.get(model_name)

    def list_model_names(self) -> tuple[str, ...]:
        """返回当前已注册的模型 key 列表。"""
        return tuple(sorted(self._provider_by_model.keys()))

    def get_provider_view(self, provider_name: str) -> Optional[ProviderRuntimeView]:
        """按 provider 名称返回只读运行时视图。"""
        return self._provider_views_by_name.get(str(provider_name).strip())

    def list_provider_views(self) -> tuple[ProviderRuntimeView, ...]:
        """返回所有 provider 的只读运行时视图。"""
        return tuple(
            self._provider_views_by_name[name]
            for name in sorted(self._provider_views_by_name.keys())
        )

    def has_model(self, model_name: str) -> bool:
        """判断模型 key 是否已注册。"""
        return model_name in self._provider_by_model

    def _load_provider(self, config: ProviderConfigSchema) -> None:
        """加载单个 provider，并注册其模型映射。"""
        spec = RuntimeProviderSpec.from_schema(config)
        if spec.name in self._provider_by_name:
            raise ValueError(f"Duplicate provider name detected: {spec.name}")

        if not spec.enabled:
            self._logger.info("Provider '%s' disabled: skipped runtime registration", spec.name)
            return

        if not spec.model_list:
            self._logger.warning("Provider '%s' skipped: model_list is empty", spec.name)
            return

        legacy_auth_group_name: Optional[str] = None
        resolved_auth_group = spec.auth_group
        if resolved_auth_group is None and spec.api_key:
            legacy_auth_group_name = self._auth_group_manager.register_legacy_provider_group(
                spec.name,
                spec.api_key,
            )
            resolved_auth_group = legacy_auth_group_name
        runtime_spec = replace(spec, auth_group=resolved_auth_group)

        provider = LLMProvider(
            name=runtime_spec.name,
            api=runtime_spec.api,
            transport=runtime_spec.transport,
            source_format=runtime_spec.source_format,
            target_formats=runtime_spec.target_formats,
            api_key=spec.api_key,
            auth_group=runtime_spec.auth_group,
            model_list=runtime_spec.model_list,
            proxy=runtime_spec.proxy,
            timeout_seconds=runtime_spec.timeout_seconds,
            max_retries=runtime_spec.max_retries,
            verify_ssl=runtime_spec.verify_ssl,
            hook=self._load_hook(runtime_spec.hook),
        )

        self._provider_by_name[runtime_spec.name] = provider
        self._provider_views_by_name[runtime_spec.name] = ProviderRuntimeView.from_spec(
            runtime_spec,
            legacy_api_key=legacy_auth_group_name is not None,
        )

        for model in runtime_spec.model_list:
            model_key = f"{runtime_spec.name}/{model}"
            if model_key in self._provider_by_model:
                raise ValueError(f"Duplicate provider model mapping detected: {model_key}")
            self._provider_by_model[model_key] = provider

        self._logger.info(
            "Loaded provider: %s, models: %s, auth_group=%s",
            runtime_spec.name,
            list(runtime_spec.model_list),
            runtime_spec.auth_group or "<none>",
        )

    def _load_hook(self, hook_path: Optional[str]) -> Optional[HookModule]:
        """按路径加载 hook 模块，并做缓存复用。"""
        if not hook_path:
            return None

        if Path(hook_path).is_absolute():
            hook_file = Path(hook_path).resolve()
        else:
            hook_file = (self._base_dir / "hooks" / hook_path).resolve()

        cache_key = str(hook_file)
        if cache_key in self._hook_cache:
            return self._hook_cache[cache_key]

        if not hook_file.exists():
            self._logger.warning("Hook file not found: %s", hook_file)
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
            path_hash = hashlib.sha1(normalized_path.encode("utf-8")).hexdigest()[:12]
            module_name = f"hook_{hook_name}_{path_hash}"
            spec = importlib.util.spec_from_file_location(module_name, hook_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hook_class = getattr(module, "Hook", None)
                if hook_class is None or not inspect.isclass(hook_class):
                    self._logger.error("Hook file must export a class named Hook: %s", hook_file)
                    self._hook_cache[cache_key] = None
                    return None

                hook_instance = hook_class()
                self._hook_cache[cache_key] = hook_instance
                self._logger.info("Hook loaded successfully: %s", hook_file)
                return hook_instance
        except Exception as exc:
            self._logger.error("Failed to load hook %s: %s", hook_file, exc)
        finally:
            if path_inserted:
                try:
                    sys.path.remove(hook_dir)
                except ValueError:
                    pass

        self._hook_cache[cache_key] = None
        return None
