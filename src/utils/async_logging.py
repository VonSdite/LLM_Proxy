"""使用有界队列和独立原生线程写入运行日志。"""

import atexit
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from sys import getsizeof

from gevent.monkey import get_original
from gevent.threadpool import ThreadPool

from .blocking_io import run_blocking


class RotatingLogHandler(RotatingFileHandler):
    """滚动日志文件统一使用 LF 换行。"""

    def _open(self):
        return self._builtin_open(
            self.baseFilename,
            self.mode,
            encoding=self.encoding,
            errors=self.errors,
            newline="\n",
        )


class LogQueue:
    """限制待写日志的数量和消息内存，队列满时累计丢弃计数。"""

    def __init__(self, max_records: int = 10000, max_bytes: int = 16 * 1024 * 1024):
        self._queue = get_original("queue", "SimpleQueue")()
        self._lock = get_original("_thread", "allocate_lock")()
        self._max_records = max_records
        self._max_bytes = max_bytes
        self._records = 0
        self._bytes = 0
        self._dropped = 0
        self._closed = False

    def put_nowait(self, record: logging.LogRecord) -> None:
        size = getsizeof(record.msg) + getsizeof(record.__dict__)
        with self._lock:
            if self._closed or self._records >= self._max_records or self._bytes + size > self._max_bytes:
                self._dropped += 1
                return
            self._records += 1
            self._bytes += size
            self._queue.put((record, size))

    def get(self, block: bool = True) -> logging.LogRecord | None:
        record, size = self._queue.get(block)
        with self._lock:
            self._records -= 1
            self._bytes -= size
        return record

    def take_dropped(self) -> int:
        with self._lock:
            dropped, self._dropped = self._dropped, 0
            return dropped

    def close(self) -> None:
        """关闭入队入口，并在已有记录之后追加退出标记。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._records += 1
            self._queue.put((None, 0))


class AsyncLogHandler(QueueHandler):
    """将格式化后的日志放入原生线程队列。"""

    def createLock(self) -> None:
        self.lock = get_original("_thread", "RLock")()


class NativeLogListener(QueueListener):
    """在专用原生线程中执行文件、轮转和控制台输出。"""

    def __init__(self, handlers: dict[str, list[logging.Handler]], queue: LogQueue | None = None):
        sinks = []
        for name, group in handlers.items():
            for handler in group:
                handler.lock = get_original("_thread", "RLock")()
                handler.addFilter(logging.Filter(name))
                sinks.append(handler)
        super().__init__(queue or LogQueue(), *sinks, respect_handler_level=True)
        self._pool = ThreadPool(1)
        self._closed = False

    def start(self) -> None:
        """专用线程持续消费原生队列，独立于 gevent 的通用工作线程池。"""
        if self._thread is not None:
            return
        self._thread = self._pool.spawn(self._monitor)
        atexit.register(self.close)

    def dequeue(self, block: bool) -> logging.LogRecord | None:
        record = self.queue.get(block)
        dropped = self.queue.take_dropped()
        if dropped:
            self.handle(
                logging.LogRecord(
                    "app",
                    logging.WARNING,
                    __file__,
                    0,
                    "Log queue dropped %s records after reaching its capacity",
                    (dropped,),
                    None,
                )
            )
        return record

    def stop(self) -> None:
        """协作等待已入队日志写完。"""
        if self._thread is None:
            return
        self.queue.close()
        self._thread.get()
        self._thread = None
        self._pool.kill()

    def close(self) -> None:
        """排空队列并释放文件句柄。"""
        if self._closed:
            return
        self.stop()
        for handler in self.handlers:
            run_blocking(handler.close)
        self._closed = True
        atexit.unregister(self.close)
