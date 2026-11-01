#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库工具函数。"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from functools import wraps
from pathlib import Path
from sys import platform
from typing import Any

from gevent.lock import RLock

from .blocking_io import in_io_worker, run_blocking

if platform == "linux":
    import pysqlite3 as sqlite3
else:
    import sqlite3

SQLiteConnection = sqlite3.Connection
SQLiteCursor = sqlite3.Cursor
SQLiteRow = sqlite3.Row

ConnectionFactory = Callable[[], AbstractContextManager[SQLiteConnection]]


class SQLiteConnectionFactory:
    """管理 WAL 数据库连接，并在协程中串行提交写入任务。"""

    def __init__(self, db_path: Path):
        self._db_path = db_path.resolve()
        self._write_lock = RLock()
        run_blocking(self._initialize)

    def _initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() != "wal":
                mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if str(mode).lower() != "wal":
                    raise RuntimeError("SQLite WAL mode is unavailable")

    @contextmanager
    def __call__(self) -> Iterator[SQLiteConnection]:
        """在调用线程中创建、提交或回滚并关闭连接。"""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def run(self, function: Callable, *args: Any, write: bool = False, **kwargs: Any) -> Any:
        """在原生线程内执行完整仓储操作，写操作在提交前持有协程锁。"""
        if in_io_worker():
            return function(*args, **kwargs)
        with self._write_lock if write else nullcontext():
            return run_blocking(function, *args, **kwargs)


def create_connection_factory(db_path: Path) -> ConnectionFactory:
    """创建启用 WAL 的 SQLite 连接上下文工厂。"""
    return SQLiteConnectionFactory(db_path)


def database_operation(*, write: bool = False) -> Callable:
    """让仓储方法在同一原生线程内完成连接、SQL、结果读取和事务收尾。"""

    def decorate(function: Callable) -> Callable:
        @wraps(function)
        def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            factory = self._get_connection
            if isinstance(factory, SQLiteConnectionFactory):
                return factory.run(function, self, *args, write=write, **kwargs)
            return run_blocking(function, self, *args, **kwargs)

        return wrapped

    return decorate
