#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 配置 schema、factory 与校验。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlparse

from ..utils.net import normalize_proxy_url

DEFAULT_PROVIDER_TIMEOUT_SECONDS = 300
DEFAULT_PROVIDER_MAX_RETRIES = 3
DEFAULT_PROVIDER_VERIFY_SSL = False
DEFAULT_PROVIDER_TRANSPORT = "http"
SUPPORTED_PROVIDER_TRANSPORTS = {"http", "websocket"}
SUPPORTED_PROVIDER_API_SCHEMES = {"http", "https", "ws", "wss"}


def clean_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_optional_positive_int(
    value: Any,
    *,
    default: Optional[int] = None,
    error_message: str = "Expected a positive integer",
) -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(error_message) from exc

    if parsed <= 0:
        raise ValueError(error_message)
    return parsed


def parse_optional_bool(
    value: Any,
    *,
    default: Optional[bool] = None,
    error_message: str = "Expected a boolean value",
) -> Optional[bool]:
    if value is None:
        return default

    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "":
            return default
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise ValueError(error_message)

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    raise ValueError(error_message)


def normalize_model_list(value: Any) -> List[str]:
    models: List[str] = []
    if value is None:
        return models

    if isinstance(value, str):
        raw_items = value.replace(",", "\n").splitlines()
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ValueError("model_list must be a list or newline-separated string")

    seen: set[str] = set()
    for item in raw_items:
        model = str(item).strip()
        if not model or model in seen:
            continue
        seen.add(model)
        models.append(model)
    return models


def resolve_provider_transport(api: str, transport: Any = None) -> str:
    """解析 provider 上游传输类型。"""
    scheme = urlparse(api).scheme.lower()
    if scheme not in SUPPORTED_PROVIDER_API_SCHEMES:
        supported_schemes = ", ".join(sorted(SUPPORTED_PROVIDER_API_SCHEMES))
        raise ValueError(f"Provider api must use one of: {supported_schemes}")

    normalized_transport = clean_optional_string(transport)
    if normalized_transport is not None:
        lowered = normalized_transport.lower()
        if lowered not in SUPPORTED_PROVIDER_TRANSPORTS:
            supported = ", ".join(sorted(SUPPORTED_PROVIDER_TRANSPORTS))
            raise ValueError(f"Provider transport must be one of: {supported}")
        if lowered == "http" and scheme in {"ws", "wss"}:
            raise ValueError("Provider transport 'http' requires api to use http:// or https://")
        return lowered

    if scheme in {"ws", "wss"}:
        return "websocket"
    return DEFAULT_PROVIDER_TRANSPORT


@dataclass(frozen=True, slots=True)
class ProviderConfigSchema:
    """显式表示配置文件中的单个 provider。"""

    name: str
    api: str
    transport: str = DEFAULT_PROVIDER_TRANSPORT
    api_key: Optional[str] = None
    proxy: Optional[str] = None
    timeout_seconds: Optional[int] = None
    max_retries: Optional[int] = None
    verify_ssl: Optional[bool] = None
    model_list: tuple[str, ...] = ()
    hook: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ProviderConfigSchema":
        """从管理接口 payload 构造规范化 schema。"""
        return cls._from_mapping(payload)

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "ProviderConfigSchema":
        """从配置文件对象构造规范化 schema。"""
        return cls._from_mapping(config)

    @classmethod
    def _from_mapping(cls, config: Mapping[str, Any]) -> "ProviderConfigSchema":
        if not isinstance(config, Mapping):
            raise ValueError("Provider config must be an object")

        name = clean_optional_string(config.get("name"))
        api = clean_optional_string(config.get("api"))
        if name is None:
            raise ValueError("Provider name is required")
        if api is None:
            raise ValueError("Provider api is required")

        return cls(
            name=name,
            api=api,
            transport=resolve_provider_transport(api, config.get("transport")),
            api_key=clean_optional_string(config.get("api_key")),
            proxy=normalize_proxy_url(config.get("proxy")),
            timeout_seconds=parse_optional_positive_int(config.get("timeout_seconds")),
            max_retries=parse_optional_positive_int(config.get("max_retries")),
            verify_ssl=parse_optional_bool(config.get("verify_ssl")),
            model_list=tuple(normalize_model_list(config.get("model_list"))),
            hook=clean_optional_string(config.get("hook")),
        )

    def to_mapping(self) -> Dict[str, Any]:
        """转换为适合写回配置文件的普通 dict。"""
        config: Dict[str, Any] = {
            "name": self.name,
            "api": self.api,
            "transport": self.transport,
        }

        if self.api_key is not None:
            config["api_key"] = self.api_key
        if self.proxy is not None:
            config["proxy"] = self.proxy
        if self.timeout_seconds is not None:
            config["timeout_seconds"] = self.timeout_seconds
        if self.max_retries is not None:
            config["max_retries"] = self.max_retries
        if self.verify_ssl is not None:
            config["verify_ssl"] = self.verify_ssl
        if self.model_list:
            config["model_list"] = list(self.model_list)
        if self.hook is not None:
            config["hook"] = self.hook

        return config


