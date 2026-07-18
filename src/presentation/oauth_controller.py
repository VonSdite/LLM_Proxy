#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OAuth 管理接口控制器。"""

from __future__ import annotations

import io

from flask import jsonify, request, send_file
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..services import AuthenticationService, ClaudeOAuthService, CodexOAuthService
from .controller_utils import get_json_object
from .decorators import require_authentication


class OAuthController:
    """注册 OAuth 管理 API。"""

    def __init__(
        self,
        ctx: AppContext,
        codex_oauth_service: CodexOAuthService,
        claude_oauth_service: ClaudeOAuthService,
        auth_service: AuthenticationService,
    ):
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._codex_oauth_service = codex_oauth_service
        self._claude_oauth_service = claude_oauth_service
        self._auth_service = auth_service
        self._register_routes()

    def _register_routes(self) -> None:
        auth = require_authentication(self._auth_service)
        self._app.route("/api/oauth/codex/session", methods=["POST"])(auth(self.create_codex_session))
        self._app.route("/api/oauth/codex/callback", methods=["POST"])(auth(self.complete_codex_callback))
        self._app.route("/api/oauth/codex/auth-files", methods=["GET"])(auth(self.list_codex_auth_files))
        self._app.route("/api/oauth/codex/auth-files/export", methods=["POST"])(auth(self.export_codex_auth_files))
        self._app.route("/api/oauth/codex/auth-files/import", methods=["POST"])(auth(self.import_codex_auth_files))
        self._app.route("/api/oauth/codex/auth-files/<name>/disable", methods=["POST"])(
            auth(self.disable_codex_auth_file)
        )
        self._app.route("/api/oauth/codex/auth-files/<name>/enable", methods=["POST"])(
            auth(self.enable_codex_auth_file)
        )
        self._app.route("/api/oauth/codex/auth-files/<name>", methods=["DELETE"])(auth(self.delete_codex_auth_file))
        self._app.route("/api/oauth/codex/auth-files/<name>/quota", methods=["GET"])(
            auth(self.get_codex_auth_file_quota)
        )
        self._app.route("/api/oauth/codex/auth-files/<name>/reset-quota", methods=["POST"])(
            auth(self.reset_codex_auth_file_quota)
        )
        self._app.route("/api/oauth/codex/models", methods=["GET"])(auth(self.list_codex_models))
        self._app.route("/api/oauth/codex/models", methods=["POST"])(auth(self.add_codex_model))
        self._app.route("/api/oauth/codex/models/<path:model_id>", methods=["DELETE"])(auth(self.delete_codex_model))
        self._app.route("/api/oauth/claude/session", methods=["POST"])(auth(self.create_claude_session))
        self._app.route("/api/oauth/claude/callback", methods=["POST"])(auth(self.complete_claude_callback))
        self._app.route("/api/oauth/claude/auth-files", methods=["GET"])(auth(self.list_claude_auth_files))
        self._app.route("/api/oauth/claude/auth-files/export", methods=["POST"])(auth(self.export_claude_auth_files))
        self._app.route("/api/oauth/claude/auth-files/import", methods=["POST"])(auth(self.import_claude_auth_files))
        self._app.route("/api/oauth/claude/auth-files/<name>/disable", methods=["POST"])(
            auth(self.disable_claude_auth_file)
        )
        self._app.route("/api/oauth/claude/auth-files/<name>/enable", methods=["POST"])(
            auth(self.enable_claude_auth_file)
        )
        self._app.route("/api/oauth/claude/auth-files/<name>", methods=["DELETE"])(auth(self.delete_claude_auth_file))
        self._app.route("/api/oauth/claude/models", methods=["GET"])(auth(self.list_claude_models))
        self._app.route("/api/oauth/claude/models", methods=["POST"])(auth(self.add_claude_model))
        self._app.route("/api/oauth/claude/models/<path:model_id>", methods=["DELETE"])(auth(self.delete_claude_model))

    def create_codex_session(self) -> ResponseReturnValue:
        try:
            result = self._codex_oauth_service.start_login()
            return jsonify(result)
        except Exception as exc:
            self._logger.error("Error creating Codex OAuth session: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def complete_codex_callback(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            callback_url = str(payload.get("callback_url") or payload.get("redirect_url") or "").strip()
            result = self._codex_oauth_service.complete_login(callback_url)
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error completing Codex OAuth callback: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def create_claude_session(self) -> ResponseReturnValue:
        try:
            result = self._claude_oauth_service.start_login()
            return jsonify(result)
        except Exception as exc:
            self._logger.error("Error creating Claude OAuth session: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def complete_claude_callback(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            callback_url = str(payload.get("callback_url") or payload.get("redirect_url") or "").strip()
            result = self._claude_oauth_service.complete_login(callback_url)
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error completing Claude OAuth callback: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def list_codex_auth_files(self) -> ResponseReturnValue:
        try:
            return jsonify(self._codex_oauth_service.list_auth_files())
        except Exception as exc:
            self._logger.error("Error listing Codex OAuth auth files: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def list_claude_auth_files(self) -> ResponseReturnValue:
        try:
            return jsonify(self._claude_oauth_service.list_auth_files())
        except Exception as exc:
            self._logger.error("Error listing Claude OAuth auth files: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def disable_codex_auth_file(self, name: str) -> ResponseReturnValue:
        return self._set_codex_auth_file_enabled(name, False)

    def enable_codex_auth_file(self, name: str) -> ResponseReturnValue:
        return self._set_codex_auth_file_enabled(name, True)

    def _set_codex_auth_file_enabled(self, name: str, enabled: bool) -> ResponseReturnValue:
        try:
            return jsonify(self._codex_oauth_service.set_auth_file_enabled(name, enabled))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating Codex OAuth auth file enabled state: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def disable_claude_auth_file(self, name: str) -> ResponseReturnValue:
        return self._set_claude_auth_file_enabled(name, False)

    def enable_claude_auth_file(self, name: str) -> ResponseReturnValue:
        return self._set_claude_auth_file_enabled(name, True)

    def _set_claude_auth_file_enabled(self, name: str, enabled: bool) -> ResponseReturnValue:
        try:
            return jsonify(self._claude_oauth_service.set_auth_file_enabled(name, enabled))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating Claude OAuth auth file enabled state: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_codex_auth_file_quota(self, name: str) -> ResponseReturnValue:
        try:
            return jsonify(self._codex_oauth_service.get_auth_file_quota(name))
        except ValueError as exc:
            return jsonify(self._build_codex_quota_error_payload(name, exc)), 400
        except Exception as exc:
            self._logger.error("Error fetching Codex OAuth quota: %s", exc)
            return jsonify(self._build_codex_quota_error_payload(name, exc)), 500

    def reset_codex_auth_file_quota(self, name: str) -> ResponseReturnValue:
        try:
            return jsonify(self._codex_oauth_service.reset_auth_file_quota_state(name))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error resetting Codex OAuth quota state: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def _build_codex_quota_error_payload(self, name: str, exc: Exception) -> dict[str, str]:
        """构造配额刷新失败响应，并尽量带出本次刷新时间。"""
        payload = {"error": str(exc)}
        try:
            refreshed_at = self._codex_oauth_service.get_auth_file_quota_refreshed_at(name)
        except ValueError:
            refreshed_at = ""
        if refreshed_at:
            payload["quota_refreshed_at"] = refreshed_at
        return payload

    def export_codex_auth_files(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            result = self._codex_oauth_service.export_auth_files(self._get_export_auth_file_names(payload))
            return self._send_auth_file_export(result.content, result.filename)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error exporting Codex OAuth auth files: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def export_claude_auth_files(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            result = self._claude_oauth_service.export_auth_files(self._get_export_auth_file_names(payload))
            return self._send_auth_file_export(result.content, result.filename)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error exporting Claude OAuth auth files: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def import_codex_auth_files(self) -> ResponseReturnValue:
        try:
            result = self._codex_oauth_service.import_auth_files(self._get_uploaded_auth_files())
            return jsonify(result.to_dict())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error importing Codex OAuth auth files: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def import_claude_auth_files(self) -> ResponseReturnValue:
        try:
            result = self._claude_oauth_service.import_auth_files(self._get_uploaded_auth_files())
            return jsonify(result.to_dict())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error importing Claude OAuth auth files: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @staticmethod
    def _send_auth_file_export(content: bytes, filename: str) -> ResponseReturnValue:
        """发送认证文件 ZIP 导出响应。"""
        return send_file(
            io.BytesIO(content),
            mimetype="application/zip",
            as_attachment=True,
            download_name=filename,
            max_age=0,
        )

    @staticmethod
    def _get_export_auth_file_names(payload: dict[str, object]) -> object:
        """读取导出接口里的认证文件名列表。"""
        if "names" in payload:
            return payload.get("names")
        return payload.get("files")

    @staticmethod
    def _get_uploaded_auth_files() -> list[tuple[str, bytes]]:
        """读取导入接口上传的文件内容。"""
        uploaded_files = request.files.getlist("files")
        if not uploaded_files:
            raise ValueError("Please select at least one file")
        sources: list[tuple[str, bytes]] = []
        for uploaded_file in uploaded_files:
            filename = str(uploaded_file.filename or "").strip()
            if not filename:
                continue
            sources.append((filename, uploaded_file.read()))
        if not sources:
            raise ValueError("Please select at least one file")
        return sources

    def delete_codex_auth_file(self, name: str) -> ResponseReturnValue:
        try:
            return jsonify(self._codex_oauth_service.delete_auth_file(name))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error deleting Codex OAuth auth file: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def delete_claude_auth_file(self, name: str) -> ResponseReturnValue:
        try:
            return jsonify(self._claude_oauth_service.delete_auth_file(name))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error deleting Claude OAuth auth file: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def list_claude_models(self) -> ResponseReturnValue:
        try:
            return jsonify(self._claude_oauth_service.list_models())
        except Exception as exc:
            self._logger.error("Error listing Claude OAuth models: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def add_claude_model(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            model_id = str(payload.get("model_id") or payload.get("id") or "").strip()
            return jsonify(self._claude_oauth_service.add_model(model_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error adding Claude OAuth model: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def delete_claude_model(self, model_id: str) -> ResponseReturnValue:
        try:
            return jsonify(self._claude_oauth_service.delete_model(model_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error deleting Claude OAuth model: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def list_codex_models(self) -> ResponseReturnValue:
        try:
            return jsonify(self._codex_oauth_service.list_models())
        except Exception as exc:
            self._logger.error("Error listing Codex OAuth models: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def add_codex_model(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            model_id = str(payload.get("model_id") or payload.get("id") or "").strip()
            return jsonify(self._codex_oauth_service.add_model(model_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error adding Codex OAuth model: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def delete_codex_model(self, model_id: str) -> ResponseReturnValue:
        try:
            return jsonify(self._codex_oauth_service.delete_model(model_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error deleting Codex OAuth model: %s", exc)
            return jsonify({"error": str(exc)}), 500
