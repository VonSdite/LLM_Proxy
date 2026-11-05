"""通过临时 SSH/SFTP 与 CONNECT/SOCKS5 服务验证真实网络链路。"""

from __future__ import annotations

import base64
import errno
import hashlib
import logging
import os
import posixpath
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import paramiko
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.external.agent_config_transport import (
    AgentConfigError,
    connect_ssh,
    parse_proxy,
    proxy_socket,
    public_proxy_url,
)


class DiskSftp(paramiko.SFTPServerInterface):
    """测试目录映射到与用户名不同的远程主目录。"""

    def __init__(self, server, *, root):
        super().__init__(server)
        self.root = Path(root)

    def canonicalize(self, path):
        return posixpath.normpath(path if path.startswith("/") else "/srv/custom-home/" + path)

    def path(self, path):
        resolved = (self.root / self.canonicalize(path).lstrip("/")).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise OSError(errno.EACCES, "Outside test directory")
        return resolved

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(self.path(path).stat())
        except OSError as error:
            return paramiko.SFTPServer.convert_errno(error.errno)

    lstat = stat

    def open(self, path, flags, attr):
        try:
            descriptor = os.open(self.path(path), flags | getattr(os, "O_BINARY", 0), 0o600)
            mode = "r+b" if flags & os.O_RDWR else "wb" if flags & os.O_WRONLY else "rb"
            stream = os.fdopen(descriptor, mode)
            handle = paramiko.SFTPHandle(flags)
            handle.readfile = stream
            handle.writefile = stream
            return handle
        except OSError as error:
            return paramiko.SFTPServer.convert_errno(error.errno)

    def mkdir(self, path, attr):
        try:
            self.path(path).mkdir(mode=attr.st_mode or 0o700)
            return paramiko.SFTP_OK
        except OSError as error:
            return paramiko.SFTPServer.convert_errno(error.errno)

    def chattr(self, path, attr):
        try:
            paramiko.SFTPServer.set_file_attr(str(self.path(path)), attr)
            return paramiko.SFTP_OK
        except OSError as error:
            return paramiko.SFTPServer.convert_errno(error.errno)

    def posix_rename(self, old, new):
        try:
            os.replace(self.path(old), self.path(new))
            return paramiko.SFTP_OK
        except OSError as error:
            return paramiko.SFTPServer.convert_errno(error.errno)

    def remove(self, path):
        try:
            self.path(path).unlink()
            return paramiko.SFTP_OK
        except OSError as error:
            return paramiko.SFTPServer.convert_errno(error.errno)


class PasswordServer(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("agent", "test-password") else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, channel_id):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class SshServer:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "srv/custom-home").mkdir(parents=True)
        self.key = paramiko.RSAKey.generate(2048)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.stopped = threading.Event()
        self.transports = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    @property
    def fingerprint(self):
        return "SHA256:" + base64.b64encode(hashlib.sha256(self.key.asbytes()).digest()).decode().rstrip("=")

    def serve(self):
        while not self.stopped.is_set():
            try:
                stream, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            transport = paramiko.Transport(stream)
            self.transports.append(transport)
            transport.add_server_key(self.key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, DiskSftp, root=self.root)
            try:
                transport.start_server(server=PasswordServer())
            except (OSError, EOFError, paramiko.SSHException):
                transport.close()

    def machine(self, proxy=None, *, pinned=True):
        machine = {
            "host": "localhost",
            "port": str(self.port),
            "user": "agent",
            "proxy": proxy or {"mode": "direct", "url": ""},
        }
        if pinned:
            machine["fingerprint"] = self.fingerprint
        return machine

    def close(self):
        self.stopped.set()
        self.listener.close()
        for transport in self.transports:
            transport.close()
        self.thread.join(timeout=2)


def read_exact(stream, size):
    data = b""
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