@dataclass(frozen=True, slots=True)
class RuntimeProviderSpec:
    """显式表示运行时使用的 provider 规格。"""

    name: str
    api: str
    transport: str
    api_key: str
    model_list: tuple[str, ...]
    proxy: Optional[str]
    timeout_seconds: int
    max_retries: int
    verify_ssl: bool
    hook: Optional[str]

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "RuntimeProviderSpec":
        return cls.from_schema(ProviderConfigSchema.from_mapping(config))

    @classmethod
    def from_schema(cls, config: ProviderConfigSchema) -> "RuntimeProviderSpec":
        return cls(
            name=config.name,
            api=config.api,
            transport=config.transport,
            api_key=config.api_key or "",
            model_list=config.model_list,
            proxy=config.proxy,
            timeout_seconds=config.timeout_seconds or DEFAULT_PROVIDER_TIMEOUT_SECONDS,
            max_retries=config.max_retries or DEFAULT_PROVIDER_MAX_RETRIES,
            verify_ssl=(
                config.verify_ssl
                if config.verify_ssl is not None
                else DEFAULT_PROVIDER_VERIFY_SSL
            ),
            hook=config.hook,
        )


@dataclass(frozen=True, slots=True)
class ProviderRuntimeView:
    """ProviderManager 暴露的只读运行时视图。"""

    name: str
    api: str
    transport: str
    model_list: tuple[str, ...]
    proxy: Optional[str]
    timeout_seconds: int
    max_retries: int
    verify_ssl: bool
    hook: Optional[str]

    @classmethod
    def from_spec(cls, spec: RuntimeProviderSpec) -> "ProviderRuntimeView":
        return cls(
            name=spec.name,
            api=spec.api,
            transport=spec.transport,
            model_list=spec.model_list,
            proxy=spec.proxy,
            timeout_seconds=spec.timeout_seconds,
            max_retries=spec.max_retries,
            verify_ssl=spec.verify_ssl,
            hook=spec.hook,
        )


def build_provider_schemas(
    providers: Sequence[Mapping[str, Any]],
) -> tuple[ProviderConfigSchema, ...]:
    seen_names: set[str] = set()
    seen_model_keys: set[str] = set()
    schemas: List[ProviderConfigSchema] = []

    for index, provider in enumerate(providers):
        if not isinstance(provider, Mapping):
            raise ValueError(f"Provider entry at index {index} must be an object")

        schema = ProviderConfigSchema.from_mapping(provider)
        if schema.name in seen_names:
            raise ValueError(f"Duplicate provider name detected: {schema.name}")
        seen_names.add(schema.name)

        for model in schema.model_list:
            model_key = f"{schema.name}/{model}"
            if model_key in seen_model_keys:
                raise ValueError(f"Duplicate provider model mapping detected: {model_key}")
            seen_model_keys.add(model_key)

        schemas.append(schema)

    return tuple(schemas)


def validate_provider_definitions(providers: Sequence[Mapping[str, Any]]) -> None:
    build_provider_schemas(providers)
