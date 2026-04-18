#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider and auth group config schema, factory helpers, and validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlparse

from ..utils.net import normalize_proxy_url

DEFAULT_PROVIDER_TIMEOUT_SECONDS = 1200
DEFAULT_PROVIDER_MAX_RETRIES = 3
DEFAULT_PROVIDER_VERIFY_SSL = False
DEFAULT_PROVIDER_TRANSPORT = "http"
DEFAULT_PROVIDER_SOURCE_FORMAT = "openai_chat"
DEFAULT_PROVIDER_TARGET_FORMAT = "openai_chat"
SUPPORTED_PROVIDER_PROTOCOLS = (
    "openai_chat",
    "openai_responses",
    "claude_chat",
)
DEFAULT_PROVIDER_TARGET_FORMATS = SUPPORTED_PROVIDER_PROTOCOLS
DEFAULT_AUTH_GROUP_STRATEGY = "least_inflight"
DEFAULT_AUTH_GROUP_COOLDOWN_SECONDS_ON_429 = 60
SUPPORTED_PROVIDER_TRANSPORTS = {"http", "websocket"}
SUPPORTED_PROVIDER_API_SCHEMES = {"http", "https", "ws", "wss"}
SUPPORTED_AUTH_GROUP_STRATEGIES = {DEFAULT_AUTH_GROUP_STRATEGY}
SUPPORTED_PROVIDER_FIELDS = {
    "name",
    "enabled",
    "api",
    "transport",
    "source_format",
    "api_key",
    "auth_group",
    "proxy",
    "timeout_seconds",
    "max_retries",
    "verify_ssl",
    "model_list",
    "hook",
}
SUPPORTED_AUTH_GROUP_FIELDS = {
    "name",
    "strategy",
    "cooldown_seconds_on_429",
    "entries",
}
SUPPORTED_AUTH_ENTRY_FIELDS = {
    "id",
    "enabled",
    "headers",
    "max_concurrency",
    "cooldown_seconds_on_429",
    "request_quota_per_minute",
    "request_quota_per_day",
    "token_quota_per_minute",
    "token_quota_per_day",
}


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


def normalize_headers(value: Any) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("headers must be an object")

    headers: Dict[str, str] = {}
    seen_names: set[str] = set()
    for raw_key, raw_value in value.items():
        header_name = clean_optional_string(raw_key)
        header_value = "" if raw_value is None else str(raw_value).strip()
        if header_name is None:
            raise ValueError("header name must not be empty")
        normalized_name = header_name.lower()
        if normalized_name in seen_names:
            raise ValueError(f"duplicate header detected: {header_name}")
        seen_names.add(normalized_name)
        headers[header_name] = header_value
    return headers


def resolve_provider_transport(api: str, transport: Any = None) -> str:
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


def resolve_provider_protocol(
    value: Any,
    *,
    default: str,
    field_name: str,
) -> str:
    resolved = (clean_optional_string(value) or default).lower()
    if resolved not in SUPPORTED_PROVIDER_PROTOCOLS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDER_PROTOCOLS))
        raise ValueError(f"Provider {field_name} must be one of: {supported}")
    return resolved


def normalize_provider_target_formats(value: Any, *, field_name: str = "target_formats") -> tuple[str, ...]:
    raw_items: Sequence[Any]
    if value is None:
        raw_items = ()
    elif isinstance(value, str):
        raw_items = value.replace(",", "\n").splitlines()
    elif isinstance(value, Sequence):
        raw_items = value
    else:
        raise ValueError(f"{field_name} must be a list or newline-separated string")

    target_formats: List[str] = []
    seen_formats: set[str] = set()
    for item in raw_items:
        normalized_item = clean_optional_string(item)
        if normalized_item is None:
            continue
        resolved = resolve_provider_protocol(
            normalized_item,
            default=DEFAULT_PROVIDER_TARGET_FORMAT,
            field_name=field_name,
        )
        if resolved in seen_formats:
            continue
        seen_formats.add(resolved)
        target_formats.append(resolved)
    return tuple(target_formats)

def resolve_provider_target_formats(target_formats_value: Any) -> tuple[str, ...]:
    resolved_target_formats = list(
        normalize_provider_target_formats(target_formats_value, field_name="target_formats")
    )
    if not resolved_target_formats:
        resolved_target_formats = list(DEFAULT_PROVIDER_TARGET_FORMATS)
    return tuple(resolved_target_formats)


