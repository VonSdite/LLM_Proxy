"""监听热切换的真实连接、配置回滚与访问入口验证。"""

import json
import logging
import tempfile
import unittest
from http.client import HTTPResponse
from pathlib import Path
from unittest.mock import Mock, patch

import gevent
import yaml
from flask import Flask, Response
from gevent import socket
from gevent.event import Event
from src.application.app_context import AppContext
from src.application.listener_runtime import ListenerRuntime, ManagedWSGIServer
from src.config.config_manager import ConfigManager
from src.presentation.web_controller import WebController
from src.services.authentication_service import AuthenticationService
from src.services.settings_service import SettingsService


class ListenerRuntimeTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "tmp" / "listener-tests"
        root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.yaml"
        self.path.write_text(
            yaml.safe_dump({"admin": {"username": "test", "password": "test"}}), encoding="utf-8", newline="\n"
        )
        self.app = Flask(__name__)
        self.logger = logging.getLogger("listener-test")
        self.runtime = ListenerRuntime(self.app, self.logger)
        self.runtime.start("127.0.0.1", 0)
        self.addCleanup(self.runtime.close)
        self.port = self.runtime._server.address[1]
        self.config = ConfigManager(self.path, Path(self.directory.name))
        config = self.config.get_raw_config()
        config["server"] = {"host": "127.0.0.1", "port": self.port}
        self.config.write_raw_config(config)
        ctx = AppContext(self.logger, self.config, Path(self.directory.name), self.app)
        self.service = SettingsService(ctx, prepare_listener_callback=self.runtime.prepare)
        self.auth = AuthenticationService(ctx)
        self.token = self.auth.create_session("test")
        WebController(ctx, Mock(), self.service, self.auth)
        self.release_stream = Event()

        @self.app.route("/stream")
        def stream():
            def content():
                yield b"first"
                self.release_stream.wait(timeout=10)
                yield b"last"

            return Response(content(), mimetype="text/plain")

    def unused_port(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def open_request(self, port, path="/api/settings/system", body=None, keep_alive=False):
        connection = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.addCleanup(connection.close)
        data = json.dumps(body).encode() if body is not None else b""
        method = "PUT" if body is not None else "GET"
        headers = (
            f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Cookie: session_token={self.token}\r\n"
            f"Connection: {'keep-alive' if keep_alive else 'close'}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n"
        )
        connection.sendall(headers.encode() + data)
        response = HTTPResponse(connection)
        response.begin()
        return connection, response

    def request(self, port, body=None, path=None):
        path = path or ("/api/settings/system/basic" if body is not None else "/api/settings/system")
        _, response = self.open_request(port, path, body)
        return response.status, json.loads(response.read())

    def test_switch_keeps_stream_and_session_alive_and_closes_old_listener(self):
        waiter = gevent.spawn(self.runtime.wait)
        connection, stream = self.open_request(self.port, "/stream", keep_alive=True)
        self.assertEqual(stream.read(5), b"first")
        new_port = self.unused_port()
        status, result = self.request(self.port, {"server": {"host": "127.0.0.1", "port": new_port}})
        self.assertEqual(status, 200)
        self.assertTrue(result["server_listener_changed"])
        self.assertFalse(result["server_restart_required"])
        self.assertEqual(result["server_access_url"], f"http://127.0.0.1:{new_port}/settings")
        self.assertEqual(self.request(new_port)[0], 200)
        self.assertTrue(self.auth.validate_session(self.token))
        self.assertFalse(waiter.ready())
        self.assertEqual(yaml.safe_load(self.path.read_text(encoding="utf-8"))["server"]["port"], new_port)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.port), timeout=0.2)
        self.release_stream.set()
        self.assertEqual(stream.read(), b"last")
        self.assertEqual(connection.recv(1), b"")
        # 第二次切换仍由同一运行时承载，旧监听的停止不退出主等待。
        another_port = self.unused_port()
        self.assertEqual(self.request(new_port, {"server": {"host": "127.0.0.1", "port": another_port}})[0], 200)
        self.assertEqual(self.request(another_port)[0], 200)
        self.runtime.close()
        waiter.join(timeout=1)
        self.assertTrue(waiter.ready())

    def test_occupied_port_preserves_listener_file_and_session(self):
        before = self.path.read_bytes()
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            status, result = self.request(self.port, {"server": {"host": "127.0.0.1", "port": port}})
        self.assertEqual(status, 400)
        self.assertIn("Cannot bind new listener", result["error"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.config.get_server_port(), self.port)
        self.assertEqual(self.request(self.port)[0], 200)

    def test_write_failure_releases_candidate_and_preserves_old_listener(self):
        before = self.path.read_bytes()
        port = self.unused_port()
        with patch.object(self.config, "write_raw_config", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(OSError, "write failed"):
                self.service.update_basic_settings({"server": {"host": "127.0.0.1", "port": port}})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.request(self.port)[0], 200)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))

    def test_activation_failure_rolls_back_persisted_config(self):
        before = self.config.get_raw_config()
        port = self.unused_port()
        with patch.object(ManagedWSGIServer, "start", side_effect=RuntimeError("activation failed")):
            with self.assertRaisesRegex(RuntimeError, "activation failed"):
                self.service.update_basic_settings({"server": {"host": "127.0.0.1", "port": port}})
        self.assertEqual(self.config.get_raw_config(), before)
        self.assertEqual(yaml.safe_load(self.path.read_text(encoding="utf-8")), before)
        self.assertEqual(self.request(self.port)[0], 200)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))

    def test_unchanged_and_admin_only_do_not_create_listeners(self):
        with patch.object(self.runtime, "_new_server") as factory:
            result = self.service.update_basic_settings({"server": {"host": "127.0.0.1", "port": self.port}})
            self.assertFalse(result["server_listener_changed"])
            self.service.update_basic_settings({"admin": {"username": "test", "password": "new"}})
            factory.assert_not_called()

    def test_legacy_combined_endpoint_also_switches_listener(self):
        payload = self.service.get_system_settings()
        payload["server"]["port"] = self.unused_port()
        status, result = self.request(self.port, payload, path="/api/settings/system")
        self.assertEqual(status, 200)
        self.assertTrue(result["server_listener_changed"])
        self.assertEqual(self.request(payload["server"]["port"])[0], 200)

    def test_external_wsgi_host_reports_restart_required(self):
        self.runtime.close()
        result = self.service.update_basic_settings({"server": {"host": "127.0.0.1", "port": self.unused_port()}})
        self.assertTrue(result["server_restart_required"])
        self.assertFalse(result["server_listener_changed"])


