#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型权限目录服务。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from ..application.app_context import AppContext
from ..config.provider_config import normalize_model_list


class ModelListProvider(Protocol):
    """提供当前可用模型名的服务协议。"""

    def list_model_names(self) -> Iterable[str]: ...


class ModelCatalogService:
    """汇总控制平面模型权限可选目录。"""

    def __init__(
        self,
        ctx: AppContext,
        *,
        codex_oauth_service: ModelListProvider | None = None,
        claude_oauth_service: ModelListProvider | None = None,
    ) -> None:
        self._logger = ctx.logger
        self._config_manager = getattr(ctx, "config_manager", None)
        self._codex_oauth_service = codex_oauth_service
        self._claude_oauth_service = claude_oauth_service

    def list_permission_model_names(self) -> tuple[str, ...]:
        """返回模型权限可选择的 Provider 与 OAuth 模型。"""
        return tuple(
            sorted(
                dict.fromkeys(
                    [
                        *self._list_provider_model_names(),
                        *self._list_oauth_model_names("Codex", self._codex_oauth_service),
                        *self._list_oauth_model_names("Claude", self._claude_oauth_service),
                    ]
                )
            )
        )

    def _list_provider_model_names(self) -> tuple[str, ...]:
        """读取配置中声明的 Provider 模型，包含已禁用 Provider。"""
        if self._config_manager is None:
            return ()

        try:
            config = self._config_manager.get_raw_config()
        except Exception as exc:
            self._logger.error("Failed to load config model catalog: %s", exc)
            return ()

        raw_providers = config.get("providers", [])
        if raw_providers is None or not isinstance(raw_providers, list):
            return ()

        model_names: list[str] = []
        seen_models: set[str] = set()
        for raw_provider in raw_providers:
            if not isinstance(raw_provider, dict):
                continue

            provider_name = str(raw_provider.get("name") or "").strip()
            if not provider_name:
                continue

            try:
                provider_models = normalize_model_list(raw_provider.get("model_list"))
            except ValueError:
                continue

            for provider_model in provider_models:
                model_key = f"{provider_name}/{provider_model}"
                if model_key in seen_models:
                    continue
                seen_models.add(model_key)
                model_names.append(model_key)

        return tuple(model_names)

    def _list_oauth_model_names(
        self,
        service_name: str,
        model_provider: ModelListProvider | None,
    ) -> tuple[str, ...]:
        """读取 OAuth 模型目录，服务不存在时返回空列表。"""
        if model_provider is None:
            return ()

        try:
            model_names: list[str] = []
            for model_name in model_provider.list_model_names():
                normalized_model_name = str(model_name or "").strip()
                if normalized_model_name:
                    model_names.append(normalized_model_name)
            return tuple(model_names)
        except Exception as exc:
            self._logger.warning("%s OAuth model catalog skipped: error=%s", service_name, exc)
            return ()