def resolve_auth_group_strategy(value: Any) -> str:
    resolved = (clean_optional_string(value) or DEFAULT_AUTH_GROUP_STRATEGY).lower()
    if resolved not in SUPPORTED_AUTH_GROUP_STRATEGIES:
        supported = ", ".join(sorted(SUPPORTED_AUTH_GROUP_STRATEGIES))
        raise ValueError(f"Auth group strategy must be one of: {supported}")
    return resolved


def _validate_supported_fields(
    config: Mapping[str, Any],
    *,
    supported_fields: set[str],
    field_group_name: str,
) -> None:
    unknown_fields = sorted(
        str(key)
        for key in config.keys()
        if str(key) not in supported_fields
    )
    if unknown_fields:
        raise ValueError(f"Unsupported {field_group_name} field(s): {', '.join(unknown_fields)}")


def validate_provider_fields(config: Mapping[str, Any]) -> None:
    _validate_supported_fields(
        config,
        supported_fields=SUPPORTED_PROVIDER_FIELDS,
        field_group_name="provider",
    )


def validate_auth_group_fields(config: Mapping[str, Any]) -> None:
    _validate_supported_fields(
        config,
        supported_fields=SUPPORTED_AUTH_GROUP_FIELDS,
        field_group_name="auth_group",
    )


def validate_auth_entry_fields(config: Mapping[str, Any]) -> None:
    _validate_supported_fields(
        config,
        supported_fields=SUPPORTED_AUTH_ENTRY_FIELDS,
        field_group_name="auth_entry",
    )


@dataclass(frozen=True)
class AuthEntrySchema:
    """Normalized auth entry configuration."""

    id: str
    enabled: bool = True
    headers: tuple[tuple[str, str], ...] = ()
    max_concurrency: Optional[int] = None
    cooldown_seconds_on_429: Optional[int] = None
    request_quota_per_minute: Optional[int] = None
    request_quota_per_day: Optional[int] = None
    token_quota_per_minute: Optional[int] = None
    token_quota_per_day: Optional[int] = None

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "AuthEntrySchema":
        if not isinstance(config, Mapping):
            raise ValueError("Auth entry config must be an object")

        validate_auth_entry_fields(config)
        entry_id = clean_optional_string(config.get("id"))
        if entry_id is None:
            raise ValueError("Auth entry id is required")

        headers = normalize_headers(config.get("headers"))
        if not headers:
            raise ValueError(f"Auth entry '{entry_id}' must define at least one header")

        return cls(
            id=entry_id,
            enabled=parse_optional_bool(
                config.get("enabled"),
                default=True,
                error_message="Auth entry enabled must be a boolean value",
            )
            is not False,
            headers=tuple(headers.items()),
            max_concurrency=parse_optional_positive_int(
                config.get("max_concurrency"),
                error_message="Auth entry max_concurrency must be a positive integer",
            ),
            cooldown_seconds_on_429=parse_optional_positive_int(
                config.get("cooldown_seconds_on_429"),
                error_message="Auth entry cooldown_seconds_on_429 must be a positive integer",
            ),
            request_quota_per_minute=parse_optional_positive_int(
                config.get("request_quota_per_minute"),
                error_message="Auth entry request_quota_per_minute must be a positive integer",
            ),
            request_quota_per_day=parse_optional_positive_int(
                config.get("request_quota_per_day"),
                error_message="Auth entry request_quota_per_day must be a positive integer",
            ),
            token_quota_per_minute=parse_optional_positive_int(
                config.get("token_quota_per_minute"),
                error_message="Auth entry token_quota_per_minute must be a positive integer",
            ),
            token_quota_per_day=parse_optional_positive_int(
                config.get("token_quota_per_day"),
                error_message="Auth entry token_quota_per_day must be a positive integer",
            ),
        )

    def to_mapping(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "id": self.id,
            "enabled": bool(self.enabled),
            "headers": dict(self.headers),
        }
        if self.max_concurrency is not None:
            config["max_concurrency"] = self.max_concurrency
        if self.cooldown_seconds_on_429 is not None:
            config["cooldown_seconds_on_429"] = self.cooldown_seconds_on_429
        if self.request_quota_per_minute is not None:
            config["request_quota_per_minute"] = self.request_quota_per_minute
        if self.request_quota_per_day is not None:
            config["request_quota_per_day"] = self.request_quota_per_day
        if self.token_quota_per_minute is not None:
            config["token_quota_per_minute"] = self.token_quota_per_minute
        if self.token_quota_per_day is not None:
            config["token_quota_per_day"] = self.token_quota_per_day
        return config

    def headers_mapping(self) -> Dict[str, str]:
        return dict(self.headers)