class TunnelProxy:
    """支持带认证的 HTTP CONNECT 或 SOCKS5 测试隧道。"""

    def __init__(self, scheme, tls_context=None):
        self.scheme = scheme
        self.tls_context = tls_context
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.requests = []
        self.stopped = threading.Event()
        self.sockets = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"{self.scheme}://user:p%40ss@127.0.0.1:{self.port}"

    def serve(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.sockets.append(client)
            if self.tls_context:
                try:
                    client = self.tls_context.wrap_socket(client, server_side=True)
                    self.sockets.append(client)
                except OSError:
                    client.close()
                    continue
            threading.Thread(target=self.tunnel, args=(client,), daemon=True).start()

    def tunnel(self, client):
        upstream = None
        try:
            client.settimeout(5)
            if self.scheme in ("http", "https"):
                request = b""
                while not request.endswith(b"\r\n\r\n"):
                    request += read_exact(client, 1)
                self.requests.append(request.decode("ascii"))
                host, port = request.split()[1].decode().rsplit(":", 1)
                upstream = socket.create_connection(
                    ("127.0.0.1" if host == "localhost" else host, int(port)), timeout=5
                )
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                version, methods = read_exact(client, 2)
                read_exact(client, methods)
                client.sendall(b"\x05\x02")
                auth_version, length = read_exact(client, 2)
                username = read_exact(client, length)
                password = read_exact(client, read_exact(client, 1)[0])
                self.requests.append((username, password))
                client.sendall(b"\x01\x00")
                version, command, reserved, address_type = read_exact(client, 4)
                if address_type == 3:
                    host = read_exact(client, read_exact(client, 1)[0]).decode()
                else:
                    host = socket.inet_ntoa(read_exact(client, 4))
                self.requests.append((address_type, host))
                port = int.from_bytes(read_exact(client, 2), "big")
                upstream = socket.create_connection(("127.0.0.1" if host == "localhost" else host, port), timeout=5)
                client.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
            self.sockets.append(upstream)
            client.settimeout(None)
            upstream.settimeout(None)
            threading.Thread(target=self.pump, args=(client, upstream), daemon=True).start()
            self.pump(upstream, client)
        except (OSError, EOFError):
            pass
        finally:
            client.close()
            if upstream:
                upstream.close()

    def pump(self, source, destination):
        try:
            while not self.stopped.is_set():
                data = source.recv(65536)
                if not data:
                    return
                destination.sendall(data)
        except OSError:
            pass
        finally:
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def close(self):
        self.stopped.set()
        self.listener.close()
        for stream in self.sockets:
            stream.close()
        self.thread.join(timeout=2)


class AgentConfigTransportTests(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger("paramiko.transport")
        previous_level = logger.level
        logger.setLevel(logging.CRITICAL)
        self.addCleanup(logger.setLevel, previous_level)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.server = SshServer(temporary.name)
        self.addCleanup(self.server.close)

    def connect(self, proxy=None):
        machine = self.server.machine({"mode": "custom", "url": proxy.url} if proxy else None)
        if proxy and proxy.scheme == "socks5":
            machine["host"] = "127.0.0.1"
        files, fingerprint = connect_ssh(machine, "test-password", proxy.url if proxy else "")
        self.addCleanup(files.close)
        self.assertEqual(self.server.fingerprint, fingerprint)
        return files

    def test_real_sftp_home_missing_read_atomic_replace_and_cleanup(self):
        files = self.connect()
        self.assertEqual("/srv/custom-home", files.home)
        path = files.resolve("~/.claude/settings.json")
        self.assertEqual("/srv/custom-home/.claude/settings.json", path)
        self.assertIsNone(files.read(path))
        files.write(path, b'{"first": true}\n')
        files.write(path, b'{"second": true}\n')
        self.assertEqual(b'{"second": true}\n', files.read(path))
        files.remove(path)
        self.assertIsNone(files.read(path))
        self.assertEqual([], list((self.server.root / "srv/custom-home/.claude").iterdir()))

    def test_first_connection_confirmation_and_changed_fingerprint(self):
        with self.assertRaises(AgentConfigError) as error:
            connect_ssh(self.server.machine(pinned=False), "test-password")
        self.assertEqual("host_key_required", error.exception.code)
        self.assertEqual(self.server.fingerprint, error.exception.details["fingerprint"])
        files, _ = connect_ssh(
            self.server.machine(pinned=False), "test-password", trust_fingerprint=self.server.fingerprint
        )
        files.close()
        machine = self.server.machine()
        machine["fingerprint"] = "SHA256:changed"
        with self.assertRaises(AgentConfigError) as error:
            connect_ssh(machine, "test-password")
        self.assertEqual("host_key_changed", error.exception.code)

    def test_http_connect_and_basic_proxy_auth(self):
        proxy = TunnelProxy("http")
        self.addCleanup(proxy.close)
        files = self.connect(proxy)
        files.write(files.resolve("~/test.json"), b"{}\n")
        self.assertIn(f"CONNECT localhost:{self.server.port}", proxy.requests[0])
        self.assertIn("Proxy-Authorization: Basic " + base64.b64encode(b"user:p@ss").decode(), proxy.requests[0])

    def test_socks5h_remote_dns_and_proxy_auth(self):
        proxy = TunnelProxy("socks5h")
        self.addCleanup(proxy.close)
        files = self.connect(proxy)
        self.assertTrue(files.alive())
        self.assertEqual((b"user", b"p@ss"), proxy.requests[0])
        self.assertEqual((3, "localhost"), proxy.requests[1])

    def test_https_proxy_verifies_certificate_and_carries_ssh(self):
        directory = self.server.root
        key = self.server.key.key
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        import ipaddress

        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False
            )
            .sign(key, hashes.SHA256())
        )
        cert_path, key_path = directory / "proxy.crt", directory / "proxy.key"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert_path, key_path)
        proxy = TunnelProxy("https", server_context)
        self.addCleanup(proxy.close)
        with self.assertRaises(ssl.SSLCertVerificationError):
            proxy_socket("localhost", self.server.port, "custom", proxy.url)
        trusted_context = ssl.create_default_context(cafile=str(cert_path))
        with patch("src.external.agent_config_transport.ssl.create_default_context", return_value=trusted_context):
            files = self.connect(proxy)
            path = files.resolve("~/https.json")
            files.write(path, b"{}\n")
            self.assertEqual(b"{}\n", files.read(path))

    def test_socks5_local_dns(self):
        proxy = TunnelProxy("socks5")
        self.addCleanup(proxy.close)
        self.assertTrue(self.connect(proxy).alive())
        self.assertEqual(1, proxy.requests[1][0])

    def test_authentication_failure(self):
        with self.assertRaises(paramiko.AuthenticationException):
            connect_ssh(self.server.machine(), "wrong-password")

    def test_system_proxy_selection_and_direct_bypass(self):
        proxy = TunnelProxy("http")
        self.addCleanup(proxy.close)
        with (
            patch("src.external.agent_config_transport.getproxies", return_value={"https": proxy.url}),
            patch("src.external.agent_config_transport.proxy_bypass", return_value=False),
        ):
            stream = proxy_socket("localhost", self.server.port, "system")
            self.assertTrue(stream.recv(50).startswith(b"SSH-2.0"))
            stream.close()
        with (
            patch("src.external.agent_config_transport.getproxies", return_value={"https": "http://unreachable:1"}),
            patch("src.external.agent_config_transport.proxy_bypass", return_value=True),
        ):
            stream = proxy_socket("localhost", self.server.port, "system")
            self.assertTrue(stream.recv(50).startswith(b"SSH-2.0"))
            stream.close()

    def test_proxy_validation_and_credential_free_storage(self):
        self.assertEqual("p@ss", parse_proxy("https://u:p%40ss@[::1]:443")["password"])
        self.assertEqual("https://[::1]:443", public_proxy_url("https://u:p%40ss@[::1]"))
        for url in (
            "ftp://localhost",
            "http://",
            "http://localhost:0",
            "http://localhost/path",
            "socks5://localhost?x=1",
            "http://localhost\r\nHeader:1",
        ):
            with self.subTest(url=url), self.assertRaises(AgentConfigError):
                parse_proxy(url)


class PatchedAgentConfigRuntimeTests(unittest.TestCase):
    def test_actual_application_keeps_ssh_session_across_requests(self):
        scenario = Path(__file__).with_name("agent_config_ssh_scenario.py")
        result = subprocess.run(
            [sys.executable, str(scenario)],
            cwd=scenario.parents[1],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
            timeout=45,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
