#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""认证控制器。"""

from typing import Any

from flask import jsonify, make_response, redirect, render_template, request
from flask.typing import ResponseReturnValue

from ..application.app_context import AppContext
from ..services import AuthenticationService


class AuthenticationController:
    """处理登录/登出相关路由。"""

    def __init__(self, ctx: AppContext, auth_service: AuthenticationService):
        self._ctx = ctx
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._auth_service = auth_service
        self._register_routes()

    @staticmethod
    def _get_request_payload() -> dict[str, Any]:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            return dict(payload)
        return {}

    def _register_routes(self) -> None:
        self._app.route('/login')(self.login_page)
        self._app.route('/api/login', methods=['POST'])(self.api_login)
        self._app.route('/logout')(self.logout)
        self._app.route('/api/logout', methods=['POST'])(self.api_logout)

    def login_page(self) -> ResponseReturnValue:
        if not self._auth_service.is_auth_enabled():
            return redirect('/')

        session_token = request.cookies.get('session_token')
        if self._auth_service.validate_session(session_token):
            return redirect('/')

        return render_template('login.html')

    def api_login(self) -> ResponseReturnValue:
        if not self._auth_service.is_auth_enabled():
            return jsonify({'message': 'Authentication not enabled'}), 200

        data = self._get_request_payload()
        username = data.get('username')
        password = data.get('password')

        if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
            self._logger.warning('Login rejected: missing username or password')
            return jsonify({'error': 'Username and password are required'}), 400

        if not self._auth_service.authenticate(username, password):
            self._logger.warning('Login failed: invalid credentials for username=%r', username)
            return jsonify({'error': 'Invalid username or password'}), 401

        session_token = self._auth_service.create_session(username)
        self._logger.info('Login succeeded: username=%r', username)
        response = make_response(jsonify({'message': 'Login successful', 'username': username}))
        cookie_settings = self._auth_service.get_cookie_settings()
        response.set_cookie('session_token', session_token, **cookie_settings)
        return response

    def logout(self) -> ResponseReturnValue:
        session_token = request.cookies.get('session_token')
        if session_token:
            self._auth_service.destroy_session(session_token)
        self._logger.info('Logout succeeded via page route')

        response = make_response(redirect('/login'))
        response.delete_cookie('session_token')
        return response

    def api_logout(self) -> ResponseReturnValue:
        session_token = request.cookies.get('session_token')
        if session_token:
            self._auth_service.destroy_session(session_token)
        self._logger.info('Logout succeeded via API route')

        response = make_response(jsonify({'message': 'Logout successful'}))
        response.delete_cookie('session_token')
        return response
