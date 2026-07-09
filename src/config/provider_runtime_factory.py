#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 运行时对象构建工厂。"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from weakref import ReferenceType, ref

from ..application.app_context import AppContext, Logger
from ..external.llm_provider import LLMProvider
from ..hooks import HookContext, HookModule
from .provider_config import ProviderConfigSchema, RuntimeProviderSpec

HookFileSignature = tuple[int, int]


@dataclass(frozen=True)
class _HookCacheEntry:
    """Hook 文件签名和实例弱引用缓存。"""

    signature: HookFileSignature | None
    hook_ref: ReferenceType[Any] | None = None


class _ReloadingHookProxy:
    """按文件签名热更新实际 Hook 实例。"""

    def __init__(self, runtime_factory: "ProviderRuntimeFactory", hook_path: str):
        self._runtime_factory = runtime_factory
        self._hook_path = hook_path
        self._hook: HookModule | None = None

    def header_hook(self, ctx: HookContext, headers: dict[str, str]) -> dict[str, str] | None:
        hook = self._resolve_hook()
        if hook is None:
            return headers
        header_hook = getattr(hook, "header_hook", None)
        if not callable(header_hook):
            return headers
        return header_hook(ctx, headers)

    def request_guard(self, ctx: HookContext, body: dict[str, Any]) -> dict[str, Any] | None:
        hook = self._resolve_hook()
        if hook is None:
            return body
        request_guard = getattr(hook, "request_guard", None)
        if not callable(request_guard):
            return body
        return request_guard(ctx, body)

    def response_guard(self, ctx: HookContext, body: Any) -> Any:
        hook = self._resolve_hook()
        if hook is None:
            return body
        response_guard = getattr(hook, "response_guard", None)
        if not callable(response_guard):
            return body
        return response_guard(ctx, body)

    def fetch_models(self, ctx: HookContext, payload: dict[str, Any]) -> Any | None:
        hook = self._resolve_hook()
        if hook is None:
            return None
        fetch_models = getattr(hook, "fetch_models", None)
        if not callable(fetch_models):
            return None
        return fetch_models(ctx, payload)

    def _resolve_hook(self) -> HookModule | None:
        hook = self._runtime_factory._load_hook(self._hook_path)
        self._hook = hook
        return hook


class ProviderRuntimeFactory:
    """负责把 provider schema 构造成运行时对象，并复用 hook 缓存。"""

    def __init__(self, ctx: AppContext):
        self._base_dir = ctx.root_path.resolve()
        self._logger: Logger = ctx.logger
        self._hook_cache: dict[str, _HookCacheEntry] = {}

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
            proxy_mode=spec.proxy_mode,
            proxy=spec.proxy,
            timeout_seconds=spec.timeout_seconds,
            max_retries=spec.max_retries,
            verify_ssl=spec.verify_ssl,
            hook=self._build_hook_proxy(spec.hook),
        )

    def _build_hook_proxy(self, hook_path: str | None) -> HookModule | None:
        """构造按需重载的 Hook 代理。"""
        if not hook_path:
            return None
        hook_file = self._resolve_hook_file(hook_path)
        if hook_file is None:
            return None
        return _ReloadingHookProxy(self, hook_path)

    def _load_hook(self, hook_path: str | None) -> HookModule | None:
        """按路径加载 hook 模块，并按文件签名复用。"""
        if not hook_path:
            return None

        hook_file = self._resolve_hook_file(hook_path)
        if hook_file is None:
            return None
        cache_key = str(hook_file)

        signature = self._read_hook_signature(hook_file)
        cached = self._hook_cache.get(cache_key)
        if cached and cached.signature == signature:
            if cached.hook_ref is None:
                return None
            cached_hook = cached.hook_ref()
            if cached_hook is not None:
                return cached_hook
            self._hook_cache.pop(cache_key, None)

        if signature is None:
            if cached and cached.hook_ref is not None:
                cached_hook = cached.hook_ref()
                if cached_hook is not None:
                    return cached_hook
            if cached is None or cached.signature is not None:
                self._logger.warning("Hook file not found: %s", hook_file)
            self._hook_cache[cache_key] = _HookCacheEntry(signature=None)
            return None

        hook_instance = self._load_hook_from_file(hook_file, signature)
        if hook_instance is not None:
            try:
                self._hook_cache[cache_key] = _HookCacheEntry(
                    signature=signature,
                    hook_ref=ref(hook_instance),
                )
            except TypeError:
                self._hook_cache.pop(cache_key, None)
            return hook_instance

        self._hook_cache[cache_key] = _HookCacheEntry(signature=signature)
        return None

    def _resolve_hook_file(self, hook_path: str) -> Path | None:
        """解析 Hook 文件路径，限制在 hooks 目录内。"""
        raw_hook_path = Path(hook_path)
        if raw_hook_path.is_absolute():
            self._logger.warning("Absolute hook path is not allowed: %s", hook_path)
            return None

        hooks_dir = (self._base_dir / "hooks").resolve()
        hook_file = (hooks_dir / raw_hook_path).resolve()
        try:
            hook_file.relative_to(hooks_dir)
        except ValueError:
            self._logger.warning("Hook path must stay under hooks directory: %s", hook_path)
            return None
        return hook_file

    @staticmethod
    def _read_hook_signature(hook_file: Path) -> HookFileSignature | None:
        """读取 Hook 文件签名。"""
        try:
            stat_result = hook_file.stat()
        except FileNotFoundError:
            return None
        if not hook_file.is_file():
            return None
        return stat_result.st_mtime_ns, stat_result.st_size

    def _load_hook_from_file(self, hook_file: Path, signature: HookFileSignature) -> HookModule | None:
        """从 Hook 文件创建新实例。"""
        if not hook_file.exists():
            self._logger.warning("Hook file not found: %s", hook_file)
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
            module_name = f"hook_{hook_name}_{path_hash}_{signature[0]}_{signature[1]}"
            spec = importlib.util.spec_from_file_location(module_name, hook_file)
            if spec:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                try:
                    source = hook_file.read_text(encoding="utf-8")
                    exec(compile(source, str(hook_file), "exec"), module.__dict__)
                finally:
                    sys.modules.pop(module_name, None)
                hook_class = getattr(module, "Hook", None)
                if hook_class is None or not inspect.isclass(hook_class):
                    self._logger.error("Hook file must export a class named Hook: %s", hook_file)
                    return None

                hook_instance = hook_class()
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

        return None
