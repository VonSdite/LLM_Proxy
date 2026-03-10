#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hook type contracts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Protocol

from ..application.app_context import Logger


class HookAbortError(Exception):
    """Exception raised by hooks to stop proxy request with custom status."""

    def __init__(self, message: str, status_code: int = 400, error_type: str = "hook_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type


@dataclass(frozen=True)
class HookContext:
    """Context passed into hooks."""

    retry: int
    root_path: Path
    logger: Logger


class HookModule(Protocol):
    def header_hook(self, ctx: HookContext, headers: Dict[str, str]) -> Dict[str, str]:
        ...

    def input_body_hook(self, ctx: HookContext, body: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def output_body_hook(self, ctx: HookContext, body: Any) -> Any:
        ...


class BaseHook:
    """Base hook implementation. Override only the methods you need."""

    def header_hook(self, ctx: HookContext, headers: Dict[str, str]) -> Dict[str, str]:
        return headers

    def input_body_hook(self, ctx: HookContext, body: Dict[str, Any]) -> Dict[str, Any]:
        return body

    def output_body_hook(self, ctx: HookContext, body: Any) -> Any:
        return body
