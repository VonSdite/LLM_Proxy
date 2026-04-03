#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility helpers for supported Python runtimes."""

import sys
from dataclasses import dataclass as _dataclass
from enum import Enum
from typing import Any

try:
    from enum import StrEnum
except ImportError:
    class StrEnum(str, Enum):
        """Fallback StrEnum for Python versions before 3.11."""

        pass

try:
    from typing import Literal
except ImportError:
    try:
        from typing_extensions import Literal  # type: ignore
    except ImportError:
        class _LiteralCompat(object):
            def __getitem__(self, _item):
                return Any

        Literal = _LiteralCompat()

try:
    from typing import Protocol
except ImportError:
    try:
        from typing_extensions import Protocol  # type: ignore
    except ImportError:
        class Protocol(object):
            """Fallback Protocol base when typing_extensions is unavailable."""

            pass


def dataclass(_cls=None, **kwargs):
    """Mirror dataclasses.dataclass while skipping unsupported keywords."""

    if sys.version_info < (3, 10):
        kwargs.pop("slots", None)
    if _cls is None:
        return _dataclass(**kwargs)
    return _dataclass(_cls, **kwargs)


__all__ = ["dataclass", "Literal", "Protocol", "StrEnum"]
