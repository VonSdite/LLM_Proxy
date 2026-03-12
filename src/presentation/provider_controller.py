#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 管理 API。"""

from flask import Response, jsonify, request

from ..application.app_context import AppContext
from ..services import (
    AuthenticationService,
    ModelDiscoveryService,
    ProviderService,
    SettingsService,
)
from .decorators import require_authentication


class ProviderController:
    """处理 provider 配置的增删改查、模型拉取与配置开关更新。"""

    def __init__(
        self,
        ctx: AppContext,
        provider_service: ProviderService,
        model_discovery_service: ModelDiscoveryService,
        settings_service: SettingsService,
        auth_service: AuthenticationService,
    ):
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._provider_service = provider_service
        self._model_discovery_service = model_discovery_service
        self._settings_service = settings_service
        self._auth_service = auth_service
        self._register_routes()

    def _register_routes(self) -> None:
        auth = require_authentication(self._auth_service)

        self._app.route('/api/providers', methods=['GET'])(auth(self.get_providers))
        self._app.route('/api/providers', methods=['POST'])(auth(self.create_provider))
        self._app.route('/api/providers/fetch-models', methods=['GET'])(auth(self.fetch_models))
        self._app.route('/api/providers/chat-whitelist', methods=['PUT'])(auth(self.update_chat_whitelist))
        self._app.route('/api/providers/<string:name>', methods=['GET'])(auth(self.get_provider))
        self._app.route('/api/providers/<string:name>', methods=['PUT'])(auth(self.update_provider))
        self._app.route('/api/providers/<string:name>', methods=['DELETE'])(auth(self.delete_provider))

    def get_providers(self) -> Response:
        try:
            return jsonify(self._provider_service.list_providers())
        except Exception as exc:
            self._logger.error('Error getting providers: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def get_provider(self, name: str) -> Response:
        try:
            provider = self._provider_service.get_provider(name)
            if provider is None:
                return jsonify({'error': 'Provider not found'}), 404
            return jsonify(provider)
        except Exception as exc:
            self._logger.error('Error getting provider: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def create_provider(self) -> Response:
        try:
            payload = request.get_json(silent=True) or {}
            provider = self._provider_service.create_provider(payload)
            self._logger.info('Provider created: %s', provider.get('name'))
            return jsonify(provider), 201
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            self._logger.error('Error creating provider: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def update_provider(self, name: str) -> Response:
        try:
            payload = request.get_json(silent=True) or {}
            provider = self._provider_service.update_provider(name, payload)
            self._logger.info('Provider updated: %s -> %s', name, provider.get('name'))
            return jsonify(provider)
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if 'not found' in message.lower() else 400
            return jsonify({'error': message}), status_code
        except Exception as exc:
            self._logger.error('Error updating provider: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def delete_provider(self, name: str) -> Response:
        try:
            self._provider_service.delete_provider(name)
            self._logger.info('Provider deleted: %s', name)
            return jsonify({'message': 'Provider deleted successfully'})
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if 'not found' in message.lower() else 400
            return jsonify({'error': message}), status_code
        except Exception as exc:
            self._logger.error('Error deleting provider: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def fetch_models(self) -> Response:
        try:
            result = self._model_discovery_service.fetch_models_preview(
                api=request.args.get('api', ''),
                api_key=request.args.get('api_key'),
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

    def update_chat_whitelist(self) -> Response:
        try:
            payload = request.get_json(silent=True) or {}
            enabled = self._settings_service.update_chat_whitelist_enabled(payload.get('enabled'))
            self._logger.info('Chat whitelist updated: enabled=%s', enabled)
            return jsonify({'enabled': enabled})
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            self._logger.error('Error updating chat whitelist: %s', exc)
            return jsonify({'error': str(exc)}), 500
