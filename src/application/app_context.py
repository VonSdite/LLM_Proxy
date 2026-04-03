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
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...

    def warning(self, msg: str) -> None: ...

    def debug(self, msg: str) -> None: ...


@dataclass(frozen=True)
class AppContext:
    """跨层传递的最小依赖集合。"""

    logger: Logger
    config_manager: "ConfigManager"
    root_path: Path
    flask_app: "Flask"
