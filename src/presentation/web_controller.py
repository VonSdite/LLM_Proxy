#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""页面与统计控制器。"""

from flask import Response, jsonify, render_template, request

from ..application.app_context import AppContext
from ..services import AuthenticationService, LogService
from .decorators import require_authentication


class WebController:
    """处理页面渲染与统计相关 API。"""

    def __init__(self, ctx: AppContext, log_service: LogService, auth_service: AuthenticationService):
        self._ctx = ctx
        self._app = ctx.flask_app
        self._logger = ctx.logger
        self._config_manager = ctx.config_manager
        self._log_service = log_service
        self._auth_service = auth_service
        self._register_routes()

    def _register_routes(self) -> None:
        """注册页面与统计路由。"""
        auth = require_authentication(self._auth_service)

        self._app.route('/')(auth(self.index))
        self._app.route('/users')(auth(self.users_page))

        self._app.route('/api/statistics', methods=['GET'])(auth(self.get_statistics))
        self._app.route('/api/request-logs', methods=['GET'])(auth(self.get_request_logs))
        self._app.route('/api/usernames', methods=['GET'])(auth(self.get_usernames))
        self._app.route('/api/request-models', methods=['GET'])(auth(self.get_request_models))

    def index(self) -> str:
        """首页。"""
        return render_template('index.html')

    def users_page(self) -> str:
        """用户管理页。"""
        return render_template(
            'users.html',
            chat_whitelist_enabled=self._config_manager.is_chat_whitelist_enabled(),
        )

    def get_statistics(self) -> Response:
        """查询统计聚合数据。"""
        try:
            self._logger.info(
                "Statistics queried: start_date=%s end_date=%s username=%s request_model=%s",
                request.args.get('start_date'),
                request.args.get('end_date'),
                request.args.get('username'),
                request.args.get('request_model'),
            )
            stats = self._log_service.get_statistics(
                request.args.get('start_date'),
                request.args.get('end_date'),
                request.args.get('username'),
                request.args.get('request_model'),
            )
            return jsonify(stats)
        except Exception as exc:
            self._logger.error(f'Error getting statistics: {exc}')
            return jsonify({'error': str(exc)}), 500

    def get_request_logs(self) -> Response:
        """查询请求日志分页数据。"""
        try:
            page = max(int(request.args.get('page', 1)), 1)
            page_size = min(max(int(request.args.get('page_size', 50)), 1), 200)
            self._logger.info(f"Request logs queried: page={page}, page_size={page_size}")

            logs = self._log_service.get_request_logs(
                page,
                page_size,
                request.args.get('start_date'),
                request.args.get('end_date'),
                request.args.get('username'),
                request.args.get('request_model'),
            )
            return jsonify(logs)
        except ValueError:
            return jsonify({'error': 'page and page_size must be integers'}), 400
        except Exception as exc:
            self._logger.error(f'Error getting request logs: {exc}')
            return jsonify({'error': str(exc)}), 500

    def get_usernames(self) -> Response:
        """查询已出现过请求记录的用户名列表。"""
        try:
            usernames = self._log_service.get_unique_usernames()
            self._logger.info(f"Usernames queried: count={len(usernames)}")
            return jsonify(usernames)
        except Exception as exc:
            self._logger.error(f'Error getting usernames: {exc}')
            return jsonify({'error': str(exc)}), 500

    def get_request_models(self) -> Response:
        """查询出现过请求日志的请求模型列表。"""
        try:
            models = self._log_service.get_unique_request_models()
            self._logger.info(f"Request models queried: count={len(models)}")
            return jsonify(models)
        except Exception as exc:
            self._logger.error(f'Error getting request models: {exc}')
            return jsonify({'error': str(exc)}), 500
