#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容不同 Python 运行时的辅助工具。"""

from __future__ import annotations

import sys
from enum import Enum

try:
    from typing import Literal, Protocol
except ImportError:
    from typing_extensions import Literal, Protocol  # type: ignore

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec  # type: ignore

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    class StrEnum(str, Enum):
        """Python 3.11 之前的 StrEnum 兼容实现。"""

        pass

__all__ = ["ParamSpec", "Literal", "Protocol", "StrEnum"]
