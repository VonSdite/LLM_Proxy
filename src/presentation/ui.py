"""同源 Vue 应用入口与轻量界面上下文。"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path

from flask import current_app, jsonify, make_response, render_template, request, url_for

from ..utils.app_version import get_app_version


def is_local_management_request() -> bool:
    """要求连接对端和 Host 同时为回环地址，与 Agent 文件管理边界一致。"""
    try:
        host = (
            request.host.split("]", 1)[0].lstrip("[") if request.host.startswith("[") else request.host.split(":", 1)[0]
        )
        local_host = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
        return local_host and ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def render_ui():
    """读取构建清单，为明确的页面路由提供应用入口。"""
    asset_root = Path(current_app.static_folder) / "app"
    try:
        manifest = json.loads((asset_root / "manifest.json").read_text(encoding="utf-8"))
        entry = manifest["index.html"]
        styles = list(entry.get("css", []))
        imports = []
        visited = set()

        def collect(key):
            if key in visited:
                return
            visited.add(key)
            chunk = manifest[key]
            imports.append(chunk["file"])
            styles.extend(chunk.get("css", []))
            for dependency in chunk.get("imports", []):
                collect(dependency)

        for dependency in entry.get("imports", []):
            collect(dependency)

        def asset_url(name):
            path = (asset_root / name).resolve()
            if not path.is_relative_to(asset_root.resolve()) or not path.is_file():
                raise ValueError("Invalid frontend asset path")
            return url_for("static", filename="app/" + name)

        html = render_template(
            "app.html",
            script_url=asset_url(entry["file"]),
            style_urls=[asset_url(name) for name in dict.fromkeys(styles)],
            preload_urls=[asset_url(name) for name in dict.fromkeys(imports)],
        )
        response = make_response(html)
    except (OSError, ValueError, KeyError, TypeError):
        current_app.logger.exception("Frontend build assets are unavailable")
        response = make_response(
            "Frontend assets are unavailable. Run npm --prefix frontend ci and npm --prefix frontend run build.", 503
        )
    response.headers["Cache-Control"] = "no-store"
    return response


class UiController:
    """界面上下文只暴露会话状态、版本与功能可见性。"""

    def __init__(self, ctx, auth_service):
        self._config = ctx.config_manager
        self._auth = auth_service
        ctx.flask_app.add_url_rule("/api/ui/bootstrap", "ui_bootstrap", self.bootstrap, methods=["GET"])

    def bootstrap(self):
        auth_enabled = self._auth.is_auth_enabled()
        session = request.cookies.get("session_token")
        authenticated = not auth_enabled or self._auth.validate_session(session)
        config = self._config
        response = jsonify(
            version=get_app_version(),
            username=self._auth.get_session_username(session) or "" if authenticated and auth_enabled else "",
            auth_enabled=auth_enabled,
            authenticated=authenticated,
            features={
                "oauth": authenticated and config.is_oauth_enabled(),
                "api_keys": authenticated and config.is_api_key_management_enabled(),
                "model_mapping": authenticated and config.is_model_mapping_enabled(),
                "whitelist": authenticated and config.is_chat_whitelist_enabled(),
            },
            capabilities={"agents": authenticated and (auth_enabled or is_local_management_request())},
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Cookie, Host"
        return response
