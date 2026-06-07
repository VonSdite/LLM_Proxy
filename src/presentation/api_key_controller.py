#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API Key 管理控制器。"""

from __future__ import annotations

from flask import jsonify, request
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..services import ApiKeyService, AuthenticationService
from .controller_utils import build_value_error_response, get_json_object
from .decorators import require_authentication


class ApiKeyController:
    """处理 API Key 管理相关接口。"""

    def __init__(self, ctx: AppContext, api_key_service: ApiKeyService, auth_service: AuthenticationService):
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._api_key_service = api_key_service
        self._auth_service = auth_service
        self._register_routes()

    def _register_routes(self) -> None:
        auth = require_authentication(self._auth_service)

        self._app.route("/api/api-keys", methods=["GET"])(auth(self.get_api_keys))
        self._app.route("/api/api-keys", methods=["POST"])(auth(self.create_api_key))
        self._app.route("/api/api-keys/<int:key_id>", methods=["GET"])(auth(self.get_api_key))
        self._app.route("/api/api-keys/<int:key_id>", methods=["PUT"])(auth(self.update_api_key))
        self._app.route("/api/api-keys/<int:key_id>", methods=["DELETE"])(auth(self.delete_api_key))
        self._app.route("/api/api-keys/<int:key_id>/toggle", methods=["POST"])(auth(self.toggle_api_key))

    def get_api_keys(self) -> ResponseReturnValue:
        try:
            page = request.args.get("page", 1, type=int)
            page_size = request.args.get("page_size", 50, type=int)
            keyword = (request.args.get("keyword", "", type=str) or "").strip()
            sort_key = (
                request.args.get("sort_key", "created_at", type=str) or "created_at"
            ).strip() or "created_at"
            sort_direction = (request.args.get("sort_direction", "desc", type=str) or "desc").strip() or "desc"
            self._logger.debug(
                "List API keys requested: page=%s, page_size=%s, keyword=%r, sort_key=%r, sort_direction=%r",
                page,
                page_size,
                keyword,
                sort_key,
                sort_direction,
            )

            api_keys = self._api_key_service.get_api_keys(
                page=page,
                page_size=page_size,
                keyword=keyword,
                sort_key=sort_key,
                sort_direction=sort_direction,
            )
            total = self._api_key_service.get_total_api_keys_count(keyword=keyword)

            return jsonify(
                {
                    "api_keys": api_keys,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": (total + page_size - 1) // page_size,
                    "keyword": keyword,
                    "sort_key": sort_key,
                    "sort_direction": sort_direction,
                    "available_models": self._api_key_service.get_available_models(),
                }
            )
        except Exception as exc:
            self._logger.error("Error getting API keys: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_api_key(self, key_id: int) -> ResponseReturnValue:
        try:
            api_key = self._api_key_service.get_api_key_by_id(key_id)
            if not api_key:
                return jsonify({"error": "API key not found"}), 404
            return jsonify(api_key)
        except Exception as exc:
            self._logger.error("Error getting API key: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def create_api_key(self) -> ResponseReturnValue:
        try:
            data = get_json_object()
            name = data.get("name")
            enabled = data.get("enabled", True)
            if not isinstance(enabled, bool):
                return jsonify({"error": "Enabled must be a boolean"}), 400
            model_permissions = data.get("model_permissions", self._api_key_service.MODEL_PERMISSIONS_ALL)
            api_key = self._api_key_service.create_api_key(
                name=name,
                model_permissions=model_permissions,
                token_limit_k=data.get("token_limit_k"),
                enabled=enabled,
            )
            return jsonify({"api_key": api_key, "message": "API key created successfully"}), 201
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error creating API key: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_api_key(self, key_id: int) -> ResponseReturnValue:
        try:
            data = get_json_object()
            if not data:
                self._logger.warning("Update API key rejected: no payload, key_id=%s", key_id)
                return jsonify({"error": "No data provided"}), 400

            name = data.get("name") if "name" in data else None
            if name is not None and not isinstance(name, str):
                return jsonify({"error": "Name must be a string"}), 400

            enabled = data.get("enabled") if "enabled" in data else None
            if enabled is not None and not isinstance(enabled, bool):
                return jsonify({"error": "Enabled must be a boolean"}), 400

            success = self._api_key_service.update_api_key(
                key_id,
                name=name,
                enabled=enabled,
                model_permissions_provided="model_permissions" in data,
                model_permissions=data.get("model_permissions"),
                token_limit_k_provided="token_limit_k" in data,
                token_limit_k=data.get("token_limit_k"),
            )
            if not success:
                self._logger.warning("Update API key failed: key_id=%s", key_id)
                return jsonify({"error": "Failed to update API key"}), 400

            self._logger.info("Update API key succeeded: key_id=%s", key_id)
            return jsonify({"message": "API key updated successfully"})
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error updating API key: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def delete_api_key(self, key_id: int) -> ResponseReturnValue:
        try:
            success = self._api_key_service.delete_api_key(key_id)
            if not success:
                return jsonify({"error": "Failed to delete API key"}), 400
            return jsonify({"message": "API key deleted successfully"})
        except Exception as exc:
            self._logger.error("Error deleting API key: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def toggle_api_key(self, key_id: int) -> ResponseReturnValue:
        try:
            success = self._api_key_service.toggle_api_key_status(key_id)
            if not success:
                return jsonify({"error": "Failed to toggle API key"}), 400
            return jsonify({"message": "API key status toggled successfully"})
        except Exception as exc:
            self._logger.error("Error toggling API key: %s", exc)
            return jsonify({"error": str(exc)}), 500
