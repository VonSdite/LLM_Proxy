#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 配置的规范化与校验。"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..utils.net import normalize_proxy_url

DEFAULT_PROVIDER_TIMEOUT_SECONDS = 300
DEFAULT_PROVIDER_MAX_RETRIES = 3
DEFAULT_PROVIDER_VERIFY_SSL = False


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


def normalize_provider_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Provider payload must be an object")

    name = clean_optional_string(payload.get("name"))
    api = clean_optional_string(payload.get("api"))
    if name is None:
        raise ValueError("Provider name is required")
    if api is None:
        raise ValueError("Provider api is required")

    normalized: Dict[str, Any] = {
        "name": name,
        "api": api,
    }

    api_key = clean_optional_string(payload.get("api_key"))
    if api_key is not None:
        normalized["api_key"] = api_key

    proxy = normalize_proxy_url(payload.get("proxy"))
    if proxy is not None:
        normalized["proxy"] = proxy

    timeout_seconds = parse_optional_positive_int(payload.get("timeout_seconds"))
    if timeout_seconds is not None:
        normalized["timeout_seconds"] = timeout_seconds

    max_retries = parse_optional_positive_int(payload.get("max_retries"))
    if max_retries is not None:
        normalized["max_retries"] = max_retries

    verify_ssl = parse_optional_bool(payload.get("verify_ssl"))
    if verify_ssl is not None:
        normalized["verify_ssl"] = verify_ssl

    model_list = normalize_model_list(payload.get("model_list"))
    if model_list:
        normalized["model_list"] = model_list

    hook = clean_optional_string(payload.get("hook"))
    if hook is not None:
        normalized["hook"] = hook

    return normalized


def normalize_runtime_provider_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(config, Mapping):
        raise ValueError("Provider config must be an object")

    name = clean_optional_string(config.get("name"))
    api = clean_optional_string(config.get("api"))
    if name is None:
        raise ValueError("Provider name is required")
    if api is None:
        raise ValueError("Provider api is required")

    return {
        "name": name,
        "api": api,
        "api_key": clean_optional_string(config.get("api_key")) or "",
        "model_list": normalize_model_list(config.get("model_list")),
        "proxy": normalize_proxy_url(config.get("proxy")),
        "timeout_seconds": parse_optional_positive_int(
            config.get("timeout_seconds"),
            default=DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        ),
        "max_retries": parse_optional_positive_int(
            config.get("max_retries"),
            default=DEFAULT_PROVIDER_MAX_RETRIES,
        ),
        "verify_ssl": parse_optional_bool(
            config.get("verify_ssl"),
            default=DEFAULT_PROVIDER_VERIFY_SSL,
        ),
        "hook": clean_optional_string(config.get("hook")),
    }


def validate_provider_definitions(providers: Sequence[Mapping[str, Any]]) -> None:
    seen_names: set[str] = set()

    for index, provider in enumerate(providers):
        if not isinstance(provider, Mapping):
            raise ValueError(f"Provider entry at index {index} must be an object")

        normalized = normalize_runtime_provider_config(provider)
        name = normalized["name"]
        if name in seen_names:
            raise ValueError(f"Duplicate provider name detected: {name}")
        seen_names.add(name)
