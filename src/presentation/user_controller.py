#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户控制器。"""

from typing import Any

from flask import jsonify, request
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..services import AuthenticationService, UserService
from ..utils import is_valid_ip, normalize_ip
from .decorators import require_authentication


class UserController:
    """处理用户管理 API。"""

    def __init__(self, ctx: AppContext, user_service: UserService, auth_service: AuthenticationService):
        self._ctx = ctx
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._config_manager = ctx.config_manager
        self._user_service = user_service
        self._auth_service = auth_service
        self._register_routes()

    @staticmethod
    def _get_request_payload() -> dict[str, Any]:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            return dict(payload)
        return {}

    def _register_routes(self) -> None:
        auth = require_authentication(self._auth_service)

        self._app.route('/api/users', methods=['GET'])(auth(self.get_users))
        self._app.route('/api/users', methods=['POST'])(auth(self.create_user))
        self._app.route('/api/users/batch', methods=['POST'])(auth(self.batch_users))
        self._app.route('/api/users/<int:user_id>', methods=['GET'])(auth(self.get_user))
        self._app.route('/api/users/<int:user_id>', methods=['PUT'])(auth(self.update_user))
        self._app.route('/api/users/<int:user_id>', methods=['DELETE'])(auth(self.delete_user))
        self._app.route('/api/users/<int:user_id>/toggle', methods=['POST'])(auth(self.toggle_user))

    def get_users(self) -> ResponseReturnValue:
        try:
            page = request.args.get('page', 1, type=int)
            page_size = request.args.get('page_size', 50, type=int)
            keyword = (request.args.get('keyword', '', type=str) or '').strip()
            self._logger.debug(
                'List users requested: page=%s, page_size=%s, keyword=%r',
                page,
                page_size,
                keyword,
            )

            users = self._user_service.get_users(page=page, page_size=page_size, keyword=keyword)
            total = self._user_service.get_total_users_count(keyword=keyword)

            return jsonify(
                {
                    'users': users,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total + page_size - 1) // page_size,
                    'keyword': keyword,
                    'available_models': self._user_service.get_available_models(),
                }
            )
        except Exception as exc:
            self._logger.error('Error getting users: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def get_user(self, user_id: int) -> ResponseReturnValue:
        try:
            user = self._user_service.get_user_by_id(user_id)
            if not user:
                self._logger.warning('Get user failed: user_id=%s not found', user_id)
                return jsonify({'error': 'User not found'}), 404
            return jsonify(user)
        except Exception as exc:
            self._logger.error('Error getting user: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def create_user(self) -> ResponseReturnValue:
        try:
            data = self._get_request_payload()
            username = data.get('username')
            ip_address = data.get('ip_address')

            if not isinstance(username, str) or not username.strip():
                self._logger.warning('Create user rejected: username is empty')
                return jsonify({'error': 'Username is required'}), 400
            if not isinstance(ip_address, str) or not ip_address.strip():
                self._logger.warning('Create user rejected: ip_address is empty')
                return jsonify({'error': 'IP address is required'}), 400

            normalized_username = username.strip()
            normalized_ip = normalize_ip(ip_address)
            if not is_valid_ip(normalized_ip):
                self._logger.warning('Create user rejected: invalid ip_address=%r', ip_address)
                return jsonify({'error': 'Invalid IP address'}), 400

            user_id = self._user_service.create_user(normalized_username, normalized_ip)
            if not user_id:
                self._logger.warning('Create user failed: username=%r, ip=%s', normalized_username, normalized_ip)
                return jsonify({'error': 'Failed to create user or IP already exists'}), 400

            self._logger.info(
                'Create user succeeded: user_id=%s, username=%r, ip=%s',
                user_id,
                normalized_username,
                normalized_ip,
            )
            return jsonify({'id': user_id, 'message': 'User created successfully'}), 201
        except Exception as exc:
            self._logger.error('Error creating user: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def update_user(self, user_id: int) -> ResponseReturnValue:
        try:
            data = self._get_request_payload()
            if not data:
                self._logger.warning('Update user rejected: no payload, user_id=%s', user_id)
                return jsonify({'error': 'No data provided'}), 400

            username_value = data.get('username')
            if username_value is not None and not isinstance(username_value, str):
                return jsonify({'error': 'Username must be a string'}), 400
            username = username_value

            ip_address_value = data.get('ip_address')
            if ip_address_value is not None and not isinstance(ip_address_value, str):
                return jsonify({'error': 'IP address must be a string'}), 400
            ip_address = ip_address_value

            whitelist_access_enabled = data.get('whitelist_access_enabled')
            model_permissions_provided = 'model_permissions' in data
            model_permissions = data.get('model_permissions')

            normalized_ip = None
            if ip_address is not None:
                normalized_ip = normalize_ip(ip_address)
                if not is_valid_ip(normalized_ip):
                    self._logger.warning('Update user rejected: invalid ip=%r, user_id=%s', ip_address, user_id)
                    return jsonify({'error': 'Invalid IP address'}), 400

            success = self._user_service.update_user(
                user_id,
                username,
                normalized_ip,
                whitelist_access_enabled,
                model_permissions_provided=model_permissions_provided,
                model_permissions=model_permissions,
            )
            if not success:
                self._logger.warning('Update user failed: user_id=%s', user_id)
                return jsonify({'error': 'Failed to update user'}), 400

            self._logger.info('Update user succeeded: user_id=%s', user_id)
            return jsonify({'message': 'User updated successfully'})
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:
            self._logger.error('Error updating user: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def batch_users(self) -> ResponseReturnValue:
        try:
            payload = self._get_request_payload()
            action = str(payload.get('action') or '').strip().lower()
            if action != 'set_model_permissions':
                raise ValueError(f'Unsupported user batch action: {action or "<empty>"}')

            result = self._user_service.batch_update_model_permissions(
                payload.get('user_ids'),
                payload.get('model_permissions'),
            )
            self._logger.info(
                'User batch action completed: action=%s count=%s',
                action,
                result.get('count', 0),
            )
            return jsonify(result)
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if 'not found' in message.lower() else 400
            return jsonify({'error': message}), status_code
        except Exception as exc:
            self._logger.error('Error applying user batch action: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def delete_user(self, user_id: int) -> ResponseReturnValue:
        try:
            if not self._user_service.delete_user(user_id):
                self._logger.warning('Delete user failed: user_id=%s', user_id)
                return jsonify({'error': 'Failed to delete user'}), 400
            self._logger.info('Delete user succeeded: user_id=%s', user_id)
            return jsonify({'message': 'User deleted successfully'})
        except Exception as exc:
            self._logger.error('Error deleting user: %s', exc)
            return jsonify({'error': str(exc)}), 500

    def toggle_user(self, user_id: int) -> ResponseReturnValue:
        try:
            if not self._config_manager.is_chat_whitelist_enabled():
                return jsonify({'error': 'Whitelist control is disabled'}), 400
            if not self._user_service.toggle_user_status(user_id):
                self._logger.warning('Toggle user whitelist failed: user_id=%s', user_id)
                return jsonify({'error': 'Failed to toggle user status'}), 400
            self._logger.info('Toggle user whitelist succeeded: user_id=%s', user_id)
            return jsonify({'message': 'User status toggled successfully'})
        except Exception as exc:
            self._logger.error('Error toggling user status: %s', exc)
            return jsonify({'error': str(exc)}), 500