@dataclass(frozen=True)
class AuthGroupSchema:
    """Normalized auth group configuration."""

    name: str
    strategy: str = DEFAULT_AUTH_GROUP_STRATEGY
    cooldown_seconds_on_429: int = DEFAULT_AUTH_GROUP_COOLDOWN_SECONDS_ON_429
    entries: tuple[AuthEntrySchema, ...] = ()

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "AuthGroupSchema":
        if not isinstance(config, Mapping):
            raise ValueError("Auth group config must be an object")

        validate_auth_group_fields(config)
        name = clean_optional_string(config.get("name"))
        if name is None:
            raise ValueError("Auth group name is required")

        raw_entries = config.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError(f"Auth group '{name}' must define a non-empty entries list")

        seen_entry_ids: set[str] = set()
        entries: List[AuthEntrySchema] = []
        for index, raw_entry in enumerate(raw_entries):
            entry = AuthEntrySchema.from_mapping(raw_entry)
            if entry.id in seen_entry_ids:
                raise ValueError(f"Duplicate auth entry id detected in group '{name}': {entry.id}")
            seen_entry_ids.add(entry.id)
            entries.append(entry)

        return cls(
            name=name,
            strategy=resolve_auth_group_strategy(config.get("strategy")),
            cooldown_seconds_on_429=parse_optional_positive_int(
                config.get("cooldown_seconds_on_429"),
                default=DEFAULT_AUTH_GROUP_COOLDOWN_SECONDS_ON_429,
                error_message="Auth group cooldown_seconds_on_429 must be a positive integer",
            )
            or DEFAULT_AUTH_GROUP_COOLDOWN_SECONDS_ON_429,
            entries=tuple(entries),
        )

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "strategy": self.strategy,
            "cooldown_seconds_on_429": self.cooldown_seconds_on_429,
            "entries": [entry.to_mapping() for entry in self.entries],
        }


@dataclass(frozen=True)
class ProviderConfigSchema:
    """Normalized provider configuration schema."""

    name: str
    api: str
    enabled: bool = True
    transport: str = DEFAULT_PROVIDER_TRANSPORT
    source_format: str = DEFAULT_PROVIDER_SOURCE_FORMAT
    target_formats: tuple[str, ...] = DEFAULT_PROVIDER_TARGET_FORMATS
    api_key: Optional[str] = None
    auth_group: Optional[str] = None
    proxy: Optional[str] = None
    timeout_seconds: Optional[int] = None
    max_retries: Optional[int] = None
    verify_ssl: Optional[bool] = None
    model_list: tuple[str, ...] = ()
    hook: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ProviderConfigSchema":
        return cls._from_mapping(payload)

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "ProviderConfigSchema":
        return cls._from_mapping(config)

    @classmethod
    def _from_mapping(cls, config: Mapping[str, Any]) -> "ProviderConfigSchema":
        if not isinstance(config, Mapping):
            raise ValueError("Provider config must be an object")

        validate_provider_fields(config)

        name = clean_optional_string(config.get("name"))
        api = clean_optional_string(config.get("api"))
        api_key = clean_optional_string(config.get("api_key"))
        auth_group = clean_optional_string(config.get("auth_group"))
        if name is None:
            raise ValueError("Provider name is required")
        if api is None:
            raise ValueError("Provider api is required")
        if api_key and auth_group:
            raise ValueError("Provider must define either auth_group or api_key, not both")

        return cls(
            name=name,
            enabled=parse_optional_bool(
                config.get("enabled"),
                default=True,
                error_message="Provider enabled must be a boolean value",
            )
            is not False,
            api=api,
            transport=resolve_provider_transport(api, config.get("transport")),
            source_format=resolve_provider_protocol(
                config.get("source_format"),
                default=DEFAULT_PROVIDER_SOURCE_FORMAT,
                field_name="source_format",
            ),
            target_formats=DEFAULT_PROVIDER_TARGET_FORMATS,
            api_key=api_key,
            auth_group=auth_group,
            proxy=normalize_proxy_url(config.get("proxy")),
            timeout_seconds=parse_optional_positive_int(config.get("timeout_seconds")),
            max_retries=parse_optional_positive_int(config.get("max_retries")),
            verify_ssl=parse_optional_bool(config.get("verify_ssl")),
            model_list=tuple(normalize_model_list(config.get("model_list"))),
            hook=clean_optional_string(config.get("hook")),
        )

    @property
    def primary_target_format(self) -> str:
        return self.target_formats[0]

    @property
    def target_format(self) -> str:
        # 兼容旧调用方的只读别名。
        return self.primary_target_format

    def to_mapping(self) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "name": self.name,
            "enabled": bool(self.enabled),
            "api": self.api,
            "transport": self.transport,
            "source_format": self.source_format,
        }

        if self.api_key is not None:
            config["api_key"] = self.api_key
        if self.auth_group is not None:
            config["auth_group"] = self.auth_group
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

    def to_storage_mapping(self) -> Dict[str, Any]:
        return self.to_mapping()


