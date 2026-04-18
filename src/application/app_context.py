#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用装配过程中共享的运行上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..utils.compat import Protocol

if TYPE_CHECKING:
    from flask import Flask


class Logger(Protocol):
    def info(self, msg: str, *args: object) -> None: ...

    def error(self, msg: str, *args: object) -> None: ...

    def warning(self, msg: str, *args: object) -> None: ...

    def debug(self, msg: str, *args: object) -> None: ...


@dataclass(frozen=True)
class AppContext:
    """跨层传递的最小依赖集合。"""

    logger: Logger
    config_manager: Any
    root_path: Path
    flask_app: "Flask"
