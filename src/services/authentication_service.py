#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""认证服务。"""

import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ..application.app_context import AppContext, Logger


class AuthenticationService:
    """处理管理员登录与内存会话管理。"""

    def __init__(self, ctx: AppContext):
        self._ctx = ctx
        self._config_manager = ctx.config_manager
        self._logger: Logger = ctx.logger
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._session_max_age = 86400

    def is_auth_enabled(self) -> bool:
        """判断是否启用认证。"""
        return self._config_manager.is_auth_enabled()

    def authenticate(self, username: str, password: str) -> bool:
        """校验管理员用户名与密码。"""
        admin_config = self._config_manager.get_admin_config() or {}
        expected_username = admin_config.get("username")
        expected_password = admin_config.get("password")

        if not expected_username or not expected_password:
            self._logger.warning("Authentication skipped: admin credentials are not configured")
            return False

        # 使用 compare_digest 可降低时序攻击风险，避免通过比较耗时推断凭据内容。
        is_valid = (
            secrets.compare_digest(str(username), str(expected_username))
            and secrets.compare_digest(str(password), str(expected_password))
        )
        if not is_valid:
            self._logger.warning(f"Authentication failed for username={username!r}")
        return is_valid

    def create_session(self, username: str) -> str:
        """创建新会话并返回 session token。"""
        self._cleanup_expired_sessions()

        session_token = secrets.token_urlsafe(32)
        expires = datetime.now() + timedelta(seconds=self._session_max_age)

        self._sessions[session_token] = {"username": username, "expires": expires}
        self._logger.info(f"Created session for user={username!r}")
        return session_token

    def validate_session(self, session_token: Optional[str]) -> bool:
        """校验会话是否存在且未过期。"""
        if not session_token:
            return False

        session = self._sessions.get(session_token)
        if not session:
            return False

        if datetime.now() > session["expires"]:
            del self._sessions[session_token]
            self._logger.info("Session expired and removed")
            return False

        return True

    def destroy_session(self, session_token: str) -> None:
        """销毁指定会话。"""
        session = self._sessions.pop(session_token, None)
        if session:
            self._logger.info(f"Destroyed session for user={session['username']!r}")

    def get_cookie_settings(self) -> Dict[str, Any]:
        """返回 session cookie 配置。"""
        return {
            "max_age": self._session_max_age,
            "httponly": True,
            "secure": False,
            "samesite": "Lax",
        }

    def _cleanup_expired_sessions(self) -> None:
        """清理内存中过期会话。"""
        now = datetime.now()
        expired = [token for token, session in self._sessions.items() if now > session["expires"]]
        for token in expired:
            del self._sessions[token]
        if expired:
            self._logger.info(f"Expired sessions cleaned: count={len(expired)}")
