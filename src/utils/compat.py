#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容不同 Python 运行时的辅助工具。"""

from __future__ import annotations

import sys
from dataclasses import dataclass as _dataclass
from enum import Enum
from typing import Any, Callable, TypeVar, overload

try:
    from typing import Literal, Protocol
except ImportError:
    from typing_extensions import Literal, Protocol  # type: ignore

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec  # type: ignore

if sys.version_info >= (3, 11):
    from typing import dataclass_transform as _dataclass_transform
else:
    try:
        from typing_extensions import dataclass_transform as _dataclass_transform  # type: ignore
    except ImportError:
        def _dataclass_transform(*_args: Any, **_kwargs: Any) -> Callable[[Any], Any]:
            def decorator(func: Any) -> Any:
                return func

            return decorator

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    class StrEnum(str, Enum):
        """Python 3.11 之前的 StrEnum 兼容实现。"""

        pass


T = TypeVar("T")


@overload
def dataclass(_cls: type[T], **kwargs: Any) -> type[T]: ...


@overload
def dataclass(_cls: None = None, **kwargs: Any) -> Callable[[type[T]], type[T]]: ...


@_dataclass_transform()
def dataclass(_cls: type[T] | None = None, **kwargs: Any) -> Any:
    """兼容旧版本参数的 dataclass 包装器。"""

    if sys.version_info < (3, 10):
        kwargs.pop("slots", None)
    if _cls is None:
        return _dataclass(**kwargs)
    return _dataclass(_cls, **kwargs)


__all__ = ["ParamSpec", "dataclass", "Literal", "Protocol", "StrEnum"]
