#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""认证装饰器。"""

from functools import wraps
from typing import Callable

from flask import jsonify, redirect, request

from ..services import AuthenticationService


def require_authentication(auth_service: AuthenticationService):
    """生成认证校验装饰器。"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def decorated_function(*args, **kwargs):
            if not auth_service.is_auth_enabled():
                return func(*args, **kwargs)

            session_token = request.cookies.get("session_token")
            if not auth_service.validate_session(session_token):
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Unauthorized"}), 401
                return redirect("/login")

            return func(*args, **kwargs)

        return decorated_function

    return decorator
