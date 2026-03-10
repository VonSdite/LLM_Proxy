#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创建 Flask 应用实例。"""

from pathlib import Path

from flask import Flask


def create_flask_app() -> Flask:
    """初始化模板目录与静态目录。"""
    templates_dir = Path(__file__).resolve().parent / 'templates'
    static_dir = Path(__file__).resolve().parent / 'static'

    app = Flask(
        __name__,
        template_folder=str(templates_dir),
        static_folder=str(static_dir),
        static_url_path='/static',
    )
    app.config['JSON_AS_ASCII'] = False
    return app
