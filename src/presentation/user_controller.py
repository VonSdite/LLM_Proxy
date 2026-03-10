#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户控制器。"""

from flask import Response, jsonify, request

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

    def _register_routes(self) -> None:
        """注册用户管理路由。"""
        auth = require_authentication(self._auth_service)

        self._app.route('/api/users', methods=['GET'])(auth(self.get_users))
        self._app.route('/api/users', methods=['POST'])(auth(self.create_user))
        self._app.route('/api/users/<int:user_id>', methods=['GET'])(auth(self.get_user))
        self._app.route('/api/users/<int:user_id>', methods=['PUT'])(auth(self.update_user))
        self._app.route('/api/users/<int:user_id>', methods=['DELETE'])(auth(self.delete_user))
        self._app.route('/api/users/<int:user_id>/toggle', methods=['POST'])(auth(self.toggle_user))

    def get_users(self) -> Response:
        """分页查询用户列表。"""
        try:
            page = request.args.get('page', 1, type=int)
            page_size = request.args.get('page_size', 50, type=int)
            self._logger.info(f"List users requested: page={page}, page_size={page_size}")

            users = self._user_service.get_users(page=page, page_size=page_size)
            total = self._user_service.get_total_users_count()

            return jsonify({
                'users': users,
                'total': total,
                'page': page,
                'page_size': page_size,
                'total_pages': (total + page_size - 1) // page_size
            })
        except Exception as exc:
            self._logger.error(f'Error getting users: {exc}')
            return jsonify({'error': str(exc)}), 500

    def get_user(self, user_id: int) -> Response:
        """查询单个用户。"""
        try:
            user = self._user_service.get_user_by_id(user_id)
            if not user:
                self._logger.warning(f"Get user failed: user_id={user_id} not found")
                return jsonify({'error': 'User not found'}), 404
            return jsonify(user)
        except Exception as exc:
            self._logger.error(f'Error getting user: {exc}')
            return jsonify({'error': str(exc)}), 500

    def create_user(self) -> Response:
        """创建用户。"""
        try:
            data = request.get_json(silent=True) or {}
            username = data.get('username')
            ip_address = data.get('ip_address')

            if not username:
                self._logger.warning("Create user rejected: username is empty")
                return jsonify({'error': 'Username is required'}), 400
            if not ip_address:
                self._logger.warning("Create user rejected: ip_address is empty")
                return jsonify({'error': 'IP address is required'}), 400

            normalized_ip = normalize_ip(ip_address)
            if not is_valid_ip(normalized_ip):
                self._logger.warning(f"Create user rejected: invalid ip_address={ip_address!r}")
                return jsonify({'error': 'Invalid IP address'}), 400

            user_id = self._user_service.create_user(username, normalized_ip)
            if not user_id:
                self._logger.warning(f"Create user failed: username={username!r}, ip={normalized_ip}")
                return jsonify({'error': 'Failed to create user or IP already exists'}), 400

            self._logger.info(f"Create user succeeded: user_id={user_id}, username={username!r}, ip={normalized_ip}")
            return jsonify({'id': user_id, 'message': 'User created successfully'}), 201
        except Exception as exc:
            self._logger.error(f'Error creating user: {exc}')
            return jsonify({'error': str(exc)}), 500

    def update_user(self, user_id: int) -> Response:
        """更新用户信息。"""
        try:
            data = request.get_json(silent=True)
            if not data:
                self._logger.warning(f"Update user rejected: no payload, user_id={user_id}")
                return jsonify({'error': 'No data provided'}), 400

            username = data.get('username')
            ip_address = data.get('ip_address')
            whitelist_access_enabled = data.get('whitelist_access_enabled')

            normalized_ip = None
            if ip_address is not None:
                normalized_ip = normalize_ip(ip_address)
                if not is_valid_ip(normalized_ip):
                    self._logger.warning(f"Update user rejected: invalid ip={ip_address!r}, user_id={user_id}")
                    return jsonify({'error': 'Invalid IP address'}), 400

            success = self._user_service.update_user(
                user_id,
                username,
                normalized_ip,
                whitelist_access_enabled,
            )
            if not success:
                self._logger.warning(f"Update user failed: user_id={user_id}")
                return jsonify({'error': 'Failed to update user'}), 400

            self._logger.info(f"Update user succeeded: user_id={user_id}")
            return jsonify({'message': 'User updated successfully'})
        except Exception as exc:
            self._logger.error(f'Error updating user: {exc}')
            return jsonify({'error': str(exc)}), 500

    def delete_user(self, user_id: int) -> Response:
        """删除用户。"""
        try:
            if not self._user_service.delete_user(user_id):
                self._logger.warning(f"Delete user failed: user_id={user_id}")
                return jsonify({'error': 'Failed to delete user'}), 400
            self._logger.info(f"Delete user succeeded: user_id={user_id}")
            return jsonify({'message': 'User deleted successfully'})
        except Exception as exc:
            self._logger.error(f'Error deleting user: {exc}')
            return jsonify({'error': str(exc)}), 500

    def toggle_user(self, user_id: int) -> Response:
        """切换用户白名单状态。"""
        try:
            if not self._config_manager.is_chat_whitelist_enabled():
                return jsonify({'error': 'Whitelist control is disabled'}), 400
            if not self._user_service.toggle_user_status(user_id):
                self._logger.warning(f"Toggle user whitelist failed: user_id={user_id}")
                return jsonify({'error': 'Failed to toggle user status'}), 400
            self._logger.info(f"Toggle user whitelist succeeded: user_id={user_id}")
            return jsonify({'message': 'User status toggled successfully'})
        except Exception as exc:
            self._logger.error(f'Error toggling user status: {exc}')
            return jsonify({'error': str(exc)}), 500
