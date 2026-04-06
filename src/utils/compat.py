#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容不同 Python 运行时的辅助工具。"""

from __future__ import annotations

import sys
from dataclasses import dataclass as _dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, TypeVar, overload

if TYPE_CHECKING:
    from enum import StrEnum
    from typing import Literal, Protocol
    from typing import dataclass_transform as _dataclass_transform
else:
    try:
        from typing import Literal, Protocol
    except ImportError:
        from typing_extensions import Literal, Protocol  # type: ignore

    try:
        from typing import dataclass_transform as _dataclass_transform
    except ImportError:
        try:
            from typing_extensions import dataclass_transform as _dataclass_transform  # type: ignore
        except ImportError:
            def _dataclass_transform(*_args: Any, **_kwargs: Any) -> Callable[[Any], Any]:
                def decorator(func: Any) -> Any:
                    return func

                return decorator

    try:
        from enum import StrEnum
    except ImportError:
        class StrEnum(str, Enum):
            """Python 3.11 之前的 StrEnum 兼容实现。"""

            pass


T = TypeVar("T")


@_dataclass_transform()
@overload
def dataclass(_cls: type[T], **kwargs: Any) -> type[T]: ...


@overload
def dataclass(_cls: None = None, **kwargs: Any) -> Callable[[type[T]], type[T]]: ...


def dataclass(_cls: type[T] | None = None, **kwargs: Any) -> Any:
    """兼容旧版本参数的 dataclass 包装器。"""

    if sys.version_info < (3, 10):
        kwargs.pop("slots", None)
    if _cls is None:
        return _dataclass(**kwargs)
    return _dataclass(_cls, **kwargs)


__all__ = ["dataclass", "Literal", "Protocol", "StrEnum"]
