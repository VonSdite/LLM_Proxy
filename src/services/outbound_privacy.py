#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游请求出站脱敏与响应恢复。"""

from __future__ import annotations

import ipaddress
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PLACEHOLDER_PREFIX = "__LLM_PROXY_REDACTED_"
PLACEHOLDER_RE = re.compile(r"__LLM_PROXY_REDACTED_[A-Z0-9_]+_\d{4}_[0-9a-f]{12}__")
# 占位符可能嵌套（如 DATABASE_URL 的值整体脱敏后内部还含 URL 凭据占位符），恢复时迭代到稳定。
MAX_RESTORE_PASSES = 8
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
SSH_KEY_RE = re.compile(r"\bssh-(?:rsa|dss|ed25519|ecdsa)\s+[A-Za-z0-9+/=]{40,}")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
COMMON_SECRET_RE = re.compile(
    r"\b(?:"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"sk-ant-[A-Za-z0-9_-]{16,}|"
    r"AIza[0-9A-Za-z_-]{30,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"ASIA[0-9A-Z]{16}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"[spr]k_(?:live|test)_[A-Za-z0-9]{16,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|"
    r"hf_[A-Za-z0-9]{30,}|"
    r"npm_[A-Za-z0-9]{30,}|"
    r"xox[abprs]-[0-9A-Za-z-]{10,}|"
    r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}|"
    r"ya29\.[A-Za-z0-9_-]{20,}|"
    r"[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}"
    r")\b"
)
WEBHOOK_URL_RE = re.compile(
    r"(?i)https://(?:"
    r"hooks\.slack\.com/services/[A-Za-z0-9+/_-]{20,}"
    r"|(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/[0-9]{10,}/[A-Za-z0-9_-]{20,}"
    r")"
)
HEADER_SECRET_RE = re.compile(
    r"(?im)\b(?P<name>authorization|cookie|set-cookie|api-key|x-api-key|x-goog-api-key|"
    r"x-auth-token|x-session-token)\s*:\s*(?P<value>[^\r\n]+)"
)
# 连接串/URL 内嵌凭据（scheme://user:pass@host）。占位符吞掉结尾的 @，避免邮箱正则二次吞并主机名。
URL_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<userinfo>[^\s/?#]*:[^\s/?#]*)@"
)
AUTH_SCHEME_SECRET_RE = re.compile(
    r"(?i)\b(?P<prefix>(?:bearer|basic)\s+)(?P<value>[A-Za-z0-9._~+/=-]{16,})(?![A-Za-z0-9._~+/=-])"
)
CLI_SECRET_RE = re.compile(
    r"(?i)(?P<prefix>(?:"
    r"(?:curl|wget)\s+(?:\S+\s+)*?(?:-u|--user)\s+['\"]?(?![^\s'\"]*//)(?=[^\s'\"]*[:@])"
    r"|sshpass\s+(?:\S+\s+)*?-p\s+['\"]?"
    r"|mysql(?:dump)?\s+(?:\S+\s+)*?-p(?=\S)"
    r"))(?P<value>[^\s'\"]{3,})"
)
# 分页游标类 token 参数不是凭据，赋值时不脱敏。
ASSIGNMENT_PAGINATION_TOKEN_PREFIXES = frozenset(
    {"next", "page", "continue", "continuation", "cursor", "verify", "resume"}
)
ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)(?P<prefix>(?:"
    # 自然语言形式：关键词 + 分隔符（允许 是/为 后再跟冒号、逗号等组合分隔符）
    r"\b(?:"
    r"password|passwd|pwd|api[_\s-]?key|access[_\s-]?key(?:[_\s-]?id)?|"
    r"secret(?:[_\s-]?key)?|secret[_\s-]?access[_\s-]?key|client[_\s-]?secret|"
    r"refresh[_\s-]?token|access[_\s-]?token|id[_\s-]?token|session(?:[_\s-]?id)?|"
    r"auth[_\s-]?token|account[_\s-]?key|token|ak|sk"
    r")\b"
    r"(?:\s*(?:[:=：>＝]|是|为)[\s：:=,，>＝]*|\s+(?:is|as|为|是)?\s*)"
    r"|"
    # 环境变量/复合标识符形式：DB_PASSWORD=、AWS_SECRET_ACCESS_KEY= 等。
    # 词边界保证 X_PASSWORD_FILE 这类带后缀的变量不被误伤；ak/sk 太短，只保留上面的词边界形式。
    r"\b[A-Za-z0-9_]*(?:"
    r"password|passwd|pwd|api[_]?key|access[_]?key(?:[_]?id)?|"
    r"secret(?:[_]?key)?|secret[_]?access[_]?key|client[_]?secret|"
    r"refresh[_]?token|access[_]?token|id[_]?token|session(?:[_]?id)?|"
    r"auth[_]?token|account[_]?key|database[_]?url|db[_]?url|"
    r"authorization|credentials?|cookie|token"
    r")\b\s*[=：:＝]\s*"
    r"|"
    r"(?:密码|口令|密钥|令牌)"
    r"(?:\s*(?:[:=：>＝]|是|为)[\s：:=,，>＝]*|\s+(?:is|as|为|是)?\s*)"
    r")\s*[\"']?)"
    r"(?P<value>[A-Za-z0-9._~!@#$%^&*+/=?:-]{3,})"
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@(?!-?\d+x\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,63}\b")
# 数字边界同时排除小数（0.13812345678）和小数尾巴（13812345678.5）。
PHONE_RE = re.compile(r"(?<!\d)(?<!\d\.)(?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}(?!\d)(?!\.?\d)")
ID_CARD_RE = re.compile(
    r"(?<![0-9Xx])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?!\d)"
)
# 银行卡候选：15-19 位数字，允许空格/连字符分组，命中后还需通过 Luhn 校验以降低订单号误伤。
BANK_CARD_CANDIDATE_RE = re.compile(r"(?<!\d)\d(?:[ -]?\d){14,18}(?!\d)")
IPV4_RE = re.compile(r"(?<![A-Za-z0-9_.-])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9_.-])")
IPV6_CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9_:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![A-Za-z0-9_:])")

SENSITIVE_FIELD_COMPACT_NAMES = {
    "ak",
    "apikey",
    "accesskey",
    "accesskeyid",
    "accountkey",
    "authorization",
    "auth",
    "authtoken",
    "basicauth",
    "bearer",
    "clientsecret",
    "connectionstring",
    "cookie",
    "credential",
    "credentials",
    "databaseurl",
    "dburl",
    "idtoken",
    "pass",
    "passphrase",
    "password",
    "passwd",
    "privatekey",
    "pwd",
    "refreshtoken",
    "secret",
    "secretaccesskey",
    "secretkey",
    "session",
    "sessionid",
    "sk",
    "token",
    "webhook",
    "webhookurl",
}
SENSITIVE_FIELD_KEYWORDS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
    "account_key",
    "accountkey",
    "private_key",
    "token",
    "authorization",
    "cookie",
    "credential",
    "session",
    "basic_auth",
    "database_url",
    "databaseurl",
    "db_url",
    "webhook",
    "密码",
    "口令",
    "密钥",
    "令牌",
)
# 含 token/session 等关键词但语义上是采样参数的请求字段，不作为敏感字段处理。
SAFE_FIELD_COMPACT_NAMES = frozenset(
    {
        "maxtokens",
        "maxtoken",
        "maxoutputtokens",
        "maxcompletiontokens",
        "numtokens",
        "tokenlimit",
    }
)
# 客户端请求头中属于凭据的头，开启安全脱敏时不转发给上游。
SENSITIVE_UPSTREAM_HEADER_NAMES = frozenset(
    {
        "cookie",
        "set-cookie",
        "api-key",
        "x-api-key",
        "x-goog-api-key",
        "x-auth-token",
        "x-session-token",
        "x-csrf-token",
        "x-amz-security-token",
    }
)


