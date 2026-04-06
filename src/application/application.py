#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, cast

from flask import request

from .app_context import AppContext, Logger
from ..config import (
    ConfigManager,
    ProviderManager,
    build_auth_group_schemas,
    build_provider_schemas,
)
from ..presentation import (
    AuthenticationController,
    ProviderController,
    ProxyController,
    UserController,
    WebController,
    create_flask_app,
)
from ..repositories import AuthGroupRepository, LogRepository, UserRepository
from ..services import (
    AuthGroupService,
    AuthenticationService,
    LogService,
    ModelDiscoveryService,
    ProviderService,
    ProxyService,
    SettingsService,
    UserService,
)
from ..utils import normalize_ip
from ..utils.database import create_connection_factory


class Application:
    """应用装配入口，负责初始化配置、日志、仓储与控制器。"""

    def __init__(self, config_path: Path):
        self._config_path = config_path
        self._flask_app = create_flask_app()
        self._root_path = Path(__file__).resolve().parents[2]

        self._setup_config()
        self._setup_logging()
        self._setup_context()
        self._setup_repositories()
        self._setup_provider_manager()
        self._setup_controllers()
        self._setup_request_access_logging()

        self._logger.info('Application initialized successfully')

    def _setup_config(self) -> None:
        """初始化配置管理器。"""
        self._config_manager = ConfigManager(self._config_path, self._root_path)

    def _setup_logging(self) -> None:
        """初始化应用日志与访问日志。"""
        log_path = Path(self._config_manager.get_log_path())
        log_level = self._config_manager.get_log_level()

        log_path.mkdir(parents=True, exist_ok=True)
        level = getattr(logging, log_level.upper(), logging.INFO)

        formatter = logging.Formatter(
            '%(asctime)s|%(name)s|%(filename)s:%(lineno)d|%(levelname)s|%(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )

        logger = logging.getLogger('app')
        logger.handlers.clear()

        app_log_handler = RotatingFileHandler(
            log_path / 'app.log',
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding='utf-8',
        )
        app_log_handler.setFormatter(formatter)
        app_log_handler.setLevel(level)
        logger.addHandler(app_log_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)
        logger.setLevel(level)

        access_logger = logging.getLogger('access')
        access_logger.handlers.clear()
        access_handler = RotatingFileHandler(
            log_path / 'access.log',
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding='utf-8',
        )
        access_handler.setFormatter(
            logging.Formatter(
                '%(asctime)s|%(name)s|%(filename)s:%(lineno)d|%(levelname)s|%(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
            )
        )
        access_handler.setLevel(level)
        access_logger.addHandler(access_handler)
        access_logger.setLevel(level)

        self._logger = logger
        self._access_logger = access_logger
        cast(Any, self._flask_app).logger = logger

    def _setup_request_access_logging(self) -> None:
        """注册请求访问日志钩子，仅记录 IP、URL 与模型。"""

        @self._flask_app.before_request
        def log_request_access() -> None:
            client_ip = normalize_ip(request.remote_addr) or '-'
            requested_url = request.url
            model = None
            if (request.content_length or 0) > 0 and request.is_json:
                payload = request.get_json(silent=True)
                if isinstance(payload, dict):
                    model = payload.get('model')

            username = None
            user = self._user_service.get_user_by_ip(client_ip)
            if user:
                username = str(user.get('username') or '')

            log_message = f'ip={client_ip} url={requested_url}'
            if username:
                log_message = f'{log_message} username={username}'
            if model is not None:
                log_message = f'{log_message} model={model}'
            self._access_logger.info(log_message)

    def _setup_context(self) -> None:
        """创建全局运行上下文。"""
        self._ctx = AppContext(
            logger=cast(Logger, self._logger),
            config_manager=self._config_manager,
            root_path=self._root_path,
            flask_app=self._flask_app,
        )

    def _setup_repositories(self) -> None:
        """初始化数据库连接工厂与仓储层。"""
        db_path = Path(self._config_manager.get_database_path())
        self._db_connection_factory = create_connection_factory(db_path)
        self._user_repository = UserRepository(self._db_connection_factory)
        self._log_repository = LogRepository(self._db_connection_factory)
        self._auth_group_repository = AuthGroupRepository(self._db_connection_factory)

    def _setup_provider_manager(self) -> None:
        """加载 provider 配置并注册可用模型。"""
        from ..config import AuthGroupManager

        self._auth_group_manager = AuthGroupManager(self._ctx, self._auth_group_repository)
        self._provider_manager = ProviderManager(self._ctx, self._auth_group_manager)
        self.reload_providers()

    def _setup_controllers(self) -> None:
        """初始化服务层并完成路由注册。"""
        auth_service = AuthenticationService(self._ctx)
        user_service = UserService(self._ctx, self._user_repository)
        self._user_service = user_service
        self._user_service.sync_model_permissions()
        proxy_service = ProxyService(self._ctx, self._auth_group_manager)
        log_service = LogService(self._ctx, self._log_repository)
        provider_service = ProviderService(self._ctx, self.reload_providers)
        auth_group_service = AuthGroupService(
            self._ctx,
            self.reload_providers,
            self._auth_group_manager,
        )
        model_discovery_service = ModelDiscoveryService(self._ctx)
        settings_service = SettingsService(self._ctx)

        self._auth_controller = AuthenticationController(self._ctx, auth_service)
        self._user_controller = UserController(self._ctx, user_service, auth_service)
        self._provider_controller = ProviderController(
            self._ctx,
            provider_service,
            auth_group_service,
            model_discovery_service,
            settings_service,
            auth_service,
        )
        self._proxy_controller = ProxyController(
            self._ctx,
            proxy_service,
            user_service,
            log_service,
            self._provider_manager,
        )
        self._web_controller = WebController(self._ctx, log_service, auth_service)

        self._logger.info('All controllers initialized successfully')

    def reload_providers(self) -> None:
        self._config_manager.reload()
        config_dict = self._config_manager.get_raw_config()
        auth_groups_config = config_dict.get("auth_groups", [])
        if auth_groups_config is None:
            auth_groups_config = []
        providers_config = config_dict.get("providers", [])
        if providers_config is None:
            providers_config = []
        auth_group_schemas = build_auth_group_schemas(auth_groups_config)
        provider_schemas = build_provider_schemas(
            providers_config,
            available_auth_group_names={schema.name for schema in auth_group_schemas},
        )
        self._auth_group_manager.load_auth_groups(auth_group_schemas)
        self._provider_manager.load_providers(provider_schemas)
        if hasattr(self, "_user_service"):
            self._user_service.sync_model_permissions()

    def run(self) -> None:
        """启动 WSGI 服务。"""
        host = self._config_manager.get_server_host()
        port = self._config_manager.get_server_port()

        from gevent.pywsgi import WSGIServer

        server = WSGIServer((host, port), self._flask_app)
        self._logger.info('Starting LLM Proxy on %s:%s...', host, port)
        server.serve_forever()
