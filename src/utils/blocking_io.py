"""通过原生线程执行阻塞操作，调用方协作等待结果。"""

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar, cast

from gevent import get_hub
from gevent.monkey import get_original

P = ParamSpec("P")
T = TypeVar("T")
_worker_state = get_original("_thread", "_local")()


def in_io_worker() -> bool:
    """判断当前原生线程是否正在执行阻塞任务。"""
    return bool(getattr(_worker_state, "active", False))


def _invoke(function: Callable[P, T], args: tuple, kwargs: dict) -> tuple[bool, T | BaseException]:
    _worker_state.active = True
    try:
        try:
            return True, function(*args, **kwargs)
        except BaseException as exc:
            return False, exc
    finally:
        _worker_state.active = False


def run_blocking(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """执行完整阻塞任务；取消调用时等待任务完成后再释放调用方资源。"""
    if in_io_worker():
        return function(*args, **kwargs)
    task = get_hub().threadpool.spawn(_invoke, function, args, kwargs)
    try:
        succeeded, result = task.get()
    except BaseException:
        task.wait()
        raise
    if not succeeded:
        raise cast(BaseException, result)
    return cast(T, result)


def blocking_io(function: Callable[P, T]) -> Callable[P, T]:
    """将同步函数的完整执行过程交给原生线程。"""

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        return run_blocking(function, *args, **kwargs)

    return wrapped
