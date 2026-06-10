#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创建 Flask 应用实例。"""

from pathlib import Path

from flask import Flask

from ..utils.app_version import get_app_version
from .cors import register_data_plane_cors


def create_flask_app() -> Flask:
    """初始化模板目录与静态目录。"""
    templates_dir = Path(__file__).resolve().parent / "templates"
    static_dir = Path(__file__).resolve().parent / "static"

    app = Flask(
        __name__,
        template_folder=str(templates_dir),
        static_folder=str(static_dir),
        static_url_path="/static",
    )
    app.config["JSON_AS_ASCII"] = False
    register_data_plane_cors(app)

    app_version = get_app_version()

    @app.context_processor
    def inject_template_versions() -> dict[str, str]:
        """注入模板统一使用的版本信息。"""
        return {
            "app_version": app_version,
        }

    return app