@dataclass(frozen=True)
class OutboundPrivacyResult:
    """出站脱敏结果。"""

    body: dict[str, Any]
    context: "OutboundPrivacyContext"


class OutboundPrivacyContext:
    """单次请求内的占位符映射。"""

    def __init__(self) -> None:
        self._nonce = secrets.token_hex(6)
        self._original_to_placeholder: dict[str, str] = {}
        self._placeholder_to_original: dict[str, str] = {}
        self._placeholder_kind: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._placeholder_to_original)

    @property
    def replacement_count(self) -> int:
        return len(self._placeholder_to_original)

    def placeholder_for(self, original: str, kind: str) -> str:
        """为原文分配本次请求内稳定的占位符。"""
        if not original:
            return original
        existing = self._original_to_placeholder.get(original)
        if existing is not None:
            return existing

        normalized_kind = _normalize_kind(kind)
        placeholder = (
            f"{PLACEHOLDER_PREFIX}{normalized_kind}_{len(self._placeholder_to_original) + 1:04d}_{self._nonce}__"
        )
        self._original_to_placeholder[original] = placeholder
        self._placeholder_to_original[placeholder] = original
        self._placeholder_kind[placeholder] = normalized_kind
        return placeholder

    def restore_text(self, text: str) -> str:
        """把下游响应文本中的占位符恢复为原文，嵌套占位符迭代恢复到稳定。"""
        if not self._placeholder_to_original or PLACEHOLDER_PREFIX not in text:
            return text
        restored = text
        for _ in range(MAX_RESTORE_PASSES):
            updated = restored
            for placeholder, original in self._placeholder_to_original.items():
                if placeholder in updated:
                    updated = updated.replace(placeholder, original)
            if updated == restored:
                break
            restored = updated
        return restored

    def restore_stream_text_fragment(self, text: str) -> tuple[str, str]:
        """恢复流式文本片段，并返回需要等待下一片段的尾部。"""
        if not self._placeholder_to_original:
            return text, ""

        pending_start = _find_pending_placeholder_start(text)
        if pending_start is None:
            return self.restore_text(text), ""

        restored = self.restore_text(text[:pending_start])
        return restored, text[pending_start:]

    def restore_payload(self, payload: Any) -> Any:
        """递归恢复 JSON 结构中的占位符。"""
        return _restore_value(payload, self)

    def restore_bytes(self, payload: bytes) -> bytes:
        """恢复 UTF-8 字节响应中的占位符，非文本响应保持原样。"""
        if not self._placeholder_to_original or PLACEHOLDER_PREFIX.encode("ascii") not in payload:
            return payload
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return payload
        restored = self.restore_text(text)
        if restored == text:
            return payload
        return restored.encode("utf-8")


