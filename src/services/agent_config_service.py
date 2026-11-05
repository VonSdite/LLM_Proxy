"""Agent 配置控制平面：目标登记、连接、预览和带备份的文件写入。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import posixpath
import secrets
import socket
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import gevent
import paramiko
from gevent.lock import RLock

from ..external.agent_config_transport import (
    AgentConfigError,
    LocalConfigFiles,
    connect_ssh,
    parse_proxy,
    public_proxy_url,
)
from ..repositories.agent_config_repository import AgentConfigRepository
from .agent_config_documents import (
    MISSING,
    MODEL_LIMIT_PRESETS,
    at_path,
    desired_fields,
    display_path,
    edit_document,
    parse_document,
    value_hash,
    visible_fields,
)

TARGETS = ("claude", "codex", "codexAuth", "opencode")
AGENTS = ("claude", "codex", "opencode")
SESSION_TTL = 30 * 60
PREVIEW_TTL = 5 * 60


def _version(raw: bytes | None) -> str:
    return "missing" if raw is None else hashlib.sha256(raw).hexdigest()


def _text(raw: bytes | None) -> str:
    try:
        return (raw or b"").decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise AgentConfigError("配置文件必须使用 UTF-8 编码") from exc


class AgentConfigService:
    """以磁盘文件为准，SSH 连接与预览票据按浏览器会话隔离。"""

    def __init__(
        self,
        ctx,
        provider_manager,
        api_key_service,
        model_mapping_service,
        codex_oauth_service,
        claude_oauth_service,
        *,
        home: Path | None = None,
    ):
        self.ctx = ctx
        self.providers = provider_manager
        self.api_keys = api_key_service
        self.mapping = model_mapping_service
        self.codex_oauth = codex_oauth_service
        self.claude_oauth = claude_oauth_service
        self.local = LocalConfigFiles(home)
        self.repository = AgentConfigRepository(
            Path(ctx.config_manager.get_database_path()).resolve().parent / "agent-config"
        )
        self.state = self.repository.load()
        self.lock = RLock()
        self.sessions = {}
        self.credentials = {}
        self.previews = {}
        self.worker = None

    def start_cleanup(self):
        if self.worker is None:
            self.worker = gevent.spawn(self._cleanup_loop)

    def _cleanup_loop(self):
        while True:
            gevent.sleep(60)
            with self.lock:
                self._expire()

    def _expire(self):
        now = time.monotonic()
        for key, session in list(self.sessions.items()):
            if now - session["touched"] > SESSION_TTL:
                session["files"].close()
                self.sessions.pop(key, None)
        for key, credentials in list(self.credentials.items()):
            if now - credentials["touched"] > SESSION_TTL:
                self.credentials.pop(key, None)
        for token, preview in list(self.previews.items()):
            if now > preview["expires"]:
                self.previews.pop(token, None)

    def close(self):
        if self.worker is not None:
            self.worker.kill()
            self.worker = None
        with self.lock:
            for session in self.sessions.values():
                session["files"].close()
            self.sessions.clear()
            self.credentials.clear()
            self.previews.clear()

    def _machine(self, machine_id):
        if machine_id == "local":
            return {
                "id": "local",
                "name": "本机",
                "local": True,
                "host": "",
                "user": "",
                "port": "",
                "proxy": {"mode": "direct", "url": ""},
            }
        machine = self.state["machines"].get(machine_id)
        if machine is None:
            raise AgentConfigError("机器不存在", "not_found", 404)
        return machine

    def _public_machine(self, owner, machine):
        result = deepcopy(machine)
        session = self.sessions.get((owner, machine["id"]))
        result.update(
            connected=bool(machine.get("local") or session and session["files"].alive()), connecting=False, password=""
        )
        if machine.get("local"):
            result["platform"] = platform.system()
        credentials = self.credentials.get((owner, machine["id"]), {})
        if credentials.get("proxy_url"):
            result["proxy"]["url"] = credentials["proxy_url"]
        return result

    def list_machines(self, owner):
        with self.lock:
            self._expire()
            return [
                self._public_machine(owner, self._machine("local")),
                *(self._public_machine(owner, machine) for machine in self.state["machines"].values()),
            ]

    def save_machine(self, owner, payload, machine_id=None):
        with self.lock:
            self._expire()
            if machine_id == "local":
                raise AgentConfigError("本机配置不可作为 SSH 机器修改")
            previous = self._machine(machine_id) if machine_id else None
            host = str(payload.get("host") or "").strip().strip("[]")
            user = str(payload.get("user") or "").strip()
            if (
                not host
                or len(host) > 253
                or any(char.isspace() or ord(char) < 32 for char in host)
                or any(char in host for char in "/\\@?#")
            ):
                raise AgentConfigError("请填写有效的 IP 或主机名")
            if not user or len(user) > 128 or any(ord(char) < 32 for char in user):
                raise AgentConfigError("请填写有效的 SSH 用户名")
            try:
                port = int(payload.get("port", 22))
                if not 1 <= port <= 65535:
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise AgentConfigError("SSH 端口需为 1–65535 的整数") from exc
            proxy = payload.get("proxy", {})
            if not isinstance(proxy, dict) or proxy.get("mode", "direct") not in ("direct", "system", "custom"):
                raise AgentConfigError("请选择有效的代理模式")
            proxy_mode = proxy.get("mode", "direct")
            proxy_url = str(proxy.get("url") or "").strip() if proxy_mode == "custom" else ""
            if proxy_mode == "custom":
                parse_proxy(proxy_url)
            machine_id = machine_id or secrets.token_hex(12)
            identity_changed = previous and any(
                str(previous[key]) != str(value) for key, value in (("host", host), ("port", port), ("user", user))
            )
            machine = {
                "id": machine_id,
                "name": str(payload.get("name") or host).strip()[:48],
                "host": host,
                "port": str(port),
                "user": user,
                "local": False,
                "proxy": {"mode": proxy_mode, "url": public_proxy_url(proxy_url) if proxy_url else ""},
            }
            if previous and not identity_changed and previous.get("fingerprint"):
                machine["fingerprint"] = previous["fingerprint"]
            next_state = deepcopy(self.state)
            next_state["machines"][machine_id] = machine
            self.repository.save(next_state)
            self.state = next_state
            for key in list(self.sessions):
                if key[1] == machine_id:
                    self.sessions.pop(key)["files"].close()
            if identity_changed:
                for key in list(self.credentials):
                    if key[1] == machine_id:
                        self.credentials.pop(key)
            credentials = self.credentials.get((owner, machine_id), {})
            password = str(payload.get("password") or credentials.get("password") or "")
            if proxy_url and credentials.get("proxy_url") and proxy_url == public_proxy_url(credentials["proxy_url"]):
                proxy_url = credentials["proxy_url"]
            self.credentials[(owner, machine_id)] = {
                "password": password,
                "proxy_url": proxy_url,
                "touched": time.monotonic(),
            }
            return self._public_machine(owner, machine)

    def remove_machine(self, owner, machine_id):
        with self.lock:
            if machine_id == "local":
                raise AgentConfigError("本机不可删除")
            self._machine(machine_id)
            next_state = deepcopy(self.state)
            next_state["machines"].pop(machine_id)
            next_state["paths"].pop(machine_id, None)
            self.repository.save(next_state)
            self.state = next_state
            for key in list(self.sessions):
                if key[1] == machine_id:
                    self.sessions.pop(key)["files"].close()
            for key in list(self.credentials):
                if key[1] == machine_id:
                    self.credentials.pop(key)

    def connect(self, owner, machine_id, payload):
        with self.lock:
            self._expire()
            machine = self._machine(machine_id)
            if machine.get("local"):
                return self.snapshot(owner, machine_id)
            self.disconnect(owner, machine_id)
            credentials = self.credentials.get((owner, machine_id), {})
            password = str(payload.get("password") or credentials.get("password") or "")
            proxy_url = str(payload.get("proxyUrl") or credentials.get("proxy_url") or machine["proxy"]["url"])
            try:
                files, fingerprint = connect_ssh(
                    machine, password, proxy_url, str(payload.get("trustFingerprint") or "")
                )
            except AgentConfigError:
                raise
            except paramiko.AuthenticationException as exc:
                raise AgentConfigError(
                    "SSH 认证失败，请编辑机器并检查用户名、密码或本机 SSH 密钥", "authentication_failed", 401
                ) from exc
            except paramiko.BadHostKeyException as exc:
                raise AgentConfigError("SSH 主机密钥与 known_hosts 不一致", "host_key_changed", 409) from exc
            except (OSError, paramiko.SSHException, EOFError) as exc:
                raise AgentConfigError("SSH 连接失败，请检查主机、端口和代理设置", "connection_failed", 502) from exc
            if machine.get("fingerprint") != fingerprint:
                next_state = deepcopy(self.state)
                next_state["machines"][machine_id]["fingerprint"] = fingerprint
                try:
                    self.repository.save(next_state)
                except BaseException:
                    files.close()
                    raise
                self.state = next_state
            self.sessions[(owner, machine_id)] = {
                "files": files,
                "id": secrets.token_hex(16),
                "touched": time.monotonic(),
            }
            self.credentials[(owner, machine_id)] = {
                "password": password,
                "proxy_url": proxy_url,
                "touched": time.monotonic(),
            }
            try:
                return self.snapshot(owner, machine_id)
            except BaseException:
                self.disconnect(owner, machine_id)
                raise

    def disconnect(self, owner, machine_id):
        with self.lock:
            session = self.sessions.pop((owner, machine_id), None)
            if session:
                session["files"].close()
            for token, preview in list(self.previews.items()):
                if preview["owner"] == owner and preview["machine"] == machine_id:
                    self.previews.pop(token)

    def _files(self, owner, machine_id):
        self._expire()
        machine = self._machine(machine_id)
        if machine.get("local"):
            return self.local, "local"
        session = self.sessions.get((owner, machine_id))
        if not session or not session["files"].alive():
            raise AgentConfigError("SSH 连接已断开，请重新连接", "disconnected", 409)
        session["touched"] = time.monotonic()
        credentials = self.credentials.get((owner, machine_id))
        if credentials:
            credentials["touched"] = session["touched"]
        return session["files"], session["id"]

    def _paths(self, machine_id, files, overrides=None):
        path_module = os.path if files.local else posixpath
        join = path_module.join
        paths = {
            "claude": join(files.home, ".claude", "settings.json"),
            "codex": join(files.home, ".codex", "config.toml"),
            "codexAuth": join(files.home, ".codex", "auth.json"),
            "opencode": join(files.home, ".config", "opencode", "opencode.json"),
        }
        if files.local:
            if os.environ.get("CODEX_HOME"):
                paths.update(
                    codex=join(os.environ["CODEX_HOME"], "config.toml"),
                    codexAuth=join(os.environ["CODEX_HOME"], "auth.json"),
                )
            if os.environ.get("CLAUDE_CONFIG_DIR"):
                paths["claude"] = join(os.environ["CLAUDE_CONFIG_DIR"], "settings.json")
        overrides = self.state["paths"].get(machine_id, {}) if overrides is None else overrides
        result = {}
        for target, path in paths.items():
            agent = "codex" if target == "codexAuth" else target
            override = overrides.get(agent, "")
            actual = join(override, path_module.basename(path)) if agent == "codex" and override else override
            result[target] = {
                "defaultPath": files.resolve(path),
                "path": files.resolve(actual or path),
                "customPath": override,
                "defaultDirectory": files.resolve(path_module.dirname(path)),
            }
        return result

    def _catalog(self):
        models = list(self.providers.list_visible_model_names())
        if self.ctx.config_manager.is_oauth_enabled():
            models.extend(self.codex_oauth.list_model_names())
            models.extend(self.claude_oauth.list_model_names())
        if self.ctx.config_manager.is_model_mapping_enabled():
            models.extend(self.mapping.list_mapping_ids())
        return sorted(set(models))

    def bootstrap(self, owner, origin):
        with self.lock:
            models = self._catalog()
            prefixes = sorted({model.partition("/")[0] if "/" in model else "main" for model in models})
            providers = [
                {
                    "id": prefix,
                    "key": prefix,
                    "name": prefix,
                    "enabled": True,
                    "apiKey": "",
                    "codexWireApi": "responses",
                    "opencodeNpm": "@ai-sdk/openai-compatible",
                    "models": [
                        {"id": model}
                        for model in models
                        if (model.partition("/")[0] if "/" in model else "main") == prefix
                    ],
                }
                for prefix in prefixes
            ]
            addresses = {"127.0.0.1"}
            try:
                addresses.update(
                    entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                )
            except OSError:
                pass
            keys = (
                self.api_keys.get_api_keys(page=1, page_size=10000)
                if self.ctx.config_manager.is_api_key_management_enabled()
                else []
            )
            return {
                "machines": self.list_machines(owner),
                "providers": providers,
                "models": models,
                "modelLimitPresets": MODEL_LIMIT_PRESETS,
                "interfaces": [
                    {"ip": ip, "prefix": ip.rsplit(".", 1)[0] + "."}
                    for ip in sorted(addresses)
                    if not ip.startswith("127.")
                ],
                "serverHost": self.ctx.config_manager.get_server_host(),
                "serverPort": self.ctx.config_manager.get_server_port(),
                "serverOrigin": origin,
                "apiKeyEnabled": self.ctx.config_manager.is_api_key_management_enabled(),
                "apiKeys": [
                    {"name": item["name"], "key": item.get("api_key", "")}
                    for item in keys
                    if item.get("enabled") and item.get("api_key")
                ],
            }

    def snapshot(self, owner, machine_id, *, path_overrides=None):
        with self.lock:
            files, _ = self._files(owner, machine_id)
            paths = self._paths(machine_id, files, path_overrides)
            result = {}
            for target, info in paths.items():
                raw = files.read(info["path"])
                error = ""
                try:
                    managed = visible_fields(target, parse_document(target, _text(raw)))
                except ValueError as exc:
                    managed, error = {}, str(exc)
                result[target] = {
                    **info,
                    "usesDefaultPath": not bool(info["customPath"]),
                    "exists": raw is not None,
                    "version": _version(raw),
                    "parseError": error,
                    "managed": managed,
                }
            machine = self._public_machine(owner, self._machine(machine_id))
            machine["home"] = files.home
            if not machine.get("local"):
                try:
                    with socket.socket(
                        socket.AF_INET6 if ":" in machine["host"] else socket.AF_INET, socket.SOCK_DGRAM
                    ) as stream:
                        stream.connect((machine["host"], int(machine["port"])))
                        machine["serviceHost"] = stream.getsockname()[0]
                except OSError:
                    pass
            return {"machine": machine, "files": result}

    def set_path(self, owner, machine_id, payload):
        with self.lock:
            self._files(owner, machine_id)
            target = payload.get("target", "opencode")
            path = payload.get("path", "")
            if target not in AGENTS or not isinstance(path, str):
                raise AgentConfigError("配置路径参数无效")
            path = path.strip()
            if len(path) > 4096 or any(ord(char) < 32 for char in path):
                raise AgentConfigError("配置路径包含无效字符或过长")
            if path and target == "claude" and Path(path).suffix.lower() != ".json":
                raise AgentConfigError("Claude 路径需指向 .json 文件")
            if path and target == "opencode" and Path(path).suffix.lower() not in (".json", ".jsonc"):
                raise AgentConfigError("opencode 路径需指向 .json 或 .jsonc 文件")
            if (
                path
                and target == "codex"
                and path.replace("\\", "/").rsplit("/", 1)[-1] in ("config.toml", "auth.json")
            ):
                raise AgentConfigError("请填写包含 config.toml 和 auth.json 的目录")
            next_state = deepcopy(self.state)
            overrides = next_state["paths"].setdefault(machine_id, {})
            if path:
                overrides[target] = path
            else:
                overrides.pop(target, None)
            # 先读取候选路径，读取失败时保留上次可用的路径设置。
            result = self.snapshot(owner, machine_id, path_overrides=overrides)
            self.repository.save(next_state)
            self.state = next_state
            return result

    def _ownership_key(self, machine_id, path):
        machine = self._machine(machine_id)
        identity = (
            ["local"]
            if machine.get("local")
            else [machine["host"], machine["port"], machine["user"], machine.get("fingerprint", "")]
        )
        return json.dumps([*identity, path], ensure_ascii=True)

    def _is_service_url(self, value, origin=""):
        """按完整 HTTP 端点识别本服务，外部地址独立管理模型。"""
        try:
            parsed = urlsplit(str(value or "").strip())
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path.rstrip("/") not in ("", "/v1")
            ):
                return False
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            current = urlsplit(origin)
            if current.hostname and (
                parsed.scheme,
                parsed.hostname,
                port,
            ) == (
                current.scheme,
                current.hostname,
                current.port or (443 if current.scheme == "https" else 80),
            ):
                return True
        except ValueError:
            return False
        if parsed.scheme != "http" or port != int(self.ctx.config_manager.get_server_port()):
            return False
        hosts = {"localhost", "127.0.0.1", "::1", self.ctx.config_manager.get_server_host().strip("[]")}
        hosts.difference_update(("0.0.0.0", "::"))
        if parsed.hostname in hosts:
            return True
        try:
            hosts.update(entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None))
        except OSError:
            pass
        return parsed.hostname in hosts

    def preview(self, owner, machine_id, agent, payload, *, origin=""):
        with self.lock:
            if agent not in AGENTS:
                raise AgentConfigError("未知 Agent 类型")
            files, session = self._files(owner, machine_id)
            paths = self._paths(machine_id, files)
            versions = payload.get("versions", {})
            if not isinstance(versions, dict):
                raise AgentConfigError("缺少文件版本，请刷新配置")
            desired = None
            if "raw" not in payload:
                draft = payload.get("draft")
                catalog = self._catalog()
                if (
                    agent == "opencode"
                    and isinstance(draft, dict)
                    and not self._is_service_url(draft.get("baseUrl"), origin)
                ):
                    catalog = []
                desired = desired_fields(agent, draft, catalog)
            raw_inputs = payload.get("raw")
            if "raw" in payload and (
                not isinstance(raw_inputs, dict)
                or set(raw_inputs) - ({"codex", "codexAuth"} if agent == "codex" else {agent})
            ):
                raise AgentConfigError("文件编辑目标无效")
            targets = ("codex", "codexAuth") if agent == "codex" else (agent,)
            if raw_inputs is not None:
                targets = tuple(target for target in targets if target in raw_inputs)
            planned, rows = [], []
            for target in targets:
                path = paths[target]["path"]
                before = files.read(path)
                if versions.get(target) != _version(before):
                    raise AgentConfigError("配置文件已被其他程序修改，请刷新后重新预览", "file_conflict", 409)
                ownership_key = self._ownership_key(machine_id, path)
                ownership = self.state["managed"].get(ownership_key, [])
                if raw_inputs is not None:
                    content = raw_inputs.get(target, _text(before))
                    if not isinstance(content, str) or len(content.encode()) > 2 * 1024 * 1024:
                        raise AgentConfigError("配置文件内容无效或超过 2 MiB")
                    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
                    parse_document(target, content)
                    after = (content.rstrip() + "\n").encode()
                    updates = ownership
                    rows.append(
                        {
                            "path": paths[target]["path"],
                            "file": posixpath.basename(path.replace("\\", "/")),
                            "kind": "same" if before == after else "change",
                            "current": "当前文件",
                            "next": "编辑后的文件",
                        }
                    )
                else:
                    data = parse_document(target, _text(before))
                    fields = desired[target]
                    edits = dict(fields)
                    for old in ownership:
                        old_path = tuple(old["path"])
                        current = at_path(data, old_path)
                        if old_path not in fields and current is not MISSING and value_hash(current) == old["hash"]:
                            edits[old_path] = MISSING
                    for path_parts, value in edits.items():
                        current = at_path(data, path_parts)
                        if value is MISSING:
                            kind = "same" if current is MISSING else "remove"
                        elif current is MISSING:
                            kind = "add"
                        else:
                            kind = "same" if current == value and type(current) is type(value) else "change"
                        secret = any(name in path_parts[-1].lower() for name in ("token", "api_key", "apikey"))
                        rows.append(
                            {
                                "path": display_path(path_parts),
                                "file": posixpath.basename(path.replace("\\", "/")),
                                "kind": kind,
                                "current": None
                                if current is MISSING
                                else "••••••••"
                                if secret and current
                                else current,
                                "next": None if value is MISSING else "••••••••" if secret and value else value,
                            }
                        )
                    text = _text(before)
                    actual_edits = {
                        path: value
                        for path, value in edits.items()
                        if (value is MISSING and at_path(data, path) is not MISSING)
                        or (
                            value is not MISSING
                            and (at_path(data, path) != value or type(at_path(data, path)) is not type(value))
                        )
                    }
                    after = edit_document(target, text, actual_edits).encode() if actual_edits else before
                    updates = [
                        {"path": list(path), "hash": value_hash(value)}
                        for path, value in fields.items()
                        if value is not MISSING
                    ]
                if after is not None and len(after) > 2 * 1024 * 1024:
                    raise AgentConfigError("生成的配置文件超过 2 MiB 限制")
                planned.append(
                    {
                        "target": target,
                        "path": path,
                        "before": before,
                        "after": after,
                        "mode": files.mode(path),
                        "ownershipKey": ownership_key,
                        "ownershipBefore": value_hash(ownership),
                        "ownershipAfter": updates,
                    }
                )
            token = secrets.token_urlsafe(32)
            if len(self.previews) >= 40:
                self.previews.pop(next(iter(self.previews)))
            self.previews[token] = {
                "owner": owner,
                "machine": machine_id,
                "agent": agent,
                "session": session,
                "expires": time.monotonic() + PREVIEW_TTL,
                "files": planned,
            }
            return {
                "token": token,
                "rows": rows,
                "hasChanges": any(entry["before"] != entry["after"] for entry in planned),
            }

    def apply(self, owner, machine_id, token):
        with self.lock:
            files, session = self._files(owner, machine_id)
            plan = self.previews.get(token)
            if (
                not plan
                or plan["owner"] != owner
                or plan["machine"] != machine_id
                or plan["session"] != session
                or time.monotonic() > plan["expires"]
            ):
                raise AgentConfigError("保存预览已失效，请重新预览", "preview_expired", 409)
            paths = self._paths(machine_id, files)
            for entry in plan["files"]:
                if (
                    paths[entry["target"]]["path"] != entry["path"]
                    or files.read(entry["path"]) != entry["before"]
                    or value_hash(self.state["managed"].get(entry["ownershipKey"], [])) != entry["ownershipBefore"]
                ):
                    raise AgentConfigError("配置文件或管理状态已变化，请刷新后重新预览", "file_conflict", 409)
            changed = [entry for entry in plan["files"] if entry["before"] != entry["after"]]
            completed = []
            try:
                for entry in changed:
                    if entry["before"] is not None:
                        for index in range(5, 1, -1):
                            previous = files.read(f"{entry['path']}.bak.{index - 1}")
                            if previous is not None:
                                files.write(f"{entry['path']}.bak.{index}", previous, entry["mode"])
                        files.write(f"{entry['path']}.bak.1", entry["before"], entry["mode"])
                for entry in changed:
                    if files.read(entry["path"]) != entry["before"]:
                        raise AgentConfigError("配置文件已在保存期间变化，请刷新", "file_conflict", 409)
                    files.write(entry["path"], entry["after"] or b"", entry["mode"])
                    completed.append(entry)
                next_state = deepcopy(self.state)
                for entry in plan["files"]:
                    next_state["managed"][entry["ownershipKey"]] = entry["ownershipAfter"]
                self.repository.save(next_state)
                self.state = next_state
            except BaseException as exc:
                failed = []
                for entry in reversed(completed):
                    try:
                        if files.read(entry["path"]) != entry["after"]:
                            raise AgentConfigError("回滚期间配置被其他程序修改")
                        if entry["before"] is None:
                            files.remove(entry["path"])
                        else:
                            files.write(entry["path"], entry["before"], entry["mode"])
                    except Exception:
                        failed.append(entry["target"])
                if failed:
                    raise AgentConfigError(
                        "部分文件回滚失败，请通过配置文件和 .bak.1 检查恢复", "partial_write", 500, targets=failed
                    ) from exc
                raise
            self.previews.pop(token, None)
            self.ctx.logger.info(
                "Agent configuration saved: machine=%s agent=%s files=%d", machine_id, plan["agent"], len(changed)
            )
            result = self.snapshot(owner, machine_id)
            result["written"] = len(changed)
            return result

    def read_files(self, owner, machine_id, agent, backup=0):
        with self.lock:
            if agent not in AGENTS or not isinstance(backup, int) or not 0 <= backup <= 5:
                raise AgentConfigError("未知文件或备份编号")
            files, _ = self._files(owner, machine_id)
            paths = self._paths(machine_id, files)
            result = []
            for target in ("codex", "codexAuth") if agent == "codex" else (agent,):
                path = paths[target]["path"]
                raw = files.read(path + (f".bak.{backup}" if backup else ""))
                result.append(
                    {
                        "target": target,
                        "path": path,
                        "exists": raw is not None,
                        "text": _text(raw),
                        "version": _version(files.read(path)) if backup else _version(raw),
                    }
                )
            return {"files": result}
