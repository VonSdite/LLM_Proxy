#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库工具函数。"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, ContextManager, Iterator


ConnectionFactory = Callable[[], ContextManager[sqlite3.Connection]]


def create_connection_factory(db_path: Path) -> ConnectionFactory:
    """创建 SQLite 连接上下文工厂。"""
    resolved_db_path = db_path.resolve()
    resolved_db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection_context() -> Iterator[sqlite3.Connection]:
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
