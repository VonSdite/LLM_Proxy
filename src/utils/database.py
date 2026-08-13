#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库工具函数。"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from sys import platform

if platform == "linux":
    import pysqlite3 as sqlite3
else:
    import sqlite3

SQLiteConnection = sqlite3.Connection
SQLiteCursor = sqlite3.Cursor
SQLiteRow = sqlite3.Row

ConnectionFactory = Callable[[], AbstractContextManager[SQLiteConnection]]


def create_connection_factory(db_path: Path) -> ConnectionFactory:
    """创建 SQLite 连接上下文工厂。"""
    resolved_db_path = db_path.resolve()
    resolved_db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection_context() -> Iterator[SQLiteConnection]:
        conn = sqlite3.connect(str(resolved_db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return connection_context
