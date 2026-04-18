#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""页面与统计控制器。"""

from __future__ import annotations

from typing import Any, Callable

from flask import jsonify, make_response, render_template, request
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..services import AuthenticationService, LogService, SettingsService
from .controller_utils import require_json_object
from .decorators import require_authentication


class WebController:
    """处理页面渲染与统计相关 API。"""

    def __init__(
        self,
        ctx: AppContext,
        log_service: LogService,
        settings_service: SettingsService,
        auth_service: AuthenticationService,
    ):
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._config_manager = ctx.config_manager
        self._log_service = log_service
        self._settings_service = settings_service
        self._auth_service = auth_service
        self._register_routes()

    def _register_routes(self) -> None:
        auth = require_authentication(self._auth_service)

        self._app.route("/")(auth(self.home))
        self._app.route("/providers")(auth(self.providers_page))
        self._app.route("/users")(auth(self.users_page))
        self._app.route("/statistics")(auth(self.statistics_page))
        self._app.route("/settings")(auth(self.settings_page))

        self._app.route("/api/statistics", methods=["GET"])(auth(self.get_statistics))
        self._app.route("/api/request-logs", methods=["GET"])(
            auth(self.get_request_logs)
        )
        self._app.route("/api/usernames", methods=["GET"])(auth(self.get_usernames))
        self._app.route("/api/request-models", methods=["GET"])(
            auth(self.get_request_models)
        )
        self._app.route("/api/settings/system", methods=["GET"])(
            auth(self.get_system_settings)
        )
        self._app.route("/api/settings/system", methods=["PUT"])(
            auth(self.update_system_settings)
        )
        self._app.route("/api/settings/system/basic", methods=["PUT"])(
            auth(self.update_basic_settings)
        )
        self._app.route("/api/settings/system/debug", methods=["PUT"])(
            auth(self.update_debug_settings)
        )

    def home(self) -> str:
        return self.providers_page()

    def statistics_page(self) -> str:
        return render_template(
            "index.html",
            active_page="index",
            current_username=self._get_current_username(),
            auth_enabled=self._auth_service.is_auth_enabled(),
        )

    def users_page(self) -> str:
        return render_template(
            "users.html",
            active_page="users",
            chat_whitelist_enabled=self._config_manager.is_chat_whitelist_enabled(),
            current_username=self._get_current_username(),
            auth_enabled=self._auth_service.is_auth_enabled(),
        )

    def providers_page(self) -> str:
        return render_template(
            "providers.html",
            active_page="providers",
            chat_whitelist_enabled=self._config_manager.is_chat_whitelist_enabled(),
            current_username=self._get_current_username(),
            auth_enabled=self._auth_service.is_auth_enabled(),
        )

    def settings_page(self) -> str:
        return render_template(
            "settings.html",
            active_page="settings",
            current_username=self._get_current_username(),
            auth_enabled=self._auth_service.is_auth_enabled(),
        )

    def _get_current_username(self) -> str:
        if not self._auth_service.is_auth_enabled():
            return ""

        session_token = request.cookies.get("session_token")
        return self._auth_service.get_session_username(session_token) or ""

    @staticmethod
    def _get_multi_filter_values(name: str) -> list[str]:
        return [
            value.strip()
            for value in request.args.getlist(name)
            if isinstance(value, str) and value.strip()
        ]

    def get_statistics(self) -> ResponseReturnValue:
        try:
            usernames = self._get_multi_filter_values("username")
            request_models = self._get_multi_filter_values("request_model")
            self._logger.debug(
                "Statistics queried: start_date=%s end_date=%s usernames=%s request_models=%s",
                request.args.get("start_date"),
                request.args.get("end_date"),
                usernames,
                request_models,
            )
            stats = self._log_service.get_statistics(
                request.args.get("start_date"),
                request.args.get("end_date"),
                usernames or None,
                request_models or None,
            )
            return jsonify(stats)
        except Exception as exc:
            self._logger.error("Error getting statistics: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_request_logs(self) -> ResponseReturnValue:
        try:
            page = max(int(request.args.get("page", 1)), 1)
            page_size = min(max(int(request.args.get("page_size", 50)), 1), 200)
            usernames = self._get_multi_filter_values("username")
            request_models = self._get_multi_filter_values("request_model")
            self._logger.debug(
                "Request logs queried: page=%s, page_size=%s", page, page_size
            )

            logs = self._log_service.get_request_logs(
                page,
                page_size,
                request.args.get("start_date"),
                request.args.get("end_date"),
                usernames or None,
                request_models or None,
            )
            return jsonify(logs)
        except ValueError:
            return jsonify({"error": "page and page_size must be integers"}), 400
        except Exception as exc:
            self._logger.error("Error getting request logs: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_usernames(self) -> ResponseReturnValue:
        try:
            usernames = self._log_service.get_unique_usernames()
            self._logger.debug("Usernames queried: count=%s", len(usernames))
            return jsonify(usernames)
        except Exception as exc:
            self._logger.error("Error getting usernames: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_request_models(self) -> ResponseReturnValue:
        try:
            models = self._log_service.get_unique_request_models()
            self._logger.debug("Request models queried: count=%s", len(models))
            return jsonify(models)
        except Exception as exc:
            self._logger.error("Error getting request models: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_system_settings(self) -> ResponseReturnValue:
        try:
            settings = self._settings_service.get_system_settings()
            return jsonify(settings)
        except Exception as exc:
            self._logger.error("Error getting system settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def _apply_settings_update(
        self,
        update_func: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> ResponseReturnValue:
        payload = require_json_object()
        result = update_func(payload)
        if result.get("auth_config_changed"):
            self._auth_service.clear_sessions()

        response = make_response(jsonify(result))
        if result.get("auth_config_changed"):
            response.delete_cookie("session_token")
        return response

    def update_system_settings(self) -> ResponseReturnValue:
        try:
            return self._apply_settings_update(self._settings_service.update_system_settings)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating system settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_basic_settings(self) -> ResponseReturnValue:
        try:
            return self._apply_settings_update(self._settings_service.update_basic_settings)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating basic settings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_debug_settings(self) -> ResponseReturnValue:
        try:
            payload = require_json_object()
            return jsonify(self._settings_service.update_debug_settings(payload))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error updating debug settings: %s", exc)
            return jsonify({"error": str(exc)}), 500
