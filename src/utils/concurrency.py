"""实例内复合状态操作的协作锁。"""

from collections.abc import Callable
from functools import wraps
from operator import attrgetter
from typing import Any


def synchronized(lock_name: str) -> Callable:
    """在指定实例锁中执行完整方法，支持属性路径和同一协程重入。"""
    get_lock = attrgetter(lock_name)

    def decorate(function: Callable) -> Callable:
        @wraps(function)
        def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            with get_lock(self):
                return function(self, *args, **kwargs)

        return wrapped

    return decorate
