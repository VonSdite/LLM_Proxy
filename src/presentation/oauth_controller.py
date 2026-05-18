#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OAuth 管理接口控制器。"""

from __future__ import annotations

from flask import jsonify
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..services import AuthenticationService, CodexOAuthService
from .controller_utils import get_json_object
from .decorators import require_authentication


class OAuthController:
    """注册 OAuth 管理 API。"""

    def __init__(
        self,
        ctx: AppContext,
        codex_oauth_service: CodexOAuthService,
        auth_service: AuthenticationService,
    ):
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._codex_oauth_service = codex_oauth_service
        self._auth_service = auth_service
        self._register_routes()

    def _register_routes(self) -> None:
        auth = require_authentication(self._auth_service)
        self._app.route("/api/oauth/codex/session", methods=["POST"])(auth(self.create_codex_session))
        self._app.route("/api/oauth/codex/callback", methods=["POST"])(auth(self.complete_codex_callback))
        self._app.route("/api/oauth/codex/auth-files", methods=["GET"])(auth(self.list_codex_auth_files))
        self._app.route("/api/oauth/codex/auth-files/<name>", methods=["DELETE"])(auth(self.delete_codex_auth_file))
        self._app.route("/api/oauth/codex/auth-files/<name>/quota", methods=["GET"])(
            auth(self.get_codex_auth_file_quota)
        )
        self._app.route("/api/oauth/codex/models", methods=["GET"])(auth(self.list_codex_models))
        self._app.route("/api/oauth/codex/models", methods=["POST"])(auth(self.add_codex_model))
        self._app.route("/api/oauth/codex/models/<path:model_id>", methods=["DELETE"])(auth(self.delete_codex_model))

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

    def list_codex_auth_files(self) -> ResponseReturnValue:
        try:
            return jsonify(self._codex_oauth_service.list_auth_files())
        except Exception as exc:
            self._logger.error("Error listing Codex OAuth auth files: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_codex_auth_file_quota(self, name: str) -> ResponseReturnValue:
        try:
            return jsonify(self._codex_oauth_service.get_auth_file_quota(name))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error fetching Codex OAuth quota: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def delete_codex_auth_file(self, name: str) -> ResponseReturnValue:
        try:
            return jsonify(self._codex_oauth_service.delete_auth_file(name))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error deleting Codex OAuth auth file: %s", exc)
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
