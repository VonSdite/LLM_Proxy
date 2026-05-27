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
from ..utils.net import (
    apply_requests_proxy_settings,
    build_requests_proxy_settings,
    build_requests_request_proxies,
)
from ..utils.proxy_warning import ProxyWarningRequired, request_with_proxy_warning_retry

CLAUDE_AUTH_URL = "https://claude.ai/oauth/authorize"
CLAUDE_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_REDIRECT_URI = "http://localhost:54545/callback"
CLAUDE_SCOPE = "user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"
OAUTH_SESSION_TTL_SECONDS = 10 * 60
DEFAULT_CLAUDE_MODEL_IDS: tuple[str, ...] = ()
AUTH_FAILURE_ERROR_TYPES = {
    "authentication_error",
    "invalid_api_key",
    "invalid_grant",
    "missing_access_token",
    "permission_error",
    "token_refresh_failed",
}


@dataclass(frozen=True)
class ClaudeAuthCandidate:
    """可用于一次 Claude OAuth 请求的认证文件快照。"""

    name: str
    path: Path
    access_token: str
    email: str
    payload: dict[str, Any]


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
        self._models_file = self._auth_dir / "models.json"
        self._state_file = self._auth_dir / ".state" / "auth_files.json"
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
        self._delete_auth_file_state_entry(auth_file.name)
        self._sessions.pop(state, None)
        self._logger.info("Claude OAuth auth file generated: file=%s email=%s", auth_file.name, email)
        return {
            "status": "ok",
            "auth_file": self._build_auth_file_entry(auth_file),
        }

    def list_auth_files(self) -> dict[str, Any]:
        """列出本地 Claude OAuth 认证文件。"""
        files: list[dict[str, Any]] = []
        state = self._load_auth_file_state()
        for path in self._iter_auth_file_paths():
            entry = self._build_auth_file_entry(path, state)
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
        self._delete_auth_file_state_entry(deleted_name)
        self._clear_last_success_auth_file(deleted_name)
        self._logger.info("Claude OAuth auth file deleted: file=%s", deleted_name)
        return {
            "status": "ok",
            "deleted": deleted_name,
        }

    def list_models(self) -> dict[str, Any]:
        """返回当前 Claude OAuth 可用模型目录。"""
        models = self._build_model_entries(self._load_model_ids())
        return {
            "status": "ok",
            "provider": "claude",
            "models": models,
            "total": len(models),
        }

    def add_model(self, model_id: str) -> dict[str, Any]:
        """添加一个本地 Claude 模型 ID。"""
        normalized_model_id = self._normalize_model_id(model_id)
        model_ids = list(self._load_model_ids())
        if normalized_model_id in model_ids:
            return self.list_models()
        model_ids.append(normalized_model_id)
        self._write_model_ids(model_ids)
        return self.list_models()

    def delete_model(self, model_id: str) -> dict[str, Any]:
        """删除一个本地 Claude 模型 ID。"""
        normalized_model_id = self._normalize_model_id(model_id)
        model_ids = [
            current_model_id for current_model_id in self._load_model_ids() if current_model_id != normalized_model_id
        ]
        self._write_model_ids(model_ids)
        return self.list_models()

    def list_model_names(self) -> tuple[str, ...]:
        """返回当前认证文件实际可用的 Claude 模型名。"""
        if not self._iter_auth_file_paths():
            return ()
        return tuple(sorted(dict.fromkeys(self._load_model_ids())))

    def has_model(self, model_name: str) -> bool:
        """判断模型名是否属于当前 Claude OAuth 可代理模型。"""
        normalized_model = str(model_name or "").strip()
        return bool(normalized_model) and normalized_model in set(self.list_model_names())

    def iter_auth_candidates_for_model(self, model_name: str) -> list[ClaudeAuthCandidate]:
        """按认证文件顺序返回可用于 Claude 请求的账号。"""
        normalized_model = str(model_name or "").strip()
        if not normalized_model or normalized_model not in set(self._load_model_ids()):
            return []

        candidates: list[ClaudeAuthCandidate] = []
        state = self._load_auth_file_state()
        state_files = state.get("files")
        for path in self._iter_auth_file_paths():
            file_state = {}
            if isinstance(state_files, dict) and isinstance(state_files.get(path.name), dict):
                file_state = state_files[path.name]
            if self._is_auth_failure_state(file_state):
                continue
            candidate = self._build_auth_candidate(path)
            if candidate is None:
                continue
            candidates.append(candidate)
        return self._prioritize_last_success_candidate(candidates, state)

    def record_auth_file_success(self, name: str) -> None:
        """记录认证文件最近一次 Claude 模型代理成功。"""
        normalized_name = self._normalize_auth_file_name(name)
        if not normalized_name:
            return
        self._update_auth_file_state(
            normalized_name,
            {
                "usage_status": "success",
                "usage_status_message": "success",
                "usage_status_code": 200,
                "usage_error_type": "",
                "usage_status_updated_at": self._now_iso(),
            },
        )
        self._remember_last_success_auth_file(normalized_name)

    def record_auth_file_failure(
        self,
        name: str,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
    ) -> None:
        """记录认证文件最近一次 Claude 模型代理失败。"""
        normalized_name = self._normalize_auth_file_name(name)
        if not normalized_name:
            return
        self._update_auth_file_state(
            normalized_name,
            {
                "usage_status": "error",
                "usage_status_message": self._truncate_state_text(message),
                "usage_status_code": status_code,
                "usage_error_type": str(error_type or "").strip(),
                "usage_status_updated_at": self._now_iso(),
            },
        )

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
        session = requests.Session()
        request_options = self._build_request_options(session=session)
        normalized_method = str(method or "").strip().upper()

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

    def _build_request_options(self, *, session: requests.Session | None = None) -> dict[str, Any]:
        if self._config_manager is None:
            if session is not None:
                session.trust_env = False
            return {
                "proxies": {"http": None, "https": None, "all": None},
                "verify": False,
            }
        proxy_settings = build_requests_proxy_settings(
            self._get_oauth_proxy_mode(),
            self._config_manager.get_oauth_proxy(),
            proxy_mode_error_message="OAuth proxy_mode must be one of: direct, system, custom",
            proxy_url_error_message="OAuth proxy must be a valid absolute URL",
        )
        if session is not None:
            apply_requests_proxy_settings(session, proxy_settings)
        return {
            "proxies": build_requests_request_proxies(proxy_settings),
            "verify": self._config_manager.is_oauth_verify_ssl_enabled(),
        }

    def _get_oauth_proxy_mode(self) -> str | None:
        getter = getattr(self._config_manager, "get_oauth_proxy_mode", None)
        if callable(getter):
            return getter()
        return None

    def _refresh_auth_file(self, auth_file: Path, payload: dict[str, Any]) -> dict[str, Any]:
        refresh_token = str(payload.get("refresh_token") or "").strip()
        response = self._request_with_proxy_warning_retry(
            "POST",
            CLAUDE_TOKEN_URL,
            json={
                "client_id": CLAUDE_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise ValueError(f"Claude token refresh failed with status {response.status_code}: {response.text}")
        token_data = response.json()
        if not isinstance(token_data, dict):
            raise ValueError("Claude refresh response must be a JSON object")

        expires_in = int(token_data.get("expires_in") or 0)
        account = token_data.get("account") if isinstance(token_data.get("account"), dict) else {}
        organization = token_data.get("organization") if isinstance(token_data.get("organization"), dict) else {}
        next_payload = dict(payload)
        next_payload["access_token"] = token_data.get("access_token") or payload.get("access_token") or ""
        next_payload["refresh_token"] = token_data.get("refresh_token") or refresh_token
        next_payload["id_token"] = token_data.get("id_token") or payload.get("id_token") or ""
        next_payload["email"] = self._extract_email(token_data) or payload.get("email") or ""
        next_payload["account_uuid"] = account.get("uuid") or payload.get("account_uuid") or ""
        next_payload["organization_uuid"] = organization.get("uuid") or payload.get("organization_uuid") or ""
        next_payload["organization_name"] = organization.get("name") or payload.get("organization_name") or ""
        next_payload["expired"] = self._format_datetime(
            datetime.now(timezone.utc) + timedelta(seconds=max(expires_in, 0))
        )
        next_payload["last_refresh"] = self._format_datetime(datetime.now(timezone.utc))
        self._write_json_file(auth_file, next_payload)
        self._clear_auth_file_auth_failure(auth_file.name)
        return next_payload

    def _build_auth_candidate(self, path: Path) -> ClaudeAuthCandidate | None:
        try:
            payload = self._read_auth_file(path)
        except Exception as exc:
            self._logger.warning("Claude auth file ignored: file=%s error=%s", path.name, exc)
            return None

        if self._is_auth_payload_expired(payload):
            refresh_token = str(payload.get("refresh_token") or "").strip()
            if refresh_token:
                try:
                    payload = self._refresh_auth_file(path, payload)
                except Exception as exc:
                    self._logger.warning("Claude auth file refresh failed: file=%s error=%s", path.name, exc)
                    self.record_auth_file_failure(
                        path.name,
                        f"Token refresh failed: {exc}",
                        status_code=401,
                        error_type="token_refresh_failed",
                    )
                    return None

        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            self.record_auth_file_failure(
                path.name,
                "Auth file does not contain access_token",
                status_code=401,
                error_type="missing_access_token",
            )
            return None

        return ClaudeAuthCandidate(
            name=path.name,
            path=path,
            access_token=access_token,
            email=str(payload.get("email") or "").strip(),
            payload=payload,
        )

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

    def _build_auth_file_entry(
        self,
        path: Path,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._read_auth_file(path)
        token_status = "expired" if self._is_auth_payload_expired(payload) else "active"
        state_files = (state or {}).get("files")
        file_state = {}
        if isinstance(state_files, dict) and isinstance(state_files.get(path.name), dict):
            file_state = state_files[path.name]
        availability = self._build_auth_file_availability(payload, file_state)
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
            "usage_status": str(file_state.get("usage_status") or "unknown"),
            "usage_status_message": str(file_state.get("usage_status_message") or ""),
            "usage_status_code": file_state.get("usage_status_code"),
            "usage_error_type": str(file_state.get("usage_error_type") or ""),
            "usage_status_updated_at": str(file_state.get("usage_status_updated_at") or ""),
            "expired": payload.get("expired") or "",
            "last_refresh": payload.get("last_refresh") or "",
            "size": path.stat().st_size,
            "modified": int(path.stat().st_mtime),
        }

    def _build_auth_file_availability(
        self,
        payload: dict[str, Any],
        file_state: dict[str, Any],
    ) -> dict[str, str]:
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        usage_error_type = str(file_state.get("usage_error_type") or "").strip()
        if usage_error_type == "token_refresh_failed":
            return self._availability(
                "auth_failed", "认证失败：access_token 过期后使用 refresh_token 刷新失败，请重新登录"
            )
        if not access_token:
            return self._availability("auth_failed", "认证失败：认证文件缺少 access_token，请重新登录")
        if self._is_auth_failure_state(file_state):
            reason = self._build_availability_failure_reason(file_state)
            message = "认证失败：上游返回认证错误，请重新登录"
            if reason:
                message = f"认证失败：上游返回 {reason}，请重新登录"
            return self._availability("auth_failed", message)
        if self._is_auth_payload_expired(payload):
            if refresh_token:
                return self._availability(
                    "refresh_required", "待刷新：access_token 已过期，请求前会使用 refresh_token 自动刷新"
                )
            return self._availability(
                "auth_check_required", "待验证：access_token 已过期且缺少 refresh_token，会先用当前 access_token 请求一次"
            )
        usage_status = str(file_state.get("usage_status") or "").strip()
        if usage_status == "success":
            return self._availability("available", "可用：最近一次请求成功")
        return self._availability("available", "可用：未命中过滤条件")

    def _build_availability_failure_reason(self, file_state: dict[str, Any]) -> str:
        message = str(file_state.get("usage_status_message") or "").strip()
        if message and message != "success":
            return self._truncate_state_text(message)
        error_type = str(file_state.get("usage_error_type") or "").strip()
        if error_type:
            return self._truncate_state_text(error_type)
        return ""

    @staticmethod
    def _availability(status: str, message: str) -> dict[str, str]:
        return {
            "status": status,
            "message": message,
        }

    def _iter_auth_file_paths(self) -> list[Path]:
        if not self._auth_dir.exists():
            return []
        paths = [
            path for path in self._auth_dir.glob("*.json") if path.name != self._models_file.name and path.is_file()
        ]
        return sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)

    def _load_model_ids(self) -> list[str]:
        if not self._models_file.exists():
            return list(DEFAULT_CLAUDE_MODEL_IDS)
        try:
            with self._models_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            self._logger.warning("Claude models file ignored: error=%s", exc)
            return list(DEFAULT_CLAUDE_MODEL_IDS)
        return self._normalize_model_ids(payload)

    def _write_model_ids(self, model_ids: list[str]) -> None:
        self._auth_dir.mkdir(parents=True, exist_ok=True)
        self._write_json_file(self._models_file, self._normalize_model_ids(model_ids))

    @staticmethod
    def _build_model_entries(model_ids: list[str]) -> list[dict[str, Any]]:
        return [{"id": model_id} for model_id in model_ids]

    def _load_auth_file_state(self) -> dict[str, Any]:
        if not self._state_file.exists():
            return {"files": {}}
        try:
            payload = self._read_auth_file(self._state_file)
        except Exception as exc:
            self._logger.warning("Claude auth file state ignored: error=%s", exc)
            return {"files": {}}
        files = payload.get("files")
        if not isinstance(files, dict):
            payload["files"] = {}
        return payload

    def _write_auth_file_state(self, payload: dict[str, Any]) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        next_payload = dict(payload)
        next_payload["updated_at"] = self._now_iso()
        self._write_json_file(self._state_file, next_payload)

    def _update_auth_file_state(self, name: str, patch: dict[str, Any]) -> None:
        state = self._load_auth_file_state()
        files = state.setdefault("files", {})
        if not isinstance(files, dict):
            files = {}
            state["files"] = files
        current = files.get(name)
        if not isinstance(current, dict):
            current = {}
        current.update(patch)
        files[name] = current
        try:
            self._write_auth_file_state(state)
        except Exception as exc:
            self._logger.warning("Claude auth file state write failed: file=%s error=%s", name, exc)

    def _remember_last_success_auth_file(self, name: str) -> None:
        """记录最近一次真实请求成功的认证文件，用于后续候选优先级。"""
        state = self._load_auth_file_state()
        state["last_success_auth_file"] = name
        try:
            self._write_auth_file_state(state)
        except Exception as exc:
            self._logger.warning("Claude last success auth file write failed: file=%s error=%s", name, exc)

    def _clear_last_success_auth_file(self, name: str) -> None:
        """删除认证文件时清理最近成功指针。"""
        state = self._load_auth_file_state()
        if self._get_last_success_auth_file(state) != name:
            return
        state.pop("last_success_auth_file", None)
        try:
            self._write_auth_file_state(state)
        except Exception as exc:
            self._logger.warning("Claude last success auth file clear failed: file=%s error=%s", name, exc)

    @staticmethod
    def _get_last_success_auth_file(state: dict[str, Any]) -> str:
        value = str(state.get("last_success_auth_file") or "").strip()
        if not value or Path(value).name != value:
            return ""
        return value

    def _prioritize_last_success_candidate(
        self,
        candidates: list[ClaudeAuthCandidate],
        state: dict[str, Any],
    ) -> list[ClaudeAuthCandidate]:
        """把最近成功的认证文件放到候选列表首位，其余顺序保持不变。"""
        last_success_name = self._get_last_success_auth_file(state)
        if not last_success_name:
            return candidates
        return sorted(candidates, key=lambda candidate: 0 if candidate.name == last_success_name else 1)

    def _delete_auth_file_state_entry(self, name: str) -> None:
        state = self._load_auth_file_state()
        files = state.get("files")
        if not isinstance(files, dict) or name not in files:
            return
        files.pop(name, None)
        try:
            self._write_auth_file_state(state)
        except Exception as exc:
            self._logger.warning("Claude auth file state delete failed: file=%s error=%s", name, exc)

    def _clear_auth_file_auth_failure(self, name: str) -> None:
        state = self._load_auth_file_state()
        files = state.get("files")
        if not isinstance(files, dict):
            return
        file_state = files.get(name)
        if not isinstance(file_state, dict) or not self._is_auth_failure_state(file_state):
            return
        file_state.update(
            {
                "usage_status": "unknown",
                "usage_status_message": "",
                "usage_status_code": None,
                "usage_error_type": "",
                "usage_status_updated_at": self._now_iso(),
            }
        )
        try:
            self._write_auth_file_state(state)
        except Exception as exc:
            self._logger.warning("Claude auth file state clear failed: file=%s error=%s", name, exc)

    @classmethod
    def _is_auth_failure_state(cls, file_state: dict[str, Any]) -> bool:
        status_code = file_state.get("usage_status_code")
        try:
            if int(status_code) in {401, 403}:
                return True
        except (TypeError, ValueError):
            pass

        error_type = str(file_state.get("usage_error_type") or "").strip().lower()
        if error_type in AUTH_FAILURE_ERROR_TYPES:
            return True

        message = str(file_state.get("usage_status_message") or "").strip().lower()
        return cls._is_auth_error_text(message)

    @staticmethod
    def _is_auth_error_text(value: str) -> bool:
        text = value.lower()
        return (
            "invalid or expired token" in text
            or "invalid bearer" in text
            or "invalid_api_key" in text
            or "invalid_grant" in text
            or "authentication_error" in text
            or "permission_error" in text
            or "token refresh failed" in text
        )

    @staticmethod
    def _normalize_model_id(value: Any) -> str:
        model_id = str(value or "").strip()
        if not model_id:
            raise ValueError("Model ID is required")
        if any(ch.isspace() for ch in model_id):
            raise ValueError("Model ID must not contain whitespace")
        return model_id

    @classmethod
    def _normalize_model_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []

        normalized: list[str] = []
        seen_ids: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            try:
                model_id = cls._normalize_model_id(item)
            except ValueError:
                continue
            if not model_id or model_id in seen_ids:
                continue
            seen_ids.add(model_id)
            normalized.append(model_id)
        return normalized

    @staticmethod
    def _normalize_auth_file_name(name: str) -> str:
        normalized_name = Path(str(name or "").strip()).name
        return normalized_name if normalized_name == str(name or "").strip() else ""

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
    def _now_iso() -> str:
        return ClaudeOAuthService._format_datetime(datetime.now(timezone.utc))

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

    @staticmethod
    def _truncate_state_text(value: Any, limit: int = 500) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."
