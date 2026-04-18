#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 管理 API。"""

from __future__ import annotations

from typing import Any

from flask import jsonify, request
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..services import (
    AuthGroupService,
    AuthenticationService,
    ModelDiscoveryService,
    ProviderModelTestService,
    ProviderService,
    SettingsService,
)
from .controller_utils import build_value_error_response, coerce_string_list, get_json_object
from .decorators import require_authentication


class ProviderController:
    """处理 provider 配置、认证分组与相关管理 API。"""

    def __init__(
        self,
        ctx: AppContext,
        provider_service: ProviderService,
        provider_model_test_service: ProviderModelTestService,
        auth_group_service: AuthGroupService,
        model_discovery_service: ModelDiscoveryService,
        settings_service: SettingsService,
        auth_service: AuthenticationService,
    ):
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._provider_service = provider_service
        self._provider_model_test_service = provider_model_test_service
        self._auth_group_service = auth_group_service
        self._model_discovery_service = model_discovery_service
        self._settings_service = settings_service
        self._auth_service = auth_service
        self._register_routes()

    @staticmethod
    def _clean_optional_text(value: Any) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    def _resolve_auth_request_headers(
        self,
        *,
        api_key: Any,
        auth_group: Any,
        auth_entry_id: Any,
        action_label: str,
    ) -> tuple[str | None, dict[str, str] | None]:
        normalized_api_key = self._clean_optional_text(api_key)
        auth_group_name = self._clean_optional_text(auth_group)
        auth_entry_key = self._clean_optional_text(auth_entry_id)
        if normalized_api_key and auth_group_name:
            raise ValueError(f"{action_label} must use either auth_group or api_key, not both")
        if auth_entry_key and not auth_group_name:
            raise ValueError(f"{action_label} auth_entry_id requires auth_group")

        request_headers = None
        if auth_group_name:
            if not auth_entry_key:
                raise ValueError(f"{action_label} auth_group requires auth_entry_id")
            request_headers = self._auth_group_service.get_entry_headers(auth_group_name, auth_entry_key)

        return normalized_api_key, request_headers

    def _register_routes(self) -> None:
        auth = require_authentication(self._auth_service)

        self._app.route('/api/providers', methods=['GET'])(auth(self.get_providers))
        self._app.route('/api/providers', methods=['POST'])(auth(self.create_provider))
        self._app.route('/api/providers/batch', methods=['POST'])(auth(self.batch_providers))
        self._app.route('/api/providers/order', methods=['PUT'])(auth(self.reorder_providers))
        self._app.route('/api/providers/fetch-models', methods=['GET'])(auth(self.fetch_models))
        self._app.route('/api/providers/test-models', methods=['POST'])(auth(self.test_models))
        self._app.route('/api/providers/chat-whitelist', methods=['PUT'])(auth(self.update_chat_whitelist))
        self._app.route('/api/providers/<string:name>', methods=['GET'])(auth(self.get_provider))
        self._app.route('/api/providers/<string:name>', methods=['PUT'])(auth(self.update_provider))
        self._app.route('/api/providers/<string:name>', methods=['DELETE'])(auth(self.delete_provider))
        self._app.route('/api/providers/<string:name>/disable', methods=['POST'])(auth(self.disable_provider))
        self._app.route('/api/providers/<string:name>/enable', methods=['POST'])(auth(self.enable_provider))
        self._app.route('/api/auth-groups', methods=['GET'])(auth(self.get_auth_groups))
        self._app.route('/api/auth-groups', methods=['POST'])(auth(self.create_auth_group))
        self._app.route('/api/auth-groups/import-entries', methods=['POST'])(auth(self.import_auth_group_entries))
        self._app.route('/api/auth-groups/<string:name>', methods=['GET'])(auth(self.get_auth_group))
        self._app.route('/api/auth-groups/<string:name>', methods=['PUT'])(auth(self.update_auth_group))
        self._app.route('/api/auth-groups/<string:name>', methods=['DELETE'])(auth(self.delete_auth_group))
        self._app.route('/api/auth-groups/<string:name>/runtime', methods=['GET'])(auth(self.get_auth_group_runtime))
        self._app.route('/api/auth-groups/<string:name>/entries/<string:entry_id>/clear-cooldown', methods=['POST'])(
            auth(self.clear_auth_group_entry_cooldown)
        )
        self._app.route('/api/auth-groups/<string:name>/entries/<string:entry_id>/disable', methods=['POST'])(
            auth(self.disable_auth_group_entry)
        )
        self._app.route('/api/auth-groups/<string:name>/entries/<string:entry_id>/enable', methods=['POST'])(
            auth(self.enable_auth_group_entry)
        )
        self._app.route('/api/auth-groups/<string:name>/entries/<string:entry_id>/reset-minute-usage', methods=['POST'])(
            auth(self.reset_auth_group_entry_minute_usage)
        )
        self._app.route('/api/auth-groups/<string:name>/entries/<string:entry_id>/reset', methods=['POST'])(
            auth(self.reset_auth_group_entry_runtime)
        )
        self._app.route('/api/auth-groups/<string:name>/entries/<string:entry_id>/restore', methods=['POST'])(
            auth(self.restore_auth_group_entry)
        )

    def get_providers(self) -> ResponseReturnValue:
        try:
            return jsonify(self._provider_service.list_providers())
        except Exception as exc:
            self._logger.error('Error getting providers: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def get_provider(self, name: str) -> ResponseReturnValue:
        try:
            provider = self._provider_service.get_provider(name)
            if provider is None:
                return jsonify({'error': 'Provider not found'}), 404
            return jsonify(provider)
        except Exception as exc:
            self._logger.error('Error getting provider: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def create_provider(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            provider = self._provider_service.create_provider(payload)
            self._logger.info('Provider created: %s', provider.get('name'))
            return jsonify(provider), 201
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            self._logger.error('Error creating provider: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def reorder_providers(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            result = self._provider_service.reorder_providers(
                coerce_string_list(
                    payload.get('names'),
                    error_message='Provider names must be a non-empty list',
                )
            )
            self._logger.info(
                'Provider order updated: count=%s', result.get('count', 0)
            )
            return jsonify(result)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            self._logger.error('Error reordering providers: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def update_provider(self, name: str) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            provider = self._provider_service.update_provider(name, payload)
            self._logger.info('Provider updated: %s -> %s', name, provider.get('name'))
            return jsonify(provider)
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error updating provider: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def delete_provider(self, name: str) -> ResponseReturnValue:
        try:
            self._provider_service.delete_provider(name)
            self._logger.info('Provider deleted: %s', name)
            return jsonify({'message': 'Provider deleted successfully'})
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error deleting provider: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def disable_provider(self, name: str) -> ResponseReturnValue:
        try:
            provider = self._provider_service.set_provider_enabled(name, enabled=False)
            self._logger.info('Provider disabled: %s', name)
            return jsonify(provider)
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error disabling provider: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def enable_provider(self, name: str) -> ResponseReturnValue:
        try:
            provider = self._provider_service.set_provider_enabled(name, enabled=True)
            self._logger.info('Provider enabled: %s', name)
            return jsonify(provider)
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error enabling provider: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def batch_providers(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            action = str(payload.get('action') or '').strip().lower()
            provider_names = coerce_string_list(
                payload.get('names'),
                error_message='Provider names must be a non-empty list',
            )
            if action == 'enable':
                result = self._provider_service.batch_set_provider_enabled(provider_names, enabled=True)
            elif action == 'disable':
                result = self._provider_service.batch_set_provider_enabled(provider_names, enabled=False)
            elif action == 'delete':
                result = self._provider_service.batch_delete_providers(provider_names)
            else:
                raise ValueError(f'Unsupported provider batch action: {action or "<empty>"}')

            self._logger.info('Provider batch action completed: action=%s count=%s', action, result.get('count', 0))
            return jsonify(result)
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error applying provider batch action: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def get_auth_groups(self) -> ResponseReturnValue:
        try:
            return jsonify(self._auth_group_service.list_auth_groups())
        except Exception as exc:
            self._logger.error('Error getting auth groups: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def get_auth_group(self, name: str) -> ResponseReturnValue:
        try:
            auth_group = self._auth_group_service.get_auth_group(name)
            if auth_group is None:
                return jsonify({'error': 'Auth group not found'}), 404
            return jsonify(auth_group)
        except Exception as exc:
            self._logger.error('Error getting auth group: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def create_auth_group(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            auth_group = self._auth_group_service.create_auth_group(payload)
            self._logger.info('Auth group created: %s', auth_group.get('name'))
            return jsonify(auth_group), 201
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            self._logger.error('Error creating auth group: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def import_auth_group_entries(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            yaml_text = str(payload.get('yaml', '') or '')
            entries = self._auth_group_service.import_auth_entries(yaml_text)
            return jsonify({'entries': entries})
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            self._logger.error('Error importing auth group entries: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def update_auth_group(self, name: str) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            auth_group = self._auth_group_service.update_auth_group(name, payload)
            self._logger.info('Auth group updated: %s -> %s', name, auth_group.get('name'))
            return jsonify(auth_group)
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error updating auth group: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def delete_auth_group(self, name: str) -> ResponseReturnValue:
        try:
            self._auth_group_service.delete_auth_group(name)
            self._logger.info('Auth group deleted: %s', name)
            return jsonify({'message': 'Auth group deleted successfully'})
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error deleting auth group: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def get_auth_group_runtime(self, name: str) -> ResponseReturnValue:
        try:
            return jsonify(self._auth_group_service.get_auth_group_runtime(name))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 404
        except Exception as exc:
            self._logger.error('Error getting auth group runtime: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def clear_auth_group_entry_cooldown(self, name: str, entry_id: str) -> ResponseReturnValue:
        try:
            self._auth_group_service.clear_entry_cooldown(name, entry_id)
            self._logger.info('Auth group entry cooldown cleared: %s/%s', name, entry_id)
            return jsonify({'message': 'Auth entry cooldown cleared successfully'})
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error clearing auth group entry cooldown: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def disable_auth_group_entry(self, name: str, entry_id: str) -> ResponseReturnValue:
        try:
            self._auth_group_service.set_entry_disabled(name, entry_id, disabled=True)
            self._logger.info('Auth group entry disabled: %s/%s', name, entry_id)
            return jsonify({'message': 'Auth entry disabled successfully'})
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error disabling auth group entry: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def enable_auth_group_entry(self, name: str, entry_id: str) -> ResponseReturnValue:
        try:
            self._auth_group_service.set_entry_disabled(name, entry_id, disabled=False)
            self._logger.info('Auth group entry enabled: %s/%s', name, entry_id)
            return jsonify({'message': 'Auth entry enabled successfully'})
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error enabling auth group entry: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def reset_auth_group_entry_minute_usage(self, name: str, entry_id: str) -> ResponseReturnValue:
        try:
            self._auth_group_service.reset_entry_minute_usage(name, entry_id)
            self._logger.info('Auth group entry minute usage reset: %s/%s', name, entry_id)
            return jsonify({'message': 'Auth entry minute usage reset successfully'})
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error resetting auth group entry minute usage: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def reset_auth_group_entry_runtime(self, name: str, entry_id: str) -> ResponseReturnValue:
        try:
            self._auth_group_service.reset_entry_runtime(name, entry_id)
            self._logger.info('Auth group entry runtime reset: %s/%s', name, entry_id)
            return jsonify({'message': 'Auth entry runtime reset successfully'})
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error resetting auth group entry runtime: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def restore_auth_group_entry(self, name: str, entry_id: str) -> ResponseReturnValue:
        try:
            self._auth_group_service.restore_entry(name, entry_id)
            self._logger.info('Auth group entry restored: %s/%s', name, entry_id)
            return jsonify({'message': 'Auth entry restored successfully'})
        except ValueError as exc:
            return build_value_error_response(exc)
        except Exception as exc:
            self._logger.error('Error restoring auth group entry: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def fetch_models(self) -> ResponseReturnValue:
        try:
            api_key, request_headers = self._resolve_auth_request_headers(
                api_key=request.args.get('api_key'),
                auth_group=request.args.get('auth_group'),
                auth_entry_id=request.args.get('auth_entry_id'),
                action_label='Model fetch',
            )

            result = self._model_discovery_service.fetch_models_preview(
                api=request.args.get('api', ''),
                api_key=api_key,
                request_headers=request_headers,
                proxy=request.args.get('proxy'),
                timeout_seconds=request.args.get('timeout_seconds'),
                verify_ssl=request.args.get('verify_ssl'),
            )
            self._logger.info(
                'Provider models preview fetched: api=%s fetched=%s',
                request.args.get('api', ''),
                len(result['fetched_models']),
            )
            return jsonify(result)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            self._logger.error('Error fetching provider models: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def test_models(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            api_key, request_headers = self._resolve_auth_request_headers(
                api_key=payload.get("api_key"),
                auth_group=payload.get("auth_group"),
                auth_entry_id=payload.get("auth_entry_id"),
                action_label="Model test",
            )

            normalized_payload = dict(payload)
            normalized_payload["api_key"] = api_key
            normalized_payload["models"] = coerce_string_list(
                payload.get("models"),
                error_message="Model test models must be a non-empty list",
            )
            result = self._provider_model_test_service.test_models(
                normalized_payload,
                request_headers=request_headers,
            )
            self._logger.info(
                "Provider models tested: provider=%s models=%s",
                normalized_payload.get("name") or "<unsaved>",
                len(result.get("results", [])),
            )
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            self._logger.error("Error testing provider models: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def update_chat_whitelist(self) -> ResponseReturnValue:
        try:
            payload = get_json_object()
            enabled = self._settings_service.update_chat_whitelist_enabled(payload.get('enabled'))
            self._logger.info('Chat whitelist updated: enabled=%s', enabled)
            return jsonify({'enabled': enabled})
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            self._logger.error('Error updating chat whitelist: %s', exc)
            return jsonify({'error': str(exc)}), 500
