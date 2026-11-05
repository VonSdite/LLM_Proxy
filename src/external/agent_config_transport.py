"""Agent 配置的本机文件访问和 SSH/SFTP 连接。"""

from __future__ import annotations

import base64
import errno
import hashlib
import os
import posixpath
import socket
import ssl
import stat
import tempfile
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import getproxies, proxy_bypass

import paramiko
import socks

from ..utils.blocking_io import blocking_io

MAX_FILE_BYTES = 2 * 1024 * 1024
CONNECT_TIMEOUT = 15


class AgentConfigError(ValueError):
    """可供控制器返回的操作错误。"""

    def __init__(self, message: str, code: str = "invalid_request", status: int = 400, **details):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details


class LocalConfigFiles:
    """在应用进程所属用户的文件系统中访问配置。"""

    local = True

    def __init__(self, home: Path | None = None):
        self.home = str(home or Path.home())

    @blocking_io
    def resolve(self, path: str) -> str:
        if path == "~" or path.startswith(("~/", "~\\")):
            path = self.home + path[1:]
        return str(Path(os.path.expandvars(path)).resolve())

    @blocking_io
    def read(self, path: str) -> bytes | None:
        try:
            with open(path, "rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise AgentConfigError("配置文件超过 2 MiB 限制")
            return data
        except FileNotFoundError:
            return None

    @blocking_io
    def mode(self, path: str) -> int:
        try:
            return stat.S_IMODE(os.stat(path).st_mode)
        except FileNotFoundError:
            return 0o600

    @blocking_io
    def write(self, path: str, data: bytes, mode: int = 0o600) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @blocking_io
    def remove(self, path: str) -> None:
        Path(path).unlink(missing_ok=True)

    def alive(self) -> bool:
        return True

    def close(self) -> None:
        pass


class SftpConfigFiles:
    """通过已验证主机密钥的 SFTP 会话访问远程用户的文件。"""

    local = False

    def __init__(self, client: paramiko.SSHClient):
        self.client = client
        self.sftp = client.open_sftp()
        self.sftp.get_channel().settimeout(CONNECT_TIMEOUT)
        self.home = self.sftp.normalize(".")
        self.client.get_transport().set_keepalive(30)

    def resolve(self, path: str) -> str:
        if path == "~" or path.startswith("~/"):
            path = self.home + path[1:]
        if not path.startswith("/"):
            path = posixpath.join(self.home, path)
        path = posixpath.normpath(path)
        tail = []
        current = path
        while True:
            try:
                return posixpath.join(self.sftp.normalize(current), *reversed(tail))
            except OSError as exc:
                if exc.errno != errno.ENOENT or current == "/":
                    raise
                tail.append(posixpath.basename(current))
                current = posixpath.dirname(current)

    def read(self, path: str) -> bytes | None:
        try:
            with self.sftp.open(path, "rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise AgentConfigError("配置文件超过 2 MiB 限制")
            return data
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return None
            raise

    def mode(self, path: str) -> int:
        try:
            return stat.S_IMODE(self.sftp.stat(path).st_mode)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return 0o600
            raise

    def _mkdir(self, directory: str) -> None:
        try:
            if not stat.S_ISDIR(self.sftp.stat(directory).st_mode):
                raise AgentConfigError("配置文件的父路径不是目录")
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise
            self._mkdir(posixpath.dirname(directory))
            try:
                self.sftp.mkdir(directory, 0o700)
            except OSError:
                if not stat.S_ISDIR(self.sftp.stat(directory).st_mode):
                    raise

    def write(self, path: str, data: bytes, mode: int = 0o600) -> None:
        self._mkdir(posixpath.dirname(path))
        temporary = posixpath.join(posixpath.dirname(path), f".{posixpath.basename(path)}.{uuid.uuid4().hex}.tmp")
        try:
            with self.sftp.open(temporary, "wx") as stream:
                self.sftp.chmod(temporary, mode)
                stream.write(data)
                stream.flush()
            try:
                self.sftp.posix_rename(temporary, path)
            except OSError as exc:
                if self.read(path) is not None:
                    raise AgentConfigError("远程 SFTP 服务需要支持原子文件替换（posix-rename）") from exc
                self.sftp.rename(temporary, path)
        finally:
            self.remove(temporary)

    def remove(self, path: str) -> None:
        try:
            self.sftp.remove(path)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise

    def alive(self) -> bool:
        transport = self.client.get_transport()
        return bool(transport and transport.is_active() and transport.is_authenticated())

    def close(self) -> None:
        try:
            self.sftp.close()
        finally:
            self.client.close()


def parse_proxy(url: str) -> dict:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https", "socks5", "socks5h")
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError
        port = (
            parsed.port
            if parsed.port is not None
            else {"http": 80, "https": 443, "socks5": 1080, "socks5h": 1080}[parsed.scheme]
        )
        if not 1 <= port <= 65535 or any(char in url for char in "\r\n\x00"):
            raise ValueError
        return {
            "scheme": parsed.scheme,
            "host": parsed.hostname,
            "port": port,
            "username": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
        }
    except (ValueError, TypeError) as exc:
        raise AgentConfigError("代理地址需使用 http://、https://、socks5:// 或 socks5h://") from exc


def public_proxy_url(url: str) -> str:
    proxy = parse_proxy(url)
    host = f"[{proxy['host']}]" if ":" in proxy["host"] else proxy["host"]
    return f"{proxy['scheme']}://{host}:{proxy['port']}"


def proxy_socket(host: str, port: int, mode: str, url: str = "") -> socket.socket:
    """显式选择直连、系统代理或自定义代理，HTTP 使用 CONNECT 隧道。"""
    if mode == "system":
        proxies = getproxies()
        url = "" if proxy_bypass(host) else proxies.get("all") or proxies.get("https") or proxies.get("http") or ""
    if mode == "direct" or not url:
        return socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    proxy = parse_proxy(url)
    if proxy["scheme"].startswith("socks5"):
        stream = socks.socksocket()
        try:
            stream.settimeout(CONNECT_TIMEOUT)
            stream.set_proxy(
                socks.SOCKS5,
                proxy["host"],
                proxy["port"],
                rdns=proxy["scheme"] == "socks5h",
                username=proxy["username"] or None,
                password=proxy["password"] or None,
            )
            stream.connect((host, port))
            return stream
        except BaseException:
            stream.close()
            raise
    stream = socket.create_connection((proxy["host"], proxy["port"]), timeout=CONNECT_TIMEOUT)
    try:
        if proxy["scheme"] == "https":
            stream = ssl.create_default_context().wrap_socket(stream, server_hostname=proxy["host"])
        authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        request = f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n"
        if proxy["username"] or proxy["password"]:
            credentials = base64.b64encode(f"{proxy['username']}:{proxy['password']}".encode()).decode()
            request += f"Proxy-Authorization: Basic {credentials}\r\n"
        stream.sendall((request + "\r\n").encode("ascii"))
        response = bytearray()
        while not response.endswith(b"\r\n\r\n"):
            chunk = stream.recv(1)
            if not chunk or len(response) >= 16384:
                raise AgentConfigError("HTTP 代理未返回有效 CONNECT 响应", "proxy_failed", 502)
            response.extend(chunk)
        status = bytes(response).split(b"\r\n", 1)[0].split()
        if len(status) < 2 or status[1] != b"200":
            raise AgentConfigError("HTTP 代理拒绝了 SSH 隧道，请检查代理认证与目标端口", "proxy_failed", 502)
        return stream
    except BaseException:
        stream.close()
        raise


def connect_ssh(
    machine: dict, password: str = "", proxy_url: str = "", trust_fingerprint: str = ""
) -> tuple[SftpConfigFiles, str]:
    """验证系统 known_hosts 或已登记的指纹，首次未知密钥由页面确认。"""
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    fingerprint = ""
    expected = machine.get("fingerprint") or trust_fingerprint

    class CheckHostKey(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            nonlocal fingerprint
            fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
            if expected and fingerprint != expected:
                raise AgentConfigError("SSH 主机指纹已变化，请核实服务器身份后重新登记机器", "host_key_changed", 409)
            if not expected:
                raise AgentConfigError(
                    "首次连接需要确认 SSH 主机指纹",
                    "host_key_required",
                    409,
                    fingerprint=fingerprint,
                    key_type=key.get_name(),
                )

    client.set_missing_host_key_policy(CheckHostKey())
    stream = proxy_socket(machine["host"], int(machine["port"]), machine["proxy"]["mode"], proxy_url)
    try:
        client.connect(
            machine["host"],
            port=int(machine["port"]),
            username=machine["user"],
            password=password or None,
            sock=stream,
            timeout=CONNECT_TIMEOUT,
            banner_timeout=CONNECT_TIMEOUT,
            auth_timeout=CONNECT_TIMEOUT,
            allow_agent=not bool(password),
            look_for_keys=not bool(password),
        )
        key = client.get_transport().get_remote_server_key()
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if expected and fingerprint != expected:
            raise AgentConfigError("SSH 主机指纹与登记值不一致", "host_key_changed", 409)
        return SftpConfigFiles(client), fingerprint
    except BaseException:
        client.close()
        stream.close()
        raise
