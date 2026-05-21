#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude OAuth 登录与认证文件管理。"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import requests

from ..application.app_context import AppContext
from ..utils.net import build_requests_proxies
from ..utils.proxy_warning import ProxyWarningRequired, request_with_proxy_warning_retry

CLAUDE_AUTH_URL = "https://claude.ai/oauth/authorize"
CLAUDE_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_REDIRECT_URI = "http://localhost:54545/callback"
CLAUDE_SCOPE = "user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"
OAUTH_SESSION_TTL_SECONDS = 10 * 60


@dataclass(frozen=True)
class _ClaudeOAuthSession:
    """Claude OAuth 临时会话。"""

    state: str
    code_verifier: str
    code_challenge: str
    expires_at: float


class ClaudeOAuthService:
    """处理 Claude OAuth 授权与认证文件生成。"""

    def __init__(self, ctx: AppContext):
        self._logger = ctx.logger
        self._config_manager = ctx.config_manager
        self._auth_dir = ctx.root_path / "data" / "oauth" / "claude"
        self._sessions: dict[str, _ClaudeOAuthSession] = {}

    def start_login(self) -> dict[str, Any]:
        """生成新的 Claude OAuth 授权链接。"""
        self._purge_expired_sessions()
        state = secrets.token_hex(16)
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        expires_at = time.time() + OAUTH_SESSION_TTL_SECONDS
        self._sessions[state] = _ClaudeOAuthSession(
            state=state,
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            expires_at=expires_at,
        )

        params = {
            "code": "true",
            "client_id": CLAUDE_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": CLAUDE_REDIRECT_URI,
            "scope": CLAUDE_SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        authorization_url = f"{CLAUDE_AUTH_URL}?{urlencode(params)}"
        return {
            "authorization_url": authorization_url,
            "auth_url": authorization_url,
            "state": state,
            "redirect_uri": CLAUDE_REDIRECT_URI,
            "expires_at": self._format_timestamp(expires_at),
        }

    def complete_login(self, callback_url: str) -> dict[str, Any]:
        """根据 Claude 回调 URL 换取 token 并写入认证文件。"""
        parsed_callback = self._parse_callback_url(callback_url)
        error = parsed_callback["error"]
        error_description = parsed_callback["error_description"]
        if error:
            if error_description:
                raise ValueError(f"Claude OAuth failed: {error}: {error_description}")
            raise ValueError(f"Claude OAuth failed: {error}")

        state = parsed_callback["state"]
        code = parsed_callback["code"]
        if not state:
            raise ValueError("Callback URL missing state")
        if not code:
            raise ValueError("Callback URL missing code")

        self._purge_expired_sessions()
        session = self._sessions.get(state)
        if session is None:
            raise ValueError("OAuth state is unknown or expired")

        token_data = self._exchange_code_for_tokens(
            code=code,
            state=state,
            code_verifier=session.code_verifier,
        )
        email = self._extract_email(token_data)
        if not email:
            raise ValueError("Claude token storage missing account information")

        expires_in = int(token_data.get("expires_in") or 0)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in, 0))
        organization = token_data.get("organization") if isinstance(token_data.get("organization"), dict) else {}
        account = token_data.get("account") if isinstance(token_data.get("account"), dict) else {}
        auth_payload = {
            "type": "claude",
            "id_token": token_data.get("id_token") or "",
            "access_token": token_data.get("access_token") or "",
            "refresh_token": token_data.get("refresh_token") or "",
            "email": email,
            "account_uuid": account.get("uuid") or "",
            "organization_uuid": organization.get("uuid") or "",
            "organization_name": organization.get("name") or "",
            "expired": self._format_datetime(expires_at),
            "last_refresh": self._format_datetime(datetime.now(timezone.utc)),
        }
        auth_file = self._write_auth_file(auth_payload)
        self._sessions.pop(state, None)
        self._logger.info("Claude OAuth auth file generated: file=%s email=%s", auth_file.name, email)
        return {
            "status": "ok",
            "auth_file": self._build_auth_file_entry(auth_file),
        }

    def list_auth_files(self) -> dict[str, Any]:
        """列出本地 Claude OAuth 认证文件。"""
        files: list[dict[str, Any]] = []
        for path in self._iter_auth_file_paths():
            entry = self._build_auth_file_entry(path)
            if entry:
                files.append(entry)
        files.sort(key=lambda item: str(item.get("name") or "").lower())
        return {
            "files": files,
            "total": len(files),
            "auth_dir": str(self._auth_dir),
        }

    def delete_auth_file(self, name: str) -> dict[str, Any]:
        """删除指定 Claude OAuth 认证文件。"""
        auth_file = self._resolve_auth_file(name)
        deleted_name = auth_file.name
        auth_file.unlink()
        self._logger.info("Claude OAuth auth file deleted: file=%s", deleted_name)
        return {
            "status": "ok",
            "deleted": deleted_name,
        }

    def _exchange_code_for_tokens(self, *, code: str, state: str, code_verifier: str) -> dict[str, Any]:
        request_body = {
            "code": code,
            "state": state,
            "grant_type": "authorization_code",
            "client_id": CLAUDE_CLIENT_ID,
            "redirect_uri": CLAUDE_REDIRECT_URI,
            "code_verifier": code_verifier,
        }
        response = self._request_with_proxy_warning_retry(
            "POST",
            CLAUDE_TOKEN_URL,
            json=request_body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise ValueError(f"Claude token exchange failed with status {response.status_code}: {response.text}")
        token_data = response.json()
        if not isinstance(token_data, dict):
            raise ValueError("Claude token response must be a JSON object")
        return token_data

    def _request_with_proxy_warning_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        request_options = self._build_request_options()
        normalized_method = str(method or "").strip().upper()
        session = requests.Session()

        def send_request() -> requests.Response:
            if normalized_method == "POST":
                return session.post(
                    url,
                    allow_redirects=False,
                    **kwargs,
                    **request_options,
                )
            raise ValueError(f"Unsupported OAuth request method: {method}")

        try:
            return request_with_proxy_warning_retry(
                send_request,
                request_options=request_options,
                confirm_session=session,
                logger=self._logger,
                log_context=f"oauth_url={url}",
            )
        except ProxyWarningRequired as exc:
            raise ValueError(str(exc)) from exc
        finally:
            session.close()

    def _build_request_options(self) -> dict[str, Any]:
        if self._config_manager is None:
            return {
                "proxies": None,
                "verify": False,
            }
        return {
            "proxies": build_requests_proxies(self._config_manager.get_oauth_proxy()),
            "verify": self._config_manager.is_oauth_verify_ssl_enabled(),
        }

    def _write_auth_file(self, payload: dict[str, Any]) -> Path:
        self._auth_dir.mkdir(parents=True, exist_ok=True)
        auth_file = self._auth_dir / self._build_credential_file_name(payload)
        self._write_json_file(auth_file, payload)
        return auth_file

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    def _build_auth_file_entry(self, path: Path) -> dict[str, Any]:
        payload = self._read_auth_file(path)
        token_status = "expired" if self._is_auth_payload_expired(payload) else "active"
        availability = self._build_auth_file_availability(payload)
        return {
            "name": path.name,
            "path": str(path),
            "type": payload.get("type") or "claude",
            "email": payload.get("email") or "",
            "account_uuid": payload.get("account_uuid") or "",
            "organization_uuid": payload.get("organization_uuid") or "",
            "organization_name": payload.get("organization_name") or "",
            "status": token_status,
            "status_message": "Token expired" if token_status == "expired" else "Ready",
            "token_status": token_status,
            "token_status_message": "Token expired" if token_status == "expired" else "Ready",
            "availability_status": availability["status"],
            "availability_status_message": availability["message"],
            "expired": payload.get("expired") or "",
            "last_refresh": payload.get("last_refresh") or "",
            "size": path.stat().st_size,
            "modified": int(path.stat().st_mtime),
        }

    def _build_auth_file_availability(self, payload: dict[str, Any]) -> dict[str, str]:
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        if not access_token:
            return self._availability("auth_failed", "认证失败：认证文件缺少 access_token，请重新登录")
        if self._is_auth_payload_expired(payload):
            if refresh_token:
                return self._availability("refresh_required", "待刷新：access_token 已过期，请求前需要刷新")
            return self._availability("auth_check_required", "待验证：access_token 已过期且缺少 refresh_token")
        return self._availability("available", "可用：token 未过期")

    @staticmethod
    def _availability(status: str, message: str) -> dict[str, str]:
        return {
            "status": status,
            "message": message,
        }

    def _iter_auth_file_paths(self) -> list[Path]:
        if not self._auth_dir.exists():
            return []
        paths = [path for path in self._auth_dir.glob("*.json") if path.is_file()]
        return sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)

    @staticmethod
    def _read_auth_file(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("Auth file must contain a JSON object")
        return payload

    def _resolve_auth_file(self, name: str) -> Path:
        cleaned_name = Path(str(name or "").strip()).name
        if not cleaned_name or cleaned_name != str(name or "").strip():
            raise ValueError("Invalid auth file name")
        auth_file = self._auth_dir / cleaned_name
        if not auth_file.exists() or not auth_file.is_file():
            raise ValueError("Auth file not found")
        return auth_file

    @staticmethod
    def _build_credential_file_name(payload: dict[str, Any]) -> str:
        email = str(payload.get("email") or "").strip()
        if not email:
            raise ValueError("Claude token storage missing account information")
        safe_email = "-".join(part for part in re.split(r"[/\\\r\n]+", email) if part).strip()
        return f"claude-{safe_email or 'account'}.json"

    @staticmethod
    def _extract_email(token_data: dict[str, Any]) -> str:
        account = token_data.get("account") if isinstance(token_data.get("account"), dict) else {}
        return str(token_data.get("email") or account.get("email_address") or "").strip()

    @classmethod
    def _parse_callback_url(cls, value: str) -> dict[str, str]:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Callback URL is required")

        raw_code_state = cls._parse_raw_code_state(text)
        if raw_code_state:
            return raw_code_state

        candidate = text
        if "://" not in candidate:
            if candidate.startswith("?"):
                candidate = f"http://localhost{candidate}"
            elif any(char in candidate for char in "/?#") or ":" in candidate:
                candidate = f"http://{candidate}"
            elif "=" in candidate:
                candidate = f"http://localhost/?{candidate}"
            else:
                raise ValueError("Invalid callback URL")

        parsed = urlparse(candidate)
        query = parse_qs(parsed.query)
        fragment = parse_qs(parsed.fragment)

        code = cls._first_query_value(query, "code") or cls._first_query_value(fragment, "code")
        state = cls._first_query_value(query, "state") or cls._first_query_value(fragment, "state")
        error = cls._first_query_value(query, "error") or cls._first_query_value(fragment, "error")
        error_description = cls._first_query_value(query, "error_description") or cls._first_query_value(
            fragment, "error_description"
        )

        if code and not state and "#" in code:
            code, state = code.split("#", 1)
        if code and not state and parsed.fragment and "=" not in parsed.fragment:
            state = parsed.fragment

        if not code and not error:
            raise ValueError("Callback URL missing code")
        return {
            "code": code.strip(),
            "state": state.strip(),
            "error": error.strip(),
            "error_description": error_description.strip(),
        }

    @staticmethod
    def _parse_raw_code_state(text: str) -> dict[str, str] | None:
        if "://" in text or "?" in text or "=" in text or "#" not in text:
            return None
        code, state = text.split("#", 1)
        code = unquote(code).strip()
        state = unquote(state).strip()
        if not code or not state:
            return None
        return {
            "code": code,
            "state": state,
            "error": "",
            "error_description": "",
        }

    @staticmethod
    def _first_query_value(query: dict[str, list[str]], key: str) -> str:
        values = query.get(key)
        if not values:
            return ""
        return str(values[0] or "").strip()

    def _purge_expired_sessions(self) -> None:
        now = time.time()
        expired_states = [state for state, session in self._sessions.items() if session.expires_at <= now]
        for state in expired_states:
            self._sessions.pop(state, None)

    @staticmethod
    def _generate_code_verifier() -> str:
        return base64.urlsafe_b64encode(secrets.token_bytes(96)).decode("ascii").rstrip("=")

    @staticmethod
    def _generate_code_challenge(code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _format_timestamp(value: float) -> str:
        return ClaudeOAuthService._format_datetime(datetime.fromtimestamp(value, tz=timezone.utc))

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _is_auth_payload_expired(cls, payload: dict[str, Any]) -> bool:
        expired_at = cls._parse_datetime(payload.get("expired"))
        if expired_at is None:
            return False
        return expired_at <= datetime.now(timezone.utc)
