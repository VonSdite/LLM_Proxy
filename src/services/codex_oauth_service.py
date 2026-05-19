#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex OAuth 登录与认证文件管理。"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from ..application.app_context import AppContext
from ..utils.net import build_requests_proxies
from ..utils.proxy_warning import ProxyWarningRequired, request_with_proxy_warning_retry

CODEX_AUTH_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
CODEX_SCOPE = "openid email profile offline_access"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_MODEL_REFERENCE_URLS = (
    "https://raw.githubusercontent.com/router-for-me/models/refs/heads/main/models.json",
    "https://models.router-for.me/models.json",
)
CODEX_USER_AGENT = "codex_cli_rs/0.124.0 (Debian 13.0.0; x86_64) WindowsTerminal"
OAUTH_SESSION_TTL_SECONDS = 10 * 60
DEFAULT_CODEX_MODEL_IDS: tuple[str, ...] = ()
AUTH_FAILURE_ERROR_TYPES = {
    "authentication_error",
    "invalid_api_key",
    "invalid_grant",
    "refresh_token_reused",
    "token_refresh_failed",
}


@dataclass(frozen=True)
class CodexAuthCandidate:
    """可用于一次 Codex 请求的认证文件快照。"""

    name: str
    path: Path
    access_token: str
    account_id: str
    email: str
    plan_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class _CodexOAuthSession:
    """Codex OAuth 临时会话。"""

    state: str
    code_verifier: str
    code_challenge: str
    expires_at: float


