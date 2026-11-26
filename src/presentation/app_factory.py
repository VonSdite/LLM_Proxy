#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创建 Flask 应用实例。"""

import mimetypes
from pathlib import Path

from flask import Flask, request

from .cors import register_data_plane_cors


def create_flask_app() -> Flask:
    """初始化模板目录与静态目录。"""
    # 显式覆盖系统映射，避免 Windows 注册表将模块脚本识别为纯文本。
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/javascript", ".mjs")
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

    @app.after_request
    def cache_frontend_assets(response):
        """内容哈希资源长期缓存，构建清单每次重新校验。"""
        if request.path.startswith("/static/app/assets/") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.path in {"/static/app/manifest.json", "/static/app/importmap.json"}:
            response.headers["Cache-Control"] = "no-cache"
        return response

    return app
