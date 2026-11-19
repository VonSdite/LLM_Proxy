#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理端后端扩展的发现、路由挂载与生命周期管理。"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from flask import Blueprint, jsonify
from werkzeug.exceptions import HTTPException

from ..application.app_context import AppContext, Logger
from ..services import AuthenticationService
from .decorators import require_authentication

EXTENSION_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class AdminExtensionContext:
    """传递给受信任扩展的稳定运行时上下文。"""

    extension_id: str
    logger: Logger
    config_manager: Any
    root_path: Path
    extension_path: Path
    database: Any
    services: Mapping[str, Any]


def _authorized() -> None:
    """认证通过后继续执行扩展路由。"""


class AdminExtensionManager:
    """加载本地受信任 Python 扩展并挂载到独立 API 命名空间。"""

    def __init__(
        self,
        ctx: AppContext,
        auth_service: AuthenticationService,
        database: Any,
        services: Mapping[str, Any] | None = None,
        directory: Path | None = None,
    ):
        self._ctx = ctx
        self._auth_service = auth_service
        self._database = database
        self._services = MappingProxyType(dict(services or {}))
        self._directory = Path(directory) if directory is not None else ctx.root_path / "extensions"
        self._instances: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self._closed = False
        self._load_extensions()

    @property
    def extension_ids(self) -> tuple[str, ...]:
        return tuple(self._instances)

    @property
    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def _load_extensions(self) -> None:
        if not self._directory.is_dir():
            self._ctx.logger.debug("Admin extensions directory not found: path=%s", self._directory)
            return

        for folder in sorted(self._directory.iterdir(), key=lambda item: item.name):
            if not folder.is_dir() or not (folder / "extension.py").is_file():
                continue
            self._load_extension(folder)

    def _load_extension(self, folder: Path) -> None:
        extension_id = folder.name
        if not EXTENSION_ID.fullmatch(extension_id):
            self._record_error(extension_id, "Extension ID must be a lowercase identifier")
            return

        module_name = f"llm_proxy_admin_extension_{extension_id.encode('utf-8').hex()}"
        blueprint_name = f"admin_extension_{extension_id.encode('utf-8').hex()}"
        instance = None
        try:
            spec = importlib.util.spec_from_file_location(
                module_name,
                folder / "extension.py",
                submodule_search_locations=[str(folder)],
            )
            if spec is None or spec.loader is None:
                raise ValueError("Extension module cannot be loaded")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            extension_class = getattr(module, "Extension", None)
            if not isinstance(extension_class, type):
                raise TypeError("Extension module must export an Extension class")
            context = AdminExtensionContext(
                extension_id=extension_id,
                logger=self._ctx.logger,
                config_manager=self._ctx.config_manager,
                root_path=self._ctx.root_path,
                extension_path=folder,
                database=self._database,
                services=self._services,
            )
            instance = extension_class(context)

            blueprint = Blueprint(
                blueprint_name,
                module_name,
                url_prefix=f"/api/extensions/{extension_id}",
            )
            blueprint.before_request(require_authentication(self._auth_service)(_authorized))
            self._install_error_handler(blueprint, extension_id)
            register_routes = getattr(instance, "register_routes", None)
            if callable(register_routes):
                register_routes(blueprint)
            self._register_blueprint(blueprint)
            self._instances[extension_id] = instance
            self._ctx.logger.info("Admin extension loaded: id=%s path=%s", extension_id, folder)
        except Exception as exc:
            self._remove_extension_modules(module_name)
            self._close_instance(extension_id, instance)
            self._record_error(extension_id, str(exc))
            self._ctx.logger.error("Admin extension failed to load: id=%s error=%s", extension_id, exc)

    def _register_blueprint(self, blueprint: Blueprint) -> None:
        """在主应用上注册一次，失败时恢复 Flask 的路由及注册表。"""
        app = self._ctx.flask_app
        # Werkzeug 不提供删除规则的接口，使用公开配置和未绑定规则保存路由快照。
        url_map = app.url_map
        saved_url_map = type(url_map)(
            [rule.empty() for rule in url_map.iter_rules()],
            default_subdomain=url_map.default_subdomain,
            strict_slashes=url_map.strict_slashes,
            merge_slashes=url_map.merge_slashes,
            redirect_defaults=url_map.redirect_defaults,
            converters=url_map.converters,
            sort_parameters=url_map.sort_parameters,
            sort_key=url_map.sort_key,
            host_matching=url_map.host_matching,
        )
        registries = {
            "blueprints": dict(app.blueprints),
            "view_functions": dict(app.view_functions),
            "error_handler_spec": defaultdict(
                lambda: defaultdict(dict),
                {
                    scope: defaultdict(dict, {code: dict(handlers) for code, handlers in codes.items()})
                    for scope, codes in app.error_handler_spec.items()
                },
            ),
        }
        for name in (
            "before_request_funcs",
            "after_request_funcs",
            "teardown_request_funcs",
            "url_default_functions",
            "url_value_preprocessors",
            "template_context_processors",
        ):
            # 只复制容器，保留绑定方法及其所属服务实例。
            registries[name] = defaultdict(list, {scope: list(funcs) for scope, funcs in getattr(app, name).items()})
        commands = dict(app.cli.commands)
        template_registries = {name: dict(getattr(app.jinja_env, name)) for name in ("filters", "tests", "globals")}
        try:
            app.register_blueprint(blueprint)
        except Exception:
            app.url_map = saved_url_map
            for name, saved in registries.items():
                current = getattr(app, name)
                current.clear()
                current.update(saved)
            app.cli.commands.clear()
            app.cli.commands.update(commands)
            for name, saved in template_registries.items():
                current = getattr(app.jinja_env, name)
                current.clear()
                current.update(saved)
            raise

    @staticmethod
    def _remove_extension_modules(module_name: str) -> None:
        """移除加载失败的扩展包及其辅助模块。"""
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(f"{module_name}."):
                sys.modules.pop(name, None)

    def _install_error_handler(self, blueprint: Blueprint, extension_id: str) -> None:
        @blueprint.errorhandler(Exception)
        def handle_extension_error(error: Exception):
            if isinstance(error, HTTPException):
                return error.get_response()
            self._ctx.logger.error(
                "Admin extension request failed: id=%s error=%s",
                extension_id,
                error,
                exc_info=True,
            )
            return jsonify({"error": "Extension action failed"}), 500

    def _record_error(self, extension_id: str, message: str) -> None:
        self._errors[extension_id] = message

    def start(self) -> None:
        """启动扩展后台任务；单个扩展失败不影响其他扩展。"""
        for extension_id, instance in self._instances.items():
            try:
                start = getattr(instance, "start", None)
                if callable(start):
                    start()
            except Exception as exc:
                self._record_error(extension_id, str(exc))
                self._ctx.logger.error("Admin extension failed to start: id=%s error=%s", extension_id, exc)

    def close(self) -> None:
        """按加载逆序关闭扩展并释放其后台资源。"""
        if self._closed:
            return
        self._closed = True
        for extension_id in reversed(tuple(self._instances)):
            instance = self._instances.pop(extension_id)
            self._close_instance(extension_id, instance)

    def _close_instance(self, extension_id: str, instance: Any) -> None:
        if instance is None:
            return
        try:
            close = getattr(instance, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            self._record_error(extension_id, str(exc))
            self._ctx.logger.error("Admin extension failed to close: id=%s error=%s", extension_id, exc)
