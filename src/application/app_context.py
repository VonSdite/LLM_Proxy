#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""应用装配过程中共享的运行上下文。"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..utils.compat import Protocol

if TYPE_CHECKING:
    from ..config import ConfigManager
    from flask import Flask


class Logger(Protocol):
    def info(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None: ...

    def error(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None: ...

    def warning(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None: ...

    def debug(
        self,
        msg: object,
        *args: object,
        exc_info: object = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: object | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class AppContext:
    """跨层传递的最小依赖集合。"""

    logger: Logger
    config_manager: "ConfigManager"
    root_path: Path
    flask_app: "Flask"
