#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 运行时对象构建工厂。"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Optional

from ..application.app_context import AppContext, Logger
from ..external.llm_provider import LLMProvider
from ..hooks import HookModule
from .provider_config import ProviderConfigSchema, RuntimeProviderSpec


class ProviderRuntimeFactory:
    """负责把 provider schema 构造成运行时对象，并复用 hook 缓存。"""

    def __init__(self, ctx: AppContext):
        self._base_dir = ctx.root_path.resolve()
        self._logger: Logger = ctx.logger
        self._hook_cache: dict[str, Optional[HookModule]] = {}

    def clear_cache(self) -> None:
        """清理 hook 缓存。"""
        self._hook_cache.clear()

    def build_provider_from_schema(self, config: ProviderConfigSchema) -> LLMProvider:
        """从标准化 schema 构建运行时 provider。"""
        return self.build_provider_from_spec(RuntimeProviderSpec.from_schema(config))

    def build_provider_from_payload(self, payload: dict[str, object]) -> LLMProvider:
        """从原始 payload 校验并构建运行时 provider。"""
        return self.build_provider_from_schema(ProviderConfigSchema.from_payload(payload))

    def build_provider_from_spec(self, spec: RuntimeProviderSpec) -> LLMProvider:
        """从运行时 spec 构建运行时 provider。"""
        return LLMProvider(
            name=spec.name,
            api=spec.api,
            transport=spec.transport,
            source_format=spec.source_format,
            target_formats=spec.target_formats,
            api_key=spec.api_key,
            auth_group=spec.auth_group,
            model_list=spec.model_list,
            proxy=spec.proxy,
            timeout_seconds=spec.timeout_seconds,
            max_retries=spec.max_retries,
            verify_ssl=spec.verify_ssl,
            hook=self._load_hook(spec.hook),
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

        hook_dir = str(hook_file.parent)
        path_inserted = False
        try:
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