@dataclass(frozen=True)
class RuntimeProviderSpec:
    """Provider runtime specification."""

    name: str
    enabled: bool
    api: str
    transport: str
    source_format: str
    target_formats: tuple[str, ...]
    api_key: str
    auth_group: Optional[str]
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
            enabled=bool(config.enabled),
            api=config.api,
            transport=config.transport,
            source_format=config.source_format,
            target_formats=config.target_formats,
            api_key=config.api_key or "",
            auth_group=config.auth_group,
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

    @property
    def primary_target_format(self) -> str:
        return self.target_formats[0]


@dataclass(frozen=True)
class ProviderRuntimeView:
    """Read-only runtime view exposed by ProviderManager."""

    name: str
    enabled: bool
    api: str
    transport: str
    source_format: str
    target_formats: tuple[str, ...]
    auth_group: Optional[str]
    legacy_api_key: bool
    model_list: tuple[str, ...]
    proxy: Optional[str]
    timeout_seconds: int
    max_retries: int
    verify_ssl: bool
    hook: Optional[str]

    @classmethod
    def from_spec(cls, spec: RuntimeProviderSpec, *, legacy_api_key: bool = False) -> "ProviderRuntimeView":
        return cls(
            name=spec.name,
            enabled=bool(spec.enabled),
            api=spec.api,
            transport=spec.transport,
            source_format=spec.source_format,
            target_formats=spec.target_formats,
            auth_group=spec.auth_group,
            legacy_api_key=legacy_api_key,
            model_list=spec.model_list,
            proxy=spec.proxy,
            timeout_seconds=spec.timeout_seconds,
            max_retries=spec.max_retries,
            verify_ssl=spec.verify_ssl,
            hook=spec.hook,
        )

    @property
    def primary_target_format(self) -> str:
        return self.target_formats[0]


def build_auth_group_schemas(
    auth_groups: Sequence[Mapping[str, Any]],
) -> tuple[AuthGroupSchema, ...]:
    seen_names: set[str] = set()
    schemas: List[AuthGroupSchema] = []

    for index, auth_group in enumerate(auth_groups):
        if not isinstance(auth_group, Mapping):
            raise ValueError(f"Auth group entry at index {index} must be an object")

        schema = AuthGroupSchema.from_mapping(auth_group)
        if schema.name in seen_names:
            raise ValueError(f"Duplicate auth group name detected: {schema.name}")
        seen_names.add(schema.name)
        schemas.append(schema)

    return tuple(schemas)


def build_provider_schemas(
    providers: Sequence[Mapping[str, Any]],
    *,
    available_auth_group_names: Optional[Iterable[str]] = None,
) -> tuple[ProviderConfigSchema, ...]:
    auth_group_names = set(available_auth_group_names or ())
    seen_names: set[str] = set()
    seen_model_keys: set[str] = set()
    schemas: List[ProviderConfigSchema] = []

    for index, provider in enumerate(providers):
        if not isinstance(provider, Mapping):
            raise ValueError(f"Provider entry at index {index} must be an object")

        schema = ProviderConfigSchema.from_mapping(provider)
        if schema.name in seen_names:
            raise ValueError(f"Duplicate provider name detected: {schema.name}")
        if (
            schema.auth_group
            and available_auth_group_names is not None
            and schema.auth_group not in auth_group_names
        ):
            raise ValueError(f"Provider references unknown auth_group: {schema.auth_group}")
        seen_names.add(schema.name)

        for model in schema.model_list:
            model_key = f"{schema.name}/{model}"
            if model_key in seen_model_keys:
                raise ValueError(f"Duplicate provider model mapping detected: {model_key}")
            seen_model_keys.add(model_key)

        schemas.append(schema)

    return tuple(schemas)


def validate_auth_group_definitions(auth_groups: Sequence[Mapping[str, Any]]) -> None:
    build_auth_group_schemas(auth_groups)


def validate_provider_definitions(providers: Sequence[Mapping[str, Any]]) -> None:
    build_provider_schemas(providers)


def validate_auth_group_provider_definitions(
    auth_groups: Sequence[Mapping[str, Any]],
    providers: Sequence[Mapping[str, Any]],
) -> None:
    auth_group_schemas = build_auth_group_schemas(auth_groups)
    build_provider_schemas(
        providers,
        available_auth_group_names={schema.name for schema in auth_group_schemas},
    )
