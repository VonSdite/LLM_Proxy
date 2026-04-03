#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hook type contracts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..application.app_context import Logger
from ..utils.compat import Protocol, StrEnum


class HookAbortError(Exception):
    """Exception raised by hooks to stop proxy request with custom status."""

    def __init__(self, message: str, status_code: int = 400, error_type: str = "hook_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type


class HookErrorType(StrEnum):
    """Normalized transport-level failures exposed to hooks."""

    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    WEBSOCKET_ERROR = "websocket_error"
    TRANSPORT_ERROR = "transport_error"


@dataclass(frozen=True)
class HookContext:
    """Context passed into hooks."""

    retry: int
    root_path: Path
    logger: Logger
    provider_name: str = ""
    request_model: str = ""
    upstream_model: str = ""
    provider_source_format: str = "openai_chat"
    provider_target_format: str = "openai_chat"
    transport: str = "http"
    stream: bool = False
    auth_group_name: Optional[str] = None
    auth_entry_id: Optional[str] = None
    last_status_code: Optional[int] = None
    last_error_type: Optional[HookErrorType] = None


class HookModule(Protocol):
    def header_hook(self, ctx: HookContext, headers: Dict[str, str]) -> Dict[str, str]:
        ...

    def request_guard(self, ctx: HookContext, body: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def response_guard(self, ctx: HookContext, body: Any) -> Any:
        ...


class BaseHook:
    """Base hook implementation. Override only the methods you need."""

    def header_hook(self, ctx: HookContext, headers: Dict[str, str]) -> Dict[str, str]:
        return headers

    def request_guard(self, ctx: HookContext, body: Dict[str, Any]) -> Dict[str, Any]:
        return body

    def response_guard(self, ctx: HookContext, body: Any) -> Any:
        return body