class ListenerAccessUrlTests(unittest.TestCase):
    def test_direct_proxy_ipv6_and_loopback_destinations(self):
        app = Flask(__name__)
        cases = [
            ("http://192.168.1.10:8080", {}, "8080", "0.0.0.0", "http://192.168.1.10:9000/settings"),
            ("http://localhost:8080", {}, "8080", "127.0.0.1", "http://127.0.0.1:9000/settings"),
            ("http://[::1]:8080", {}, "8080", "::", "http://[::1]:9000/settings"),
            ("http://127.0.0.1:8080", {}, "8080", "::", None),
            ("http://[::1]:8080", {}, "8080", "0.0.0.0", None),
            ("http://192.168.1.10:8080", {}, "8080", "127.0.0.1", None),
            ("https://proxy.example", {}, "8080", "0.0.0.0", None),
            ("http://proxy.example:8080", {}, "8080", "0.0.0.0", None),
            ("http://192.168.1.10:8080", {"X-Forwarded-For": "192.168.1.20"}, "8080", "0.0.0.0", None),
            ("http://192.168.1.10:8080", {}, "80", "0.0.0.0", None),
        ]
        for origin, headers, port, host, expected in cases:
            with self.subTest(origin=origin, headers=headers, host=host, port=port):
                with app.test_request_context(
                    base_url=origin, headers=headers, environ_overrides={"SERVER_PORT": port}
                ):
                    self.assertEqual(WebController._listener_access_url({"host": host, "port": 9000}), expected)


if __name__ == "__main__":
    unittest.main()
