"""Agent 配置页面及文件管理接口。"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
from functools import wraps
from urllib.parse import urlsplit

from flask import g, jsonify, make_response, redirect, request

from ..external.agent_config_transport import AgentConfigError
from .ui import render_ui


class AgentConfigController:
    """沿用管理认证，文件操作按浏览器隔离且仅接受同源请求。"""

    def __init__(self, ctx, service, auth_service):
        self.ctx = ctx
        self.service = service
        self.auth = auth_service
        routes = (
            ("/agents", "GET", self.page),
            ("/api/agent-config/bootstrap", "GET", self.bootstrap),
            ("/api/agent-config/machines", "GET", self.machines),
            ("/api/agent-config/machines", "POST", self.create_machine),
            ("/api/agent-config/machines/<machine_id>", "PUT", self.update_machine),
            ("/api/agent-config/machines/<machine_id>", "DELETE", self.delete_machine),
            ("/api/agent-config/machines/<machine_id>/connect", "POST", self.connect),
            ("/api/agent-config/machines/<machine_id>/disconnect", "POST", self.disconnect),
            ("/api/agent-config/machines/<machine_id>/snapshot", "GET", self.snapshot),
            ("/api/agent-config/machines/<machine_id>/path", "PUT", self.path),
            ("/api/agent-config/machines/<machine_id>/<agent>/preview", "POST", self.preview),
            ("/api/agent-config/machines/<machine_id>/apply", "POST", self.apply),
            ("/api/agent-config/machines/<machine_id>/<agent>/files", "GET", self.files),
        )
        for route, method, handler in routes:
            ctx.flask_app.add_url_rule(
                route, "agent_config_" + handler.__name__, self._guard(handler), methods=[method]
            )

    def _guard(self, handler):
        @wraps(handler)
        def guarded(**kwargs):
            response = None
            try:
                if self.auth.is_auth_enabled():
                    session = request.cookies.get("session_token")
                    if not self.auth.validate_session(session):
                        response = (
                            redirect("/login")
                            if request.path == "/agents"
                            else make_response(jsonify(error="请先登录", code="unauthorized"), 401)
                        )
                else:
                    try:
                        host = (
                            request.host.split("]", 1)[0].lstrip("[")
                            if request.host.startswith("[")
                            else request.host.split(":", 1)[0]
                        )
                        local_host = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
                    except ValueError:
                        local_host = False
                    try:
                        local_peer = ipaddress.ip_address(request.remote_addr or "").is_loopback
                    except ValueError:
                        local_peer = False
                    if not local_peer or not local_host:
                        raise AgentConfigError(
                            "请先在系统设置中启用管理账号，再从远程访问 Agent 配置", "authentication_required", 403
                        )
                    session = "local"
                if response is None:
                    if request.headers.get("Sec-Fetch-Site") == "cross-site":
                        raise AgentConfigError("请从管理页面访问此功能", "same_origin_required", 403)
                    origin = request.headers.get("Origin")
                    if origin:
                        parsed = urlsplit(origin)
                        if (
                            parsed.netloc.lower() != request.host.lower()
                            or parsed.scheme not in (request.scheme, "https")
                            or parsed.path
                            or parsed.query
                            or parsed.fragment
                        ):
                            raise AgentConfigError("请求来源无效", "same_origin_required", 403)
                    if request.path.startswith("/api/") and request.headers.get("X-Agent-Config") != "1":
                        raise AgentConfigError("缺少文件管理请求标识", "same_origin_required", 403)
                    browser_id = request.cookies.get("agent_config_session", "")
                    fresh = not re.fullmatch(r"[A-Za-z0-9_-]{43}", browser_id)
                    if fresh:
                        browser_id = secrets.token_urlsafe(32)
                    g.agent_config_owner = hashlib.sha256((session + ":" + browser_id).encode()).hexdigest()
                    response = make_response(handler(**kwargs))
                    if fresh:
                        response.set_cookie(
                            "agent_config_session",
                            browser_id,
                            httponly=True,
                            secure=request.is_secure,
                            samesite="Strict",
                        )
            except AgentConfigError as exc:
                response = make_response(jsonify(error=str(exc), code=exc.code, **exc.details), exc.status)
            except ValueError as exc:
                response = make_response(jsonify(error=str(exc), code="invalid_request"), 400)
            except OSError:
                self.ctx.logger.warning("Agent configuration file operation failed", exc_info=True)
                response = make_response(
                    jsonify(error="文件访问失败，请检查路径、权限及 SSH 连接", code="file_access_failed"), 502
                )
            except Exception:
                self.ctx.logger.exception("Agent configuration operation failed")
                response = make_response(jsonify(error="操作失败，请检查连接后重试", code="operation_failed"), 500)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "same-origin"
            return response

        return guarded

    @staticmethod
    def _payload():
        if request.content_length and request.content_length > 12 * 1024 * 1024:
            raise AgentConfigError("请求内容过大")
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise AgentConfigError("请求需为 JSON 对象")
        return data

    def page(self):
        return render_ui()

    def bootstrap(self):
        return jsonify(self.service.bootstrap(g.agent_config_owner, request.host_url.rstrip("/")))

    def machines(self):
        return jsonify(machines=self.service.list_machines(g.agent_config_owner))

    def create_machine(self):
        return jsonify(machine=self.service.save_machine(g.agent_config_owner, self._payload())), 201

    def update_machine(self, machine_id):
        return jsonify(machine=self.service.save_machine(g.agent_config_owner, self._payload(), machine_id))

    def delete_machine(self, machine_id):
        self.service.remove_machine(g.agent_config_owner, machine_id)
        return jsonify(ok=True)

    def connect(self, machine_id):
        return jsonify(self.service.connect(g.agent_config_owner, machine_id, self._payload()))

    def disconnect(self, machine_id):
        self.service.disconnect(g.agent_config_owner, machine_id)
        return jsonify(ok=True)

    def snapshot(self, machine_id):
        return jsonify(self.service.snapshot(g.agent_config_owner, machine_id))

    def path(self, machine_id):
        return jsonify(self.service.set_path(g.agent_config_owner, machine_id, self._payload()))

    def preview(self, machine_id, agent):
        return jsonify(
            self.service.preview(
                g.agent_config_owner,
                machine_id,
                agent,
                self._payload(),
                origin=request.headers.get("Origin") or request.host_url.rstrip("/"),
            )
        )

    def apply(self, machine_id):
        token = self._payload().get("token")
        if not isinstance(token, str):
            raise AgentConfigError("缺少保存预览，请重新预览")
        return jsonify(self.service.apply(g.agent_config_owner, machine_id, token))

    def files(self, machine_id, agent):
        try:
            backup = int(request.args.get("backup", "0"))
        except ValueError as exc:
            raise AgentConfigError("备份编号无效") from exc
        return jsonify(self.service.read_files(g.agent_config_owner, machine_id, agent, backup))