class CodexOAuthService:
    """处理 Codex OAuth 授权、认证文件生成与配额查询。"""

    def __init__(self, ctx: AppContext):
        self._logger = ctx.logger
        self._config_manager = ctx.config_manager
        self._auth_dir = ctx.root_path / "data" / "oauth" / "codex"
        self._models_file = self._auth_dir / "models.json"
        self._state_file = self._auth_dir / ".state" / "auth_files.json"
        self._sessions: dict[str, _CodexOAuthSession] = {}
        self._quota_cooldowns: dict[str, float] = {}
        self._quota_refresh_locks: dict[str, threading.Lock] = {}
        self._quota_refresh_lock_guard = threading.RLock()

    def start_login(self) -> dict[str, Any]:
        """生成新的 Codex OAuth 授权链接。"""
        self._purge_expired_sessions()
        state = secrets.token_urlsafe(24)
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        expires_at = time.time() + OAUTH_SESSION_TTL_SECONDS
        self._sessions[state] = _CodexOAuthSession(
            state=state,
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            expires_at=expires_at,
        )

        params = {
            "client_id": CODEX_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": CODEX_REDIRECT_URI,
            "scope": CODEX_SCOPE,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "login",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
        }
        authorization_url = f"{CODEX_AUTH_URL}?{urlencode(params)}"
        return {
            "authorization_url": authorization_url,
            "auth_url": authorization_url,
            "state": state,
            "redirect_uri": CODEX_REDIRECT_URI,
            "expires_at": self._format_timestamp(expires_at),
        }

    def complete_login(self, callback_url: str) -> dict[str, Any]:
        """根据回调 URL 换取 token 并写入认证文件。"""
        parsed_callback = self._parse_callback_url(callback_url)
        state = parsed_callback["state"]
        code = parsed_callback["code"]
        error = parsed_callback["error"]
        if error:
            raise ValueError(f"Codex OAuth failed: {error}")
        if not state:
            raise ValueError("Callback URL missing state")
        if not code:
            raise ValueError("Callback URL missing code")

        self._purge_expired_sessions()
        session = self._sessions.get(state)
        if session is None:
            raise ValueError("OAuth state is unknown or expired")

        token_data = self._exchange_code_for_tokens(code, session.code_verifier)
        claims = self._parse_jwt_claims(str(token_data.get("id_token") or ""))
        auth_info = claims.get("https://api.openai.com/auth")
        if not isinstance(auth_info, dict):
            auth_info = {}

        email = str(claims.get("email") or "").strip()
        if not email:
            raise ValueError("Codex token storage missing account information")
        account_id = str(auth_info.get("chatgpt_account_id") or "").strip()
        plan_type = str(auth_info.get("chatgpt_plan_type") or "").strip()
        expires_in = int(token_data.get("expires_in") or 0)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in, 0))
        auth_payload = {
            "type": "codex",
            "id_token": token_data.get("id_token") or "",
            "access_token": token_data.get("access_token") or "",
            "refresh_token": token_data.get("refresh_token") or "",
            "account_id": account_id,
            "email": email,
            "plan_type": plan_type,
            "expired": self._format_datetime(expires_at),
            "last_refresh": self._format_datetime(datetime.now(timezone.utc)),
        }
        auth_file = self._write_auth_file(auth_payload, state)
        self._delete_auth_file_state_entry(auth_file.name)
        self._sessions.pop(state, None)
        self._logger.info("Codex OAuth auth file generated: file=%s email=%s", auth_file.name, email or "<empty>")
        return {
            "status": "ok",
            "auth_file": self._build_auth_file_entry(auth_file),
        }

    def list_auth_files(self) -> dict[str, Any]:
        """列出本地 Codex OAuth 认证文件。"""
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
        """删除指定 Codex OAuth 认证文件及其本地状态。"""
        auth_file = self._resolve_auth_file(name)
        deleted_name = auth_file.name
        auth_file.unlink()
        self._quota_cooldowns.pop(deleted_name, None)
        self._delete_auth_file_state_entry(deleted_name)
        self._logger.info("Codex OAuth auth file deleted: file=%s", deleted_name)
        return {
            "status": "ok",
            "deleted": deleted_name,
        }

    def list_models(self) -> dict[str, Any]:
        """返回当前 Codex OAuth 可用模型目录。"""
        models = self._build_model_entries(self._load_model_ids())
        return {
            "status": "ok",
            "provider": "codex",
            "models": models,
            "total": len(models),
            "reference_urls": list(CODEX_MODEL_REFERENCE_URLS),
        }

    def add_model(self, model_id: str) -> dict[str, Any]:
        """添加一个本地 Codex 模型 ID。"""
        normalized_model_id = self._normalize_model_id(model_id)
        model_ids = list(self._load_model_ids())
        if normalized_model_id in model_ids:
            return self.list_models()
        model_ids.append(normalized_model_id)
        self._write_model_ids(model_ids)
        return self.list_models()

    def delete_model(self, model_id: str) -> dict[str, Any]:
        """删除一个本地 Codex 模型 ID。"""
        normalized_model_id = self._normalize_model_id(model_id)
        model_ids = [
            current_model_id for current_model_id in self._load_model_ids() if current_model_id != normalized_model_id
        ]
        self._write_model_ids(model_ids)
        return self.list_models()

    def list_model_names(self) -> tuple[str, ...]:
        """返回当前认证文件实际可用的 Codex 模型名。"""
        if not self._iter_auth_file_paths():
            return ()
        return tuple(sorted(dict.fromkeys(self._load_model_ids())))

    def has_model(self, model_name: str) -> bool:
        """判断模型名是否属于当前 Codex OAuth 可代理模型。"""
        normalized_model = str(model_name or "").strip()
        return bool(normalized_model) and normalized_model in set(self.list_model_names())

    def iter_auth_candidates_for_model(self, model_name: str) -> list[CodexAuthCandidate]:
        """按填满一个账号再使用下一个账号的策略返回认证候选。"""
        normalized_model = str(model_name or "").strip()
        if not normalized_model:
            return []

        if normalized_model not in set(self._load_model_ids()):
            return []

        candidates: list[CodexAuthCandidate] = []
        self._purge_quota_cooldowns()
        state_files = self._load_auth_file_state().get("files")
        for path in self._iter_auth_file_paths():
            file_state = {}
            if isinstance(state_files, dict) and isinstance(state_files.get(path.name), dict):
                file_state = state_files[path.name]
            if self._is_auth_failure_state(file_state):
                continue
            candidate = self._build_auth_candidate(path)
            if candidate is None:
                continue
            if self._is_quota_cooling_down(candidate.name):
                continue
            candidates.append(candidate)
        return candidates

    def mark_auth_file_quota_exhausted(
        self,
        name: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        """把认证文件标记为临时配额耗尽，后续请求优先尝试其他账号。"""
        normalized_name = Path(str(name or "").strip()).name
        if not normalized_name:
            return
        cooldown_seconds = retry_after_seconds if retry_after_seconds is not None else 60.0
        self._quota_cooldowns[normalized_name] = time.time() + max(float(cooldown_seconds), 1.0)

    def record_auth_file_success(self, name: str) -> None:
        """记录认证文件最近一次 Codex 模型代理成功。"""
        normalized_name = self._normalize_auth_file_name(name)
        if not normalized_name:
            return
        self._quota_cooldowns.pop(normalized_name, None)
        self._update_auth_file_state(
            normalized_name,
            {
                "usage_status": "success",
                "usage_status_message": "success",
                "usage_status_code": 200,
                "usage_error_type": "",
                "usage_retry_after_seconds": None,
                "usage_status_updated_at": self._now_iso(),
            },
        )

    def record_auth_file_failure(
        self,
        name: str,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        """记录认证文件最近一次 Codex 模型代理失败。"""
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
                "usage_retry_after_seconds": retry_after_seconds,
                "usage_status_updated_at": self._now_iso(),
            },
        )

    def get_auth_file_quota(self, name: str) -> dict[str, Any]:
        """查询指定认证文件的 Codex 使用配额。"""
        auth_file = self._resolve_auth_file(name)
        quota_lock = self._get_quota_refresh_lock(auth_file.name)
        if not quota_lock.acquire(blocking=False):
            return self._build_skipped_quota_refresh_result(auth_file.name)
        try:
            payload = self._read_auth_file(auth_file)
            refreshed = False
            if self._is_auth_payload_expired(payload) and str(payload.get("refresh_token") or "").strip():
                try:
                    payload = self._refresh_auth_file(auth_file, payload)
                except Exception as exc:
                    self.record_auth_file_failure(
                        auth_file.name,
                        f"Token refresh failed: {exc}",
                        status_code=401,
                        error_type="token_refresh_failed",
                    )
                    raise
                refreshed = True

            response = self._request_auth_file_quota(payload)
            if (
                response.status_code >= 400
                and not refreshed
                and str(payload.get("refresh_token") or "").strip()
                and self._is_auth_error_response(response)
            ):
                try:
                    payload = self._refresh_auth_file(auth_file, payload)
                except Exception as exc:
                    self.record_auth_file_failure(
                        auth_file.name,
                        f"Token refresh failed: {exc}",
                        status_code=401,
                        error_type="token_refresh_failed",
                    )
                    raise
                refreshed = True
                response = self._request_auth_file_quota(payload)
            if response.status_code >= 400:
                if self._is_auth_error_response(response):
                    self.record_auth_file_failure(
                        auth_file.name,
                        self._response_error_text(response),
                        status_code=response.status_code,
                        error_type="authentication_error",
                    )
                raise ValueError(f"Codex quota request returned {response.status_code}: {response.text}")
            usage_payload = response.json()
            if not isinstance(usage_payload, dict):
                raise ValueError("Codex quota response must be a JSON object")

            result = {
                "status": "ok",
                "refreshed": refreshed,
                "refreshed_at": self._now_iso(),
                "plan_type": self._normalize_text(
                    usage_payload.get("plan_type") or usage_payload.get("planType") or payload.get("plan_type")
                ),
                "windows": self._build_quota_windows(usage_payload),
                "raw": usage_payload,
            }
            self._store_auth_file_quota(auth_file.name, result)
            self._sync_quota_cooldown_from_quota(auth_file.name, result)
            self._clear_auth_file_auth_failure(auth_file.name)
            return result
        except Exception as exc:
            self._store_auth_file_quota_error(auth_file.name, str(exc))
            raise
        finally:
            quota_lock.release()

    def _get_quota_refresh_lock(self, name: str) -> threading.Lock:
        """返回单个认证文件的配额刷新锁。"""
        with self._quota_refresh_lock_guard:
            quota_lock = self._quota_refresh_locks.get(name)
            if quota_lock is None:
                quota_lock = threading.Lock()
                self._quota_refresh_locks[name] = quota_lock
            return quota_lock

    def _build_skipped_quota_refresh_result(self, name: str) -> dict[str, Any]:
        """构造重复配额刷新被跳过时的兼容响应。"""
        state = self._load_auth_file_state()
        files = state.get("files")
        file_state = files.get(name) if isinstance(files, dict) else None
        if not isinstance(file_state, dict):
            file_state = {}
        raw_quota = file_state.get("quota")
        quota = cast(dict[str, Any], raw_quota) if isinstance(raw_quota, dict) else {}
        result: dict[str, Any] = dict(quota)
        result.update(
            {
                "status": "ok",
                "skipped": True,
                "reason": "quota_refresh_in_progress",
                "message": "Quota refresh already in progress",
                "refreshed_at": result.get("refreshed_at") or str(file_state.get("quota_refreshed_at") or ""),
            }
        )
        result.setdefault("windows", [])
        return result

    def _request_auth_file_quota(self, payload: dict[str, Any]) -> requests.Response:
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("Auth file does not contain access_token")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": CODEX_USER_AGENT,
        }
        account_id = str(payload.get("account_id") or "").strip()
        if account_id:
            headers["Chatgpt-Account-Id"] = account_id

        return self._request_with_proxy_warning_retry(
            "GET",
            CODEX_USAGE_URL,
            headers=headers,
            timeout=20,
        )

    def _iter_auth_file_paths(self) -> list[Path]:
        if not self._auth_dir.exists():
            return []
        paths = [
            path for path in self._auth_dir.glob("*.json") if path.name != self._models_file.name and path.is_file()
        ]
        return sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)

    def _load_model_ids(self) -> list[str]:
        if not self._models_file.exists():
            return list(DEFAULT_CODEX_MODEL_IDS)
        try:
            with self._models_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            self._logger.warning("Codex models file ignored: error=%s", exc)
            return list(DEFAULT_CODEX_MODEL_IDS)
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
            self._logger.warning("Codex auth file state ignored: error=%s", exc)
            return {"files": {}}
        files = payload.get("files")
        if not isinstance(files, dict):
            return {"files": {}}
        return {"files": files}

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
            self._logger.warning("Codex auth file state write failed: file=%s error=%s", name, exc)

    def _store_auth_file_quota(self, name: str, quota: dict[str, Any]) -> None:
        refreshed_at = str(quota.get("refreshed_at") or self._now_iso())
        self._update_auth_file_state(
            name,
            {
                "quota": quota,
                "quota_error": "",
                "quota_refreshed_at": refreshed_at,
            },
        )

    def _store_auth_file_quota_error(self, name: str, message: str) -> None:
        self._update_auth_file_state(
            name,
            {
                "quota_error": self._truncate_state_text(message),
                "quota_refreshed_at": self._now_iso(),
            },
        )

    def _delete_auth_file_state_entry(self, name: str) -> None:
        state = self._load_auth_file_state()
        files = state.get("files")
        if not isinstance(files, dict) or name not in files:
            return
        files.pop(name, None)
        try:
            self._write_auth_file_state(state)
        except Exception as exc:
            self._logger.warning("Codex auth file state delete failed: file=%s error=%s", name, exc)

    def _sync_quota_cooldown_from_quota(self, name: str, quota: dict[str, Any]) -> None:
        """根据最新配额刷新结果同步内存冷却状态。"""
        normalized_name = self._normalize_auth_file_name(name)
        if not normalized_name:
            return
        if self._is_quota_exhausted(quota):
            self.mark_auth_file_quota_exhausted(
                normalized_name,
                retry_after_seconds=self._quota_retry_after_seconds(quota),
            )
            return
        self._quota_cooldowns.pop(normalized_name, None)

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
                "usage_retry_after_seconds": None,
                "usage_status_updated_at": self._now_iso(),
            }
        )
        try:
            self._write_auth_file_state(state)
        except Exception as exc:
            self._logger.warning("Codex auth file state clear failed: file=%s error=%s", name, exc)

    @classmethod
    def _is_auth_failure_state(cls, file_state: dict[str, Any]) -> bool:
        status_code = file_state.get("usage_status_code")
        try:
            if int(status_code) == 401:
                return True
        except (TypeError, ValueError):
            pass

        error_type = str(file_state.get("usage_error_type") or "").strip().lower()
        if error_type in AUTH_FAILURE_ERROR_TYPES:
            return True

        message = str(file_state.get("usage_status_message") or "").strip().lower()
        return cls._is_auth_error_text(message)

    @classmethod
    def _is_auth_error_response(cls, response: requests.Response) -> bool:
        if response.status_code == 401:
            return True
        return cls._is_auth_error_text(str(getattr(response, "text", "") or ""))

    @staticmethod
    def _response_error_text(response: requests.Response) -> str:
        text = str(getattr(response, "text", "") or "").strip()
        try:
            payload = response.json()
        except Exception:
            return text or f"HTTP {response.status_code}"
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("type") or "").strip()
                if message:
                    return message
            if isinstance(error, str) and error.strip():
                return error.strip()
            detail = str(payload.get("detail") or payload.get("message") or "").strip()
            if detail:
                return detail
        return text or f"HTTP {response.status_code}"

    @staticmethod
    def _is_auth_error_text(value: str) -> bool:
        text = value.lower()
        return (
            "invalid or expired token" in text
            or "invalid_api_key" in text
            or "invalid_grant" in text
            or "refresh_token_reused" in text
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

    def _build_auth_candidate(self, path: Path) -> CodexAuthCandidate | None:
        try:
            payload = self._read_auth_file(path)
        except Exception as exc:
            self._logger.warning("Codex auth file ignored: file=%s error=%s", path.name, exc)
            return None

        if self._is_auth_payload_expired(payload):
            refresh_token = str(payload.get("refresh_token") or "").strip()
            if refresh_token:
                try:
                    payload = self._refresh_auth_file(path, payload)
                except Exception as exc:
                    self._logger.warning("Codex auth file refresh failed: file=%s error=%s", path.name, exc)
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

        return CodexAuthCandidate(
            name=path.name,
            path=path,
            access_token=access_token,
            account_id=str(payload.get("account_id") or "").strip(),
            email=str(payload.get("email") or "").strip(),
            plan_type=self._normalize_codex_plan_type(payload.get("plan_type")),
            payload=payload,
        )

    @staticmethod
    def _normalize_codex_plan_type(value: Any) -> str:
        plan_type = str(value or "").strip().lower()
        return plan_type or "unknown"

    @staticmethod
    def _is_quota_exhausted(quota: dict[str, Any]) -> bool:
        windows = quota.get("windows")
        if not isinstance(windows, list):
            return False

        codex_windows = [
            window
            for window in windows
            if isinstance(window, dict) and str(window.get("label") or "").strip().lower().startswith("codex")
        ]
        if not codex_windows:
            return False

        for window in codex_windows:
            remaining_percent = CodexOAuthService._parse_float(window.get("remaining_percent"))
            used_percent = CodexOAuthService._parse_float(window.get("used_percent"))
            if remaining_percent is not None and remaining_percent <= 0:
                return True
            if used_percent is not None and used_percent >= 100:
                return True
        return False

    @staticmethod
    def _quota_retry_after_seconds(quota: dict[str, Any]) -> float | None:
        windows = quota.get("windows")
        if not isinstance(windows, list):
            return None
        for window in windows:
            if not isinstance(window, dict):
                continue
            remaining_percent = CodexOAuthService._parse_float(window.get("remaining_percent"))
            used_percent = CodexOAuthService._parse_float(window.get("used_percent"))
            if (remaining_percent is not None and remaining_percent <= 0) or (
                used_percent is not None and used_percent >= 100
            ):
                return None
        return None

    def _purge_quota_cooldowns(self) -> None:
        now = time.time()
        expired_names = [name for name, expires_at in self._quota_cooldowns.items() if expires_at <= now]
        for name in expired_names:
            self._quota_cooldowns.pop(name, None)

    def _is_quota_cooling_down(self, name: str) -> bool:
        expires_at = self._quota_cooldowns.get(name)
        return expires_at is not None and expires_at > time.time()

    def _exchange_code_for_tokens(self, code: str, code_verifier: str) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code",
            "client_id": CODEX_CLIENT_ID,
            "code": code,
            "redirect_uri": CODEX_REDIRECT_URI,
            "code_verifier": code_verifier,
        }
        response = self._request_with_proxy_warning_retry(
            "POST",
            CODEX_TOKEN_URL,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise ValueError(f"Token exchange failed with status {response.status_code}: {response.text}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Token response must be a JSON object")
        if not payload.get("access_token"):
            raise ValueError("Token response missing access_token")
        return payload

    def _refresh_auth_file(self, auth_file: Path, payload: dict[str, Any]) -> dict[str, Any]:
        refresh_token = str(payload.get("refresh_token") or "").strip()
        response = self._request_with_proxy_warning_retry(
            "POST",
            CODEX_TOKEN_URL,
            data={
                "client_id": CODEX_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "openid profile email",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise ValueError(f"Token refresh failed with status {response.status_code}: {response.text}")
        token_data = response.json()
        if not isinstance(token_data, dict):
            raise ValueError("Refresh response must be a JSON object")

        expires_in = int(token_data.get("expires_in") or 0)
        next_payload = dict(payload)
        next_payload["access_token"] = token_data.get("access_token") or payload.get("access_token") or ""
        next_payload["refresh_token"] = token_data.get("refresh_token") or refresh_token
        next_payload["id_token"] = token_data.get("id_token") or payload.get("id_token") or ""
        next_payload["expired"] = self._format_datetime(
            datetime.now(timezone.utc) + timedelta(seconds=max(expires_in, 0))
        )
        next_payload["last_refresh"] = self._format_datetime(datetime.now(timezone.utc))
        self._write_json_file(auth_file, next_payload)
        self._clear_auth_file_auth_failure(auth_file.name)
        return next_payload

    def _write_auth_file(self, payload: dict[str, Any], state: str) -> Path:
        self._auth_dir.mkdir(parents=True, exist_ok=True)
        file_name = self._build_credential_file_name(payload, state)
        auth_file = self._auth_dir / file_name
        self._write_json_file(auth_file, payload)
        return auth_file

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    def _request_with_proxy_warning_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        request_options = self._build_request_options()
        normalized_method = str(method or "").strip().upper()

        def send_request() -> requests.Response:
            if normalized_method == "GET":
                return requests.get(
                    url,
                    allow_redirects=False,
                    **kwargs,
                    **request_options,
                )
            if normalized_method == "POST":
                return requests.post(
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
                logger=self._logger,
                log_context=f"oauth_url={url}",
            )
        except ProxyWarningRequired as exc:
            raise ValueError(str(exc)) from exc

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

    def _build_auth_file_entry(
        self,
        path: Path,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._purge_quota_cooldowns()
        payload = self._read_auth_file(path)
        token_status = "expired" if self._is_auth_payload_expired(payload) else "active"
        state_files = (state or {}).get("files")
        file_state = {}
        if isinstance(state_files, dict) and isinstance(state_files.get(path.name), dict):
            file_state = state_files[path.name]
        quota = file_state.get("quota") if isinstance(file_state.get("quota"), dict) else None
        availability = self._build_auth_file_availability(path.name, payload, file_state, quota)
        return {
            "name": path.name,
            "path": str(path),
            "type": payload.get("type") or "codex",
            "email": payload.get("email") or "",
            "account_id": payload.get("account_id") or "",
            "plan_type": payload.get("plan_type") or self._extract_plan_type_from_payload(payload),
            "status": token_status,
            "status_message": "Token expired" if token_status == "expired" else "Ready",
            "token_status": token_status,
            "token_status_message": "Token expired" if token_status == "expired" else "Ready",
            "availability_status": availability["status"],
            "availability_status_message": availability["message"],
            "availability_retry_at": availability["retry_at"],
            "usage_status": str(file_state.get("usage_status") or "unknown"),
            "usage_status_message": str(file_state.get("usage_status_message") or ""),
            "usage_status_code": file_state.get("usage_status_code"),
            "usage_error_type": str(file_state.get("usage_error_type") or ""),
            "usage_status_updated_at": str(file_state.get("usage_status_updated_at") or ""),
            "quota": quota,
            "quota_error": str(file_state.get("quota_error") or ""),
            "quota_refreshed_at": str(file_state.get("quota_refreshed_at") or ""),
            "expired": payload.get("expired") or "",
            "last_refresh": payload.get("last_refresh") or "",
            "size": path.stat().st_size,
            "modified": int(path.stat().st_mtime),
        }

    def _build_auth_file_availability(
        self,
        name: str,
        payload: dict[str, Any],
        file_state: dict[str, Any],
        quota: dict[str, Any] | None,
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

        quota_retry_at = self._quota_cooldowns.get(name)
        if quota_retry_at is not None and quota_retry_at > time.time():
            reason = self._build_availability_failure_reason(file_state)
            message = "配额冷却中：上游返回配额耗尽，暂时跳过此认证文件"
            if reason:
                message = f"配额冷却中：上游返回 {reason}，暂时跳过此认证文件"
            return self._availability("quota_cooldown", message, quota_retry_at)

        if quota is not None and self._is_quota_exhausted(quota):
            return self._availability("quota_exhausted", "配额已耗尽：最近一次配额刷新显示 Codex 窗口无剩余额度")

        if self._is_auth_payload_expired(payload):
            if refresh_token:
                return self._availability(
                    "refresh_required", "待刷新：access_token 已过期，请求前会使用 refresh_token 自动刷新"
                )
            return self._availability(
                "auth_check_required",
                "待验证：access_token 已过期且缺少 refresh_token，会先用当前 access_token 请求一次",
            )

        usage_status = str(file_state.get("usage_status") or "").strip()
        if usage_status == "success":
            return self._availability("available", "可用：最近一次请求成功")
        return self._availability("available", "可用：未命中过滤或冷却条件")

    def _build_availability_failure_reason(self, file_state: dict[str, Any]) -> str:
        message = str(file_state.get("usage_status_message") or "").strip()
        if message and message != "success":
            return self._truncate_state_text(message)
        error_type = str(file_state.get("usage_error_type") or "").strip()
        if error_type:
            return self._truncate_state_text(error_type)
        return ""

    def _availability(
        self,
        status: str,
        message: str,
        retry_at: float | None = None,
    ) -> dict[str, str]:
        return {
            "status": status,
            "message": message,
            "retry_at": self._format_timestamp(retry_at) if retry_at is not None else "",
        }

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

    def _build_credential_file_name(self, payload: dict[str, Any], state: str) -> str:
        del state
        email = str(payload.get("email") or "").strip()
        if not email:
            raise ValueError("Codex token storage missing account information")
        account_id = str(payload.get("account_id") or "").strip()
        plan_type = self._normalize_plan_type_for_filename(str(payload.get("plan_type") or ""))
        account_hash = hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:8] if account_id else ""
        if not plan_type:
            return f"codex-{email}.json"
        if plan_type == "team":
            return f"codex-{account_hash}-{email}-{plan_type}.json"
        return f"codex-{email}-{plan_type}.json"

    @staticmethod
    def _normalize_plan_type_for_filename(plan_type: str) -> str:
        parts = [
            part.strip().lower() for part in re.split(r"[^A-Za-z0-9]+", str(plan_type or "").strip()) if part.strip()
        ]
        return "-".join(parts)

    @staticmethod
    def _generate_code_verifier() -> str:
        return base64.urlsafe_b64encode(secrets.token_bytes(96)).decode("ascii").rstrip("=")

    @staticmethod
    def _generate_code_challenge(code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _format_timestamp(value: float) -> str:
        return CodexOAuthService._format_datetime(datetime.fromtimestamp(value, tz=timezone.utc))

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _now_iso() -> str:
        return CodexOAuthService._format_datetime(datetime.now(timezone.utc))

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

    def _is_auth_payload_expired(self, payload: dict[str, Any]) -> bool:
        expires_at = self._parse_datetime(payload.get("expired"))
        if expires_at is None:
            return False
        return expires_at <= datetime.now(timezone.utc)

    @staticmethod
    def _parse_callback_url(callback_url: str) -> dict[str, str]:
        parsed = urlparse(str(callback_url or "").strip())
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("Callback URL must be a complete URL")
        query = parse_qs(parsed.query)
        return {
            "state": (query.get("state") or [""])[0].strip(),
            "code": (query.get("code") or [""])[0].strip(),
            "error": ((query.get("error") or query.get("error_description") or [""])[0]).strip(),
        }

    @staticmethod
    def _parse_jwt_claims(token: str) -> dict[str, Any]:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode(f"{payload}{padding}")
            claims = json.loads(decoded.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return {}
        return claims if isinstance(claims, dict) else {}

    def _extract_plan_type_from_payload(self, payload: dict[str, Any]) -> str:
        claims = self._parse_jwt_claims(str(payload.get("id_token") or ""))
        auth_info = claims.get("https://api.openai.com/auth")
        if isinstance(auth_info, dict):
            return str(auth_info.get("chatgpt_plan_type") or "").strip()
        return ""

    def _purge_expired_sessions(self) -> None:
        now = time.time()
        expired_states = [state for state, session in self._sessions.items() if session.expires_at <= now]
        for state in expired_states:
            self._sessions.pop(state, None)

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_auth_file_name(name: str) -> str:
        cleaned_name = Path(str(name or "").strip()).name
        if not cleaned_name or cleaned_name != str(name or "").strip():
            return ""
        return cleaned_name

    @staticmethod
    def _truncate_state_text(value: Any) -> str:
        normalized = str(value or "").strip()
        return normalized[:1000]

    def _build_quota_windows(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        self._append_rate_limit_windows(windows, "Codex", payload.get("rate_limit") or payload.get("rateLimit"))
        self._append_rate_limit_windows(
            windows,
            "Code Review",
            payload.get("code_review_rate_limit") or payload.get("codeReviewRateLimit"),
        )
        additional = payload.get("additional_rate_limits") or payload.get("additionalRateLimits")
        if isinstance(additional, list):
            for item in additional:
                if not isinstance(item, dict):
                    continue
                label = (
                    item.get("limit_name")
                    or item.get("limitName")
                    or item.get("metered_feature")
                    or item.get("meteredFeature")
                    or "Additional"
                )
                self._append_rate_limit_windows(windows, str(label), item.get("rate_limit") or item.get("rateLimit"))
        return windows

    def _append_rate_limit_windows(self, windows: list[dict[str, Any]], label: str, rate_limit: Any) -> None:
        if not isinstance(rate_limit, dict):
            return
        for key, fallback_label in (("primary_window", "5 小时"), ("secondary_window", "7 天")):
            window = rate_limit.get(key) or rate_limit.get(self._camelize(key))
            if not isinstance(window, dict):
                continue
            used_percent = self._parse_float(window.get("used_percent") or window.get("usedPercent"))
            remaining_percent = None if used_percent is None else max(0.0, min(100.0, 100.0 - used_percent))
            windows.append(
                {
                    "label": f"{label} {fallback_label}",
                    "used_percent": used_percent,
                    "remaining_percent": remaining_percent,
                    "reset_label": self._format_reset_label(window),
                    "reset_at": self._resolve_reset_at(window),
                }
            )

    @staticmethod
    def _camelize(value: str) -> str:
        parts = value.split("_")
        return parts[0] + "".join(part.capitalize() for part in parts[1:])

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _format_reset_label(self, window: dict[str, Any]) -> str:
        reset_seconds = self._parse_float(
            window.get("reset_after_seconds")
            or window.get("resetAfterSeconds")
            or window.get("resets_in_seconds")
            or window.get("resetsInSeconds")
        )
        if reset_seconds is not None:
            return self._format_seconds(reset_seconds)
        reset_at = self._normalize_text(window.get("resets_at") or window.get("resetsAt"))
        return reset_at or "-"

    def _resolve_reset_at(self, window: dict[str, Any]) -> str:
        reset_seconds = self._parse_float(
            window.get("reset_after_seconds")
            or window.get("resetAfterSeconds")
            or window.get("resets_in_seconds")
            or window.get("resetsInSeconds")
        )
        if reset_seconds is not None:
            return self._format_datetime(datetime.now(timezone.utc) + timedelta(seconds=max(reset_seconds, 0.0)))

        reset_at = window.get("resets_at") or window.get("resetsAt")
        parsed_reset = self._parse_epoch_or_datetime(reset_at)
        if parsed_reset is not None:
            return self._format_datetime(parsed_reset)
        return self._normalize_text(reset_at)

    @classmethod
    def _parse_epoch_or_datetime(cls, value: Any) -> datetime | None:
        if value is None:
            return None
        parsed_number = cls._parse_float(value)
        if parsed_number is not None:
            timestamp = parsed_number / 1000 if parsed_number > 100000000000 else parsed_number
            try:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return None
        return cls._parse_datetime(value)

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        total = max(int(seconds), 0)
        hours, remainder = divmod(total, 3600)
        minutes, _ = divmod(remainder, 60)
        if hours:
            return f"{hours}小时{minutes}分钟后"
        return f"{minutes}分钟后"
