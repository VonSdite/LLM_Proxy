#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 管理器：负责加载配置、构建运行时对象并暴露只读注册表接口。"""

from __future__ import annotations

from typing import Dict, Optional, Sequence

from ..application.app_context import AppContext, Logger
from ..external import LLMProvider
from .provider_config import ProviderConfigSchema, ProviderRuntimeView, RuntimeProviderSpec
from .provider_runtime_factory import ProviderRuntimeFactory


class ProviderManager:
    """管理模型到 provider 的运行时映射关系。"""

    def __init__(self, ctx: AppContext, runtime_factory: Optional[ProviderRuntimeFactory] = None):
        self._logger: Logger = ctx.logger
        self._runtime_factory = runtime_factory or ProviderRuntimeFactory(ctx)
        self._provider_by_model: Dict[str, LLMProvider] = {}
        self._provider_by_name: Dict[str, LLMProvider] = {}
        self._provider_views_by_name: Dict[str, ProviderRuntimeView] = {}

    def load_providers(self, providers_config: Sequence[ProviderConfigSchema]) -> None:
        """重载 provider 配置。"""
        self._provider_by_model.clear()
        self._provider_by_name.clear()
        self._provider_views_by_name.clear()
        self._runtime_factory.clear_cache()

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

        runtime_spec = spec

        provider = self._runtime_factory.build_provider_from_spec(runtime_spec)

        self._provider_by_name[runtime_spec.name] = provider
        self._provider_views_by_name[runtime_spec.name] = ProviderRuntimeView.from_spec(
            runtime_spec,
            legacy_api_key=bool(spec.api_key) and runtime_spec.auth_group is None,
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
