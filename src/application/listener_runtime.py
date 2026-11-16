"""管理进程内的 HTTP 监听切换与连接排空。"""

from collections.abc import Iterator
from contextlib import contextmanager

from gevent import socket
from gevent.event import Event
from gevent.lock import RLock
from gevent.pool import Pool
from gevent.pywsgi import WSGIHandler, WSGIServer


class DrainingWSGIHandler(WSGIHandler):
    """旧监听的连接在当前响应完成后结束 keep-alive。"""

    def handle_one_response(self) -> None:
        try:
            super().handle_one_response()
        finally:
            if self.server.draining:
                self.close_connection = True


class ManagedWSGIServer(WSGIServer):
    draining = False
    handler_class = DrainingWSGIHandler

    @classmethod
    def get_listener(cls, address, backlog=None, family=None):
        """显式释放绑定失败的 socket，避免反复保存时积累句柄。"""
        listener = socket.socket(family=family or socket.AF_INET)
        try:
            if cls.reuse_addr is not None:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, cls.reuse_addr)
            listener.bind(address)
            listener.listen(backlog if backlog is not None else cls.backlog)
            listener.setblocking(False)
            return listener
        except BaseException:
            listener.close()
            raise


class ListenerRuntime:
    """监听生命周期独立于单个 WSGIServer，切换不结束应用进程。"""

    def __init__(self, application, logger):
        self._application = application
        self._logger = logger
        self._server: ManagedWSGIServer | None = None
        self._handlers = Pool()
        self._stopped = Event()
        self._lock = RLock()

    def _new_server(self, host: str, port: int) -> ManagedWSGIServer:
        return ManagedWSGIServer((host, port), self._application, spawn=self._handlers)

    def start(self, host: str, port: int) -> None:
        with self._lock:
            server = self._new_server(host, port)
            try:
                server.start()
            except BaseException:
                server.close()
                raise
            self._server = server
            self._stopped.clear()

    def wait(self) -> None:
        self._stopped.wait()

    @contextmanager
    def prepare(self, host: str, port: int) -> Iterator[bool]:
        """先绑定候选监听，配置提交成功后启用并排空旧监听。"""
        with self._lock:
            previous = self._server
            if previous is None:
                # 外部 WSGI 宿主负责监听时，仅保存配置并提示重启。
                yield False
                return
            if previous.address[:2] == (host, port):
                yield True
                return
            candidate = self._new_server(host, port)
            try:
                try:
                    candidate.init_socket()
                except OSError as exc:
                    self._logger.warning("Cannot bind listener %s:%s (errno=%s)", host, port, exc.errno)
                    raise ValueError("Cannot bind new listener; current listener is unchanged") from exc
                yield True
                candidate.start()
            except BaseException:
                candidate.close()
                raise
            self._server = candidate
            previous.draining = True
            # close 只停止监听，不调用 stop 杀死仍在输出的请求协程。
            previous.close()
            self._logger.info("HTTP listener switched to %s:%s", host, port)

    def close(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.close()
                self._server = None
            self._stopped.set()
        self._handlers.kill()
