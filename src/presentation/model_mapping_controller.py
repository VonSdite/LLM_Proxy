#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型映射管理控制器。"""

from __future__ import annotations

from collections.abc import Callable

from flask import jsonify
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..services import AuthenticationService, ModelMappingService
from .controller_utils import build_value_error_response, coerce_string_list, get_json_object
from .decorators import require_authentication


class ModelMappingController:
    """提供模型映射 CRUD、复制、目标启停和导入导出接口。"""

    def __init__(
        self,
        ctx: AppContext,
        service: ModelMappingService,
        auth_service: AuthenticationService,
        *,
        model_catalog_changed_callback: Callable[[], None] | None = None,
    ) -> None:
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._service = service
        self._auth_service = auth_service
        self._model_catalog_changed_callback = model_catalog_changed_callback
        self._register_routes()

    def _register_routes(self) -> None:
        auth = require_authentication(self._auth_service)
        self._app.route("/api/model-mappings", methods=["GET"])(auth(self.list_mappings))
        self._app.route("/api/model-mappings", methods=["POST"])(auth(self.create_mapping))
        self._app.route("/api/model-mappings/order", methods=["PUT"])(auth(self.reorder_mappings))
        self._app.route("/api/model-mappings/targets", methods=["GET"])(auth(self.list_targets))
        self._app.route("/api/model-mappings/export", methods=["POST"])(auth(self.export_mappings))
        self._app.route("/api/model-mappings/import", methods=["POST"])(auth(self.import_mappings))
        self._app.route("/api/model-mappings/<mapping_id>", methods=["GET"])(auth(self.get_mapping))
        self._app.route("/api/model-mappings/<mapping_id>", methods=["PUT"])(auth(self.update_mapping))
        self._app.route("/api/model-mappings/<mapping_id>", methods=["DELETE"])(auth(self.delete_mapping))
        self._app.route("/api/model-mappings/<mapping_id>/copy", methods=["POST"])(auth(self.copy_mapping))
        self._app.route("/api/model-mappings/<mapping_id>/disable", methods=["POST"])(auth(self.disable_mapping))
        self._app.route("/api/model-mappings/<mapping_id>/enable", methods=["POST"])(auth(self.enable_mapping))
        self._app.route("/api/model-mappings/<mapping_id>/targets/toggle", methods=["POST"])(auth(self.toggle_target))

    def list_mappings(self) -> ResponseReturnValue:
        try:
            return jsonify(
                {
                    "enabled": self._service.is_enabled(),
                    "model_mappings": self._service.list_mappings(),
                    "available_targets": self._service.list_available_target_model_ids(),
                }
            )
        except Exception as exc:
            self._logger.error("Error listing model mappings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def list_targets(self) -> ResponseReturnValue:
        try:
            return jsonify({"models": self._service.list_available_target_model_ids()})
        except Exception as exc:
            self._logger.error("Error listing model mapping targets: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def get_mapping(self, mapping_id: str) -> ResponseReturnValue:
        try:
            mapping = self._service.get_mapping(mapping_id)
            if mapping is None:
                return jsonify({"error": f"模型映射不存在: {mapping_id}"}), 404
            return jsonify(mapping)
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error getting model mapping: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def create_mapping(self) -> ResponseReturnValue:
        try:
            mapping = self._service.create_mapping(get_json_object())
            self._sync_model_catalog()
            self._logger.info("Model mapping created: id=%s", mapping["id"])
            return jsonify(mapping), 201
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error creating model mapping: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_mapping(self, mapping_id: str) -> ResponseReturnValue:
        try:
            mapping = self._service.update_mapping(mapping_id, get_json_object())
            self._sync_model_catalog()
            self._logger.info("Model mapping updated: id=%s", mapping["id"])
            return jsonify(mapping)
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error updating model mapping: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def delete_mapping(self, mapping_id: str) -> ResponseReturnValue:
        try:
            self._service.delete_mapping(mapping_id)
            self._sync_model_catalog()
            self._logger.info("Model mapping deleted: id=%s", mapping_id)
            return jsonify({"message": "模型映射已删除"})
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error deleting model mapping: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def copy_mapping(self, mapping_id: str) -> ResponseReturnValue:
        try:
            mapping = self._service.copy_mapping(mapping_id)
            self._sync_model_catalog()
            self._logger.info("Model mapping copied: %s -> %s", mapping_id, mapping["id"])
            return jsonify(mapping), 201
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error copying model mapping: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def reorder_mappings(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            result = self._service.reorder_mappings(
                coerce_string_list(
                    payload.get("ids"),
                    error_message="模型映射 ID 必须是非空数组",
                )
            )
            self._logger.info("Model mapping order updated: count=%s", result["count"])
            return jsonify(result)
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error reordering model mappings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def disable_mapping(self, mapping_id: str) -> ResponseReturnValue:
        return self._set_mapping_enabled(mapping_id, enabled=False)

    def enable_mapping(self, mapping_id: str) -> ResponseReturnValue:
        return self._set_mapping_enabled(mapping_id, enabled=True)

    def _set_mapping_enabled(self, mapping_id: str, *, enabled: bool) -> ResponseReturnValue:
        try:
            mapping = self._service.set_mapping_enabled(mapping_id, enabled=enabled)
            self._sync_model_catalog()
            self._logger.info("Model mapping status updated: id=%s enabled=%s", mapping_id, enabled)
            return jsonify(mapping)
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error updating model mapping status: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def toggle_target(self, mapping_id: str) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            model_id = str(payload.get("model_id") or "").strip()
            enabled = payload.get("enabled")
            if not model_id:
                raise ValueError("目标模型 ID 不能为空")
            if not isinstance(enabled, bool):
                raise ValueError("目标启用状态必须是布尔值")
            return jsonify(self._service.set_target_enabled(mapping_id, model_id, enabled=enabled))
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error toggling model mapping target: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def export_mappings(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            mapping_ids = payload.get("mapping_ids") if "mapping_ids" in payload else None
            if mapping_ids is not None and not isinstance(mapping_ids, list):
                raise ValueError("mapping_ids 必须是数组")
            return jsonify(self._service.export_mappings(mapping_ids))
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error exporting model mappings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def import_mappings(self) -> ResponseReturnValue:
        try:
            result = self._service.import_mappings(get_json_object())
            self._sync_model_catalog()
            self._logger.info("Model mappings imported: count=%s", result["count"])
            return jsonify(result), 201
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error("Error importing model mappings: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def _sync_model_catalog(self) -> None:
        if self._model_catalog_changed_callback is not None:
            self._model_catalog_changed_callback()
