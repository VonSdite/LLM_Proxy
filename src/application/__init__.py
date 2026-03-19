#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用层导出。"""

from .app_context import AppContext, Logger

__all__ = ["Application", "AppContext", "Logger"]


def __getattr__(name: str):
    if name == "Application":
        from .application import Application

        return Application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