class OutboundPrivacyService:
    """高性能确定性脱敏服务。"""

    def sanitize_request_body(self, body: dict[str, Any]) -> OutboundPrivacyResult:
        """脱敏请求体，并返回请求级恢复上下文。"""
        context = OutboundPrivacyContext()
        sanitized = _sanitize_value(body, context, path=())
        if not isinstance(sanitized, dict):
            sanitized = dict(body)
        return OutboundPrivacyResult(body=sanitized, context=context)

    def sanitize_request_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        """剔除不应转发给上游的凭据类请求头。"""
        return {
            key: value
            for key, value in headers.items()
            if str(key).lower() not in SENSITIVE_UPSTREAM_HEADER_NAMES
        }


def _sanitize_value(value: Any, context: OutboundPrivacyContext, *, path: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        if _should_skip_string(value, path=path):
            return value
        if path and _is_sensitive_field_name(path[-1]):
            return context.placeholder_for(value, _kind_for_field(path[-1]))
        return _sanitize_text(value, context)

    if isinstance(value, Mapping):
        changed = False
        result: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            next_path = (*path, key_text)
            if path == () and key_text == "model":
                sanitized_item = item
            elif _is_sensitive_field_name(key_text) and _is_scalar_secret_value(item):
                sanitized_item = context.placeholder_for(str(item), _kind_for_field(key_text))
            else:
                sanitized_item = _sanitize_value(item, context, path=next_path)
            result[key] = sanitized_item
            changed = changed or sanitized_item is not item
        return result if changed else value

    if isinstance(value, list):
        changed = False
        items: list[Any] = []
        for index, item in enumerate(value):
            sanitized_item = _sanitize_value(item, context, path=(*path, str(index)))
            items.append(sanitized_item)
            changed = changed or sanitized_item is not item
        return items if changed else value

    if isinstance(value, tuple):
        changed = False
        items = []
        for index, item in enumerate(value):
            sanitized_item = _sanitize_value(item, context, path=(*path, str(index)))
            items.append(sanitized_item)
            changed = changed or sanitized_item is not item
        return tuple(items) if changed else value

    return value


def _sanitize_text(text: str, context: OutboundPrivacyContext) -> str:
    if not text:
        return text

    sanitized = PRIVATE_KEY_RE.sub(lambda match: context.placeholder_for(match.group(0), "private_key"), text)
    sanitized = SSH_KEY_RE.sub(lambda match: context.placeholder_for(match.group(0), "ssh_key"), sanitized)
    sanitized = HEADER_SECRET_RE.sub(lambda match: _replace_header_secret(match, context), sanitized)
    sanitized = URL_CREDENTIALS_RE.sub(lambda match: _replace_url_credentials(match, context), sanitized)
    sanitized = ASSIGNMENT_SECRET_RE.sub(lambda match: _replace_assignment_secret(match, context), sanitized)
    sanitized = AUTH_SCHEME_SECRET_RE.sub(lambda match: _replace_auth_scheme_secret(match, context), sanitized)
    sanitized = JWT_RE.sub(lambda match: context.placeholder_for(match.group(0), "jwt"), sanitized)
    sanitized = COMMON_SECRET_RE.sub(lambda match: context.placeholder_for(match.group(0), "secret"), sanitized)
    sanitized = WEBHOOK_URL_RE.sub(lambda match: context.placeholder_for(match.group(0), "webhook_url"), sanitized)
    sanitized = CLI_SECRET_RE.sub(lambda match: _replace_cli_secret(match, context), sanitized)
    sanitized = EMAIL_RE.sub(lambda match: context.placeholder_for(match.group(0), "email"), sanitized)
    sanitized = PHONE_RE.sub(lambda match: context.placeholder_for(match.group(0), "phone"), sanitized)
    sanitized = ID_CARD_RE.sub(lambda match: context.placeholder_for(match.group(0), "id_card"), sanitized)
    sanitized = BANK_CARD_CANDIDATE_RE.sub(lambda match: _replace_bank_card(match, context), sanitized)
    sanitized = IPV4_RE.sub(lambda match: _replace_ip_candidate(match.group(0), context), sanitized)
    sanitized = IPV6_CANDIDATE_RE.sub(lambda match: _replace_ip_candidate(match.group(0), context), sanitized)
    return sanitized


def _replace_header_secret(match: re.Match[str], context: OutboundPrivacyContext) -> str:
    name = match.group("name")
    value = match.group("value").strip()
    placeholder = context.placeholder_for(value, _kind_for_field(name))
    return f"{name}: {placeholder}"


def _replace_url_credentials(match: re.Match[str], context: OutboundPrivacyContext) -> str:
    userinfo = match.group("userinfo")
    placeholder = context.placeholder_for(f"{userinfo}@", "url_credentials")
    return f"{match.group('scheme')}{placeholder}"


def _replace_assignment_secret(match: re.Match[str], context: OutboundPrivacyContext) -> str:
    value = match.group("value").strip()
    if not value or not _looks_like_inline_secret(value):
        return match.group(0)
    prefix = match.group("prefix")
    if _is_prose_like_assignment(prefix, value):
        return match.group(0)
    placeholder = context.placeholder_for(value, _kind_for_field(prefix))
    return f"{prefix}{placeholder}"


def _is_prose_like_assignment(prefix: str, value: str) -> bool:
    """判断赋值命中是否更像普通文本而非口令，避免误伤。

    英文关键词后跟纯字母词（Password Policy、token: string、session: timeout）
    或分页游标（next_token=）时跳过；中文关键词和 X_PASSWORD= 强赋值不受影响。
    """
    if _is_pagination_cursor_assignment(prefix):
        return True
    # 句尾标点会被值字符集吞进来，判断前先剥掉。
    if not value.rstrip(".,;:!?").isalpha():
        return False
    if any(keyword in prefix for keyword in ("密码", "口令", "密钥", "令牌")):
        return False
    if prefix.rstrip(" \t\r\n\"'").endswith(("=", "＝")):
        return False
    return True


def _is_pagination_cursor_assignment(prefix: str) -> bool:
    identifier = re.split(r"[=：:＝>]", prefix, maxsplit=1)[0]
    normalized = re.sub(r"[^a-z0-9]", "", identifier.lower())
    return any(normalized == f"{name}token" for name in ASSIGNMENT_PAGINATION_TOKEN_PREFIXES)


def _replace_auth_scheme_secret(match: re.Match[str], context: OutboundPrivacyContext) -> str:
    prefix = match.group("prefix")
    value = match.group("value")
    if prefix.lower().startswith("basic") and value.isalpha():
        # 纯字母更像普通英文单词，避免把 "basic xxxxx" 误当凭据。
        return match.group(0)
    kind = "basic_credential" if prefix.lower().startswith("basic") else "bearer_token"
    placeholder = context.placeholder_for(value, kind)
    return f"{prefix}{placeholder}"


def _replace_cli_secret(match: re.Match[str], context: OutboundPrivacyContext) -> str:
    value = match.group("value")
    placeholder = context.placeholder_for(value, "credential")
    return f"{match.group('prefix')}{placeholder}"


def _replace_bank_card(match: re.Match[str], context: OutboundPrivacyContext) -> str:
    candidate = match.group(0)
    digits = candidate.replace(" ", "").replace("-", "")
    if not (15 <= len(digits) <= 19) or not _luhn_valid(digits):
        return candidate
    return context.placeholder_for(candidate, "bank_card")


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = ord(char) - ord("0")
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _replace_ip_candidate(candidate: str, context: OutboundPrivacyContext) -> str:
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return candidate
    return context.placeholder_for(candidate, "ip")


def _restore_value(value: Any, context: OutboundPrivacyContext) -> Any:
    if isinstance(value, str):
        return context.restore_text(value)

    if isinstance(value, Mapping):
        changed = False
        result: dict[Any, Any] = {}
        for key, item in value.items():
            restored_item = _restore_value(item, context)
            result[key] = restored_item
            changed = changed or restored_item is not item
        return result if changed else value

    if isinstance(value, list):
        changed = False
        items: list[Any] = []
        for item in value:
            restored_item = _restore_value(item, context)
            items.append(restored_item)
            changed = changed or restored_item is not item
        return items if changed else value

    if isinstance(value, tuple):
        changed = False
        items = []
        for item in value:
            restored_item = _restore_value(item, context)
            items.append(restored_item)
            changed = changed or restored_item is not item
        return tuple(items) if changed else value

    return value


def _find_pending_placeholder_start(text: str) -> int | None:
    search_pos = 0
    last_complete_end = 0
    while True:
        prefix_index = text.find(PLACEHOLDER_PREFIX, search_pos)
        if prefix_index < 0:
            break
        match = PLACEHOLDER_RE.match(text, prefix_index)
        if match is None:
            return prefix_index
        last_complete_end = match.end()
        search_pos = match.end()

    max_suffix_length = min(len(PLACEHOLDER_PREFIX) - 1, len(text))
    for suffix_length in range(max_suffix_length, 0, -1):
        suffix_start = len(text) - suffix_length
        if suffix_start >= last_complete_end and PLACEHOLDER_PREFIX.startswith(text[-suffix_length:]):
            return suffix_start
    return None


def _is_scalar_secret_value(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    return isinstance(value, str | int | float)


def _should_skip_string(value: str, *, path: tuple[str, ...]) -> bool:
    if not value:
        return True
    stripped = value.lstrip()
    if stripped.startswith("data:"):
        return True
    if len(value) > 4096 and _looks_like_base64(value):
        return True
    return bool(path and path[-1] in {"model"})


def _looks_like_base64(value: str) -> bool:
    sample = value[:4096].strip()
    if len(sample) < 1024:
        return False
    base64_chars = sum(1 for char in sample if char.isalnum() or char in "+/=\n\r")
    return base64_chars / len(sample) > 0.98


def _looks_like_inline_secret(value: str) -> bool:
    """判断自然语言赋值右侧是否像一个单段口令或密钥。"""
    if len(value) < 3:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._~!@#$%^&*+/=?:-]+", value))


def _is_sensitive_field_name(field_name: str) -> bool:
    normalized = _normalize_field_name(field_name)
    if normalized in SAFE_FIELD_COMPACT_NAMES:
        return False
    if normalized in SENSITIVE_FIELD_COMPACT_NAMES:
        return True
    lower_name = str(field_name or "").strip().lower()
    return any(keyword in lower_name for keyword in SENSITIVE_FIELD_KEYWORDS)


def _kind_for_field(field_name: str) -> str:
    normalized = _normalize_field_name(field_name)
    if "cookie" in normalized:
        return "cookie"
    if (
        "password" in normalized
        or "passwd" in normalized
        or "pwd" in normalized
        or normalized in {"pass", "passphrase"}
        or "密码" in field_name
        or "口令" in field_name
    ):
        return "password"
    if "authorization" in normalized or normalized == "auth":
        return "authorization"
    if "token" in normalized or "令牌" in field_name:
        return "token"
    if "key" in normalized or normalized in {"ak", "sk"} or "密钥" in field_name:
        return "secret"
    if "session" in normalized:
        return "session"
    return "secret"


def _normalize_field_name(field_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(field_name or "").lower())


def _normalize_kind(kind: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", kind).strip("_").upper()
    return normalized or "SECRET"
