"""Agent 配置格式、字段校验和保留注释的局部编辑。"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import tomlkit

MODEL_LIMIT_PRESETS = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "agent_model_limits.json").read_text(encoding="utf-8")
)

CLAUDE_MODELS = (
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_MODEL",
)
CLAUDE_PERMISSION_MODES = ("default", "manual", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions")
CLAUDE_MODES = {
    "attributionHeader": ("CLAUDE_CODE_ATTRIBUTION_HEADER", "1", "0"),
    "disableNonessentialTraffic": ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1", None),
    "experimentalAgentTeams": ("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "1", "0"),
    "enableToolSearch": ("ENABLE_TOOL_SEARCH", "true", "false"),
    "sandbox": ("IS_SANDBOX", "1", "0"),
}
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
MISSING = object()
_TOKEN = re.compile(
    r'\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:[^"\\]|\\.)*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}\[\],:]'
)


def value_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def at_path(data: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return MISSING
        data = data[key]
    return data


def display_path(path: tuple[str, ...]) -> str:
    return ".".join(path)


@dataclass
class _Node:
    start: int
    end: int
    value: Any
    members: dict[str, tuple[int, _Node, int | None]] = field(default_factory=dict)


class JsoncDocument:
    """保留未编辑区域的 JSON 注释、缩进和尾随逗号。"""

    def __init__(self, text: str):
        self.text = text if text.strip() else "{}\n"
        self.tokens = []
        position = 0
        while position < len(self.text):
            match = _TOKEN.match(self.text, position)
            if not match:
                raise ValueError(f"JSON syntax error at offset {position}")
            token = match.group()
            if not token.isspace() and not token.startswith(("//", "/*")):
                self.tokens.append((token, match.start(), match.end()))
            position = match.end()
        self.index = 0
        self.root = self._parse()
        if self.index != len(self.tokens) or not isinstance(self.root.value, dict):
            raise ValueError("Configuration must contain a JSON object")
        self.data = self.root.value

    def _take(self, expected: str | None = None) -> tuple[str, int, int]:
        if self.index >= len(self.tokens):
            raise ValueError("Unexpected end of JSON configuration")
        token = self.tokens[self.index]
        if expected is not None and token[0] != expected:
            raise ValueError(f"JSON syntax error at offset {token[1]}")
        self.index += 1
        return token

    def _peek(self) -> str:
        return self.tokens[self.index][0] if self.index < len(self.tokens) else ""

    def _parse(self) -> _Node:
        token, start, end = self._take()
        if token == "{":
            node = _Node(start, end, {})
            while self._peek() != "}":
                key_token, key_start, _ = self._take()
                if not key_token.startswith('"'):
                    raise ValueError(f"JSON object key expected at offset {key_start}")
                key = json.loads(key_token)
                if key in node.value:
                    raise ValueError("Duplicate JSON object key")
                self._take(":")
                child = self._parse()
                comma = self._take(",")[1] if self._peek() == "," else None
                node.value[key] = child.value
                node.members[key] = (key_start, child, comma)
                if comma is None:
                    break
            node.end = self._take("}")[2]
            return node
        if token == "[":
            values = []
            while self._peek() != "]":
                values.append(self._parse().value)
                if self._peek() != ",":
                    break
                self._take(",")
            return _Node(start, self._take("]")[2], values)
        try:
            return _Node(start, end, json.loads(token))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"JSON value expected at offset {start}") from exc

    def edit(self, path: tuple[str, ...], value: Any = MISSING) -> str:
        node = self.root
        for index, key in enumerate(path):
            if not isinstance(node.value, dict):
                raise ValueError(f"Configuration field {display_path(path[:index])} must be an object")
            member = node.members.get(key)
            if index < len(path) - 1 and member:
                node = member[1]
                continue
            if index < len(path) - 1 and value is not MISSING:
                for remaining in reversed(path[index + 1 :]):
                    value = {remaining: value}
            if value is MISSING:
                if member is None:
                    return self.text
                keys = list(node.members)
                offset = keys.index(key)
                key_start, child, comma = member
                if comma is not None:
                    return self.text[:key_start] + self.text[comma + 1 :]
                if offset:
                    previous = node.members[keys[offset - 1]][2]
                    return self.text[:previous] + self.text[child.end :]
                return self.text[:key_start] + self.text[child.end :]
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if member:
                child = member[1]
                return self.text[: child.start] + encoded + self.text[child.end :]
            line = self.text[self.text.rfind("\n", 0, node.start) + 1 : node.start]
            indent = re.match(r"[ \t]*", line).group() + "  "
            entry = json.dumps(key, ensure_ascii=False) + ": " + encoded
            if node.members:
                _, last, comma = list(node.members.values())[-1]
                position = comma + 1 if comma is not None else last.end
                insertion = (
                    ("" if comma is not None else ",") + "\n" + indent + entry + ("," if comma is not None else "")
                )
            else:
                position = node.start + 1
                insertion = "\n" + indent + entry + "\n" + indent[:-2]
            return self.text[:position] + insertion + self.text[position:]
        raise ValueError("Empty configuration field path")


def parse_document(target: str, text: str) -> dict:
    try:
        return tomlkit.parse(text).unwrap() if target == "codex" else JsoncDocument(text).data
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError(f"Invalid {'TOML' if target == 'codex' else 'JSON'} configuration") from exc


def edit_document(target: str, text: str, edits: dict[tuple[str, ...], Any]) -> str:
    if target != "codex":
        if not text.strip():
            data = {}
            for path, value in edits.items():
                if value is MISSING:
                    continue
                node = data
                for key in path[:-1]:
                    node = node.setdefault(key, {})
                node[path[-1]] = value
            return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        for path, value in edits.items():
            text = JsoncDocument(text).edit(path, value)
        JsoncDocument(text)
        return text.rstrip() + "\n"
    document = tomlkit.parse(text)
    for path, value in edits.items():
        current = document
        for key in path[:-1]:
            if key not in current:
                if value is MISSING:
                    break
                current[key] = tomlkit.table()
            if not isinstance(current[key], dict):
                raise ValueError(f"Configuration field {key} must be a table")
            current = current[key]
        else:
            if value is MISSING:
                current.pop(path[-1], None)
            else:
                current[path[-1]] = deepcopy(value)
    return tomlkit.dumps(document).rstrip() + "\n"


def _integer(value: Any, label: str, maximum: int = 2_000_000_000) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= maximum:
        raise ValueError(f"{label} must be an integer between 1 and {maximum}")
    return int(value)


def _choice(value: Any, choices: tuple[str, ...], label: str) -> str:
    value = str(value or "")
    if value and value not in choices:
        raise ValueError(f"Invalid {label}")
    return value


def provider_key(model: str) -> str:
    return model.partition("/")[0] if "/" in model else "main"


def model_limits(model: str) -> dict:
    """按模型名、别名及日期快照读取页面共用的限制候选。"""
    base = re.sub(r"\[1m\]$", "", model.rsplit("/", 1)[-1].lower())
    names = list(dict.fromkeys((base, base.replace(".", "-"), base.replace("-", "."))))
    for kind in ("ids", "aliases"):
        for name in names:
            for preset in MODEL_LIMIT_PRESETS:
                if name in preset.get(kind, []):
                    return preset
    for name in names:
        for preset in MODEL_LIMIT_PRESETS:
            if any(
                name.startswith(key + "-") and re.fullmatch(r"\d{4}(-?\d{2}){0,2}", name[len(key) + 1 :])
                for key in preset["ids"]
            ):
                return preset
    return {}


def _explicit_clears(agent: str, draft: dict, result: dict) -> dict:
    """用户主动清空的可见字段允许删除，自动清理仍按字段所有权处理。"""
    requested = draft.get("clearFields", [])
    if not isinstance(requested, list) or not all(isinstance(name, str) for name in requested):
        raise ValueError("清空字段列表无效")
    if agent == "claude":
        allowed = {"models." + name: (agent, ("env", name)) for name in CLAUDE_MODELS}
        for name, env in {
            "apiKey": "ANTHROPIC_AUTH_TOKEN",
            "runtime.effortLevel": "CLAUDE_CODE_EFFORT_LEVEL",
            "limits.context": "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            "limits.compact": "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
            "limits.output": "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            **{"runtime." + name: definition[0] for name, definition in CLAUDE_MODES.items()},
        }.items():
            allowed[name] = (agent, ("env", env))
        allowed["runtime.permissionMode"] = (agent, ("permissions", "defaultMode"))
        allowed["runtime.sandboxEnabled"] = (agent, ("sandbox", "enabled"))
        allowed["runtime.skipWebFetchPreflight"] = (agent, ("skipWebFetchPreflight",))
    elif agent == "codex":
        allowed = {"apiKey": ("codexAuth", ("OPENAI_API_KEY",))}
        for name, field_name in {
            "serviceTier": "service_tier",
            "modelReasoningEffort": "model_reasoning_effort",
            "sandboxMode": "sandbox_mode",
            "approvalPolicy": "approval_policy",
        }.items():
            allowed["runtime." + name] = (agent, (field_name,))
        allowed.update(
            {
                "limits.context": (agent, ("model_context_window",)),
                "limits.compact": (agent, ("model_auto_compact_token_limit",)),
                "runtime.memories": (agent, ("features", "memories")),
            }
        )
    else:
        model = str(draft.get("model") or "").strip()
        prefix = ("provider", provider_key(model))
        selected = prefix + ("models", model)
        allowed = {
            "apiKey": (agent, prefix + ("options", "apiKey")),
            "limits.context": (agent, selected + ("limit", "context")),
            "limits.output": (agent, selected + ("limit", "output")),
            "limits.compact": (agent, ("compaction", "reserved")),
            "runtime.reasoningEffort": (agent, selected + ("options", "reasoningEffort")),
        }
    for name in requested:
        if name not in allowed:
            raise ValueError("清空字段不在当前表单范围内")
        target, path = allowed[name]
        if path not in result[target]:
            result[target][path] = MISSING
    return result


def desired_fields(agent: str, draft: dict, catalog: list[str]) -> dict[str, dict[tuple[str, ...], Any]]:
    """从表单生成允许管理的字段，模型 ID 按代理对外格式完整写入。"""
    if (
        not isinstance(draft, dict)
        or not isinstance(draft.get("runtime", {}), dict)
        or not isinstance(draft.get("limits", {}), dict)
    ):
        raise ValueError("Invalid Agent form")
    base_url = str(draft.get("baseUrl") or "").strip()
    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme not in ("http", "https")
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.fragment
        or parsed_url.query
    ):
        raise ValueError("API address must be an absolute HTTP(S) URL without credentials, query or fragment")
    try:
        if parsed_url.port is not None and not 1 <= parsed_url.port <= 65535:
            raise ValueError
    except ValueError as exc:
        raise ValueError("API 地址端口需为 1–65535 的整数") from exc
    api_key = str(draft.get("apiKey") or "").strip()
    if len(base_url) > 4096 or len(api_key) > 16384 or any(char in base_url for char in "\r\n\x00"):
        raise ValueError("API 地址或凭据过长或包含无效字符")
    runtime = draft.get("runtime", {})
    limits = draft.get("limits", {})
    context = _integer(limits.get("context"), "Context window")
    compact = _integer(limits.get("compact"), "Compaction percentage", 100 if agent == "claude" else 99)
    output = _integer(limits.get("output"), "Maximum output")
    if agent != "claude" and context and output and output > context:
        raise ValueError("Maximum output must not exceed context window")
    model = str(draft.get("model") or "").strip()
    fields: dict[tuple[str, ...], Any] = {}
    if agent == "claude":
        models = draft.get("models", {})
        if not isinstance(models, dict):
            raise ValueError("Invalid Claude model selections")
        fields[("env", "ANTHROPIC_BASE_URL")] = base_url
        if api_key:
            fields[("env", "ANTHROPIC_AUTH_TOKEN")] = api_key
        for key in CLAUDE_MODELS:
            configured = str(models.get(key) or "").strip()
            if len(configured) > 512 or any(ord(char) < 32 for char in configured):
                raise ValueError("Claude 模型 ID 无效")
            if configured:
                fields[("env", key)] = configured
        for name, number in (
            ("CLAUDE_CODE_AUTO_COMPACT_WINDOW", context),
            ("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", compact),
            ("CLAUDE_CODE_MAX_OUTPUT_TOKENS", output),
        ):
            if number is not None:
                fields[("env", name)] = str(number)
        effort = _choice(
            runtime.get("effortLevel"), ("low", "medium", "high", "xhigh", "max", "auto", "unset"), "Claude effort"
        )
        if effort:
            fields[("env", "CLAUDE_CODE_EFFORT_LEVEL")] = effort
        permission_mode = _choice(runtime.get("permissionMode"), CLAUDE_PERMISSION_MODES, "Claude permission mode")
        if permission_mode:
            fields[("permissions", "defaultMode")] = permission_mode
        sandbox = _choice(runtime.get("sandboxEnabled", "unset"), ("unset", "enabled", "disabled"), "Claude sandbox")
        if sandbox in ("enabled", "disabled"):
            fields[("sandbox", "enabled")] = sandbox == "enabled"
        preflight = _choice(
            runtime.get("skipWebFetchPreflight", "unset"), ("unset", "enabled", "disabled"), "WebFetch preflight"
        )
        if preflight in ("enabled", "disabled"):
            fields[("skipWebFetchPreflight",)] = preflight == "enabled"
        for name, (env, enabled, disabled) in CLAUDE_MODES.items():
            mode = _choice(runtime.get(name, "unset"), ("unset", "enabled", "disabled"), name)
            if mode in ("enabled", "disabled"):
                fields[("env", env)] = enabled if mode == "enabled" else disabled if disabled is not None else MISSING
        return _explicit_clears(agent, draft, {agent: fields})
    if not model or len(model) > 512 or any(ord(char) < 32 for char in model):
        raise ValueError("Select a valid model")
    provider = provider_key(model)
    if agent == "codex":
        prefix = ("model_providers", provider)
        fields.update(
            {
                ("model_provider",): provider,
                ("model",): model,
                prefix + ("name",): provider,
                prefix + ("base_url",): base_url,
                prefix + ("wire_api",): "responses",
                prefix + ("requires_openai_auth",): bool(api_key),
            }
        )
        # 显式空值表示保持未设置；旧调用未提供参数时继续使用创建默认值。
        context = context or (372000 if "context" not in limits else None)
        if context:
            fields[("model_context_window",)] = context
        compact_tokens = _integer(limits.get("compactTokens"), "压缩上限", (context or 2_000_000_000) - 1)
        if compact_tokens:
            fields[("model_auto_compact_token_limit",)] = compact_tokens
        elif compact:
            if not context:
                raise ValueError("请先设置上下文窗口，再设置压缩阈值")
            fields[("model_auto_compact_token_limit",)] = context * compact // 100
        elif "compact" not in limits:
            fields[("model_auto_compact_token_limit",)] = min(350000, (context or 372000) * 95 // 100)
        for name, setting, choices in (
            ("service_tier", "serviceTier", ("auto", "default", "flex", "priority")),
            ("model_reasoning_effort", "modelReasoningEffort", EFFORTS),
            ("sandbox_mode", "sandboxMode", ("read-only", "workspace-write", "danger-full-access")),
            ("approval_policy", "approvalPolicy", ("untrusted", "on-request", "never")),
        ):
            value = _choice(runtime.get(setting), choices, name)
            if value:
                fields[(name,)] = value
        memories = _choice(runtime.get("memories", "unset"), ("unset", "enabled", "disabled"), "Codex memories")
        if memories in ("enabled", "disabled"):
            fields[("features", "memories")] = memories == "enabled"
        return _explicit_clears(
            agent, draft, {agent: fields, "codexAuth": {("OPENAI_API_KEY",): api_key} if api_key else {}}
        )
    prefix = ("provider", provider)
    fields.update(
        {
            ("model",): provider + "/" + model,
            prefix + ("npm",): "@ai-sdk/openai-compatible",
            prefix + ("name",): provider,
            prefix + ("options", "baseURL"): base_url,
        }
    )
    if api_key:
        fields[prefix + ("options", "apiKey")] = api_key
    effort = _choice(runtime.get("reasoningEffort"), ("none",) + EFFORTS, "opencode effort")
    for configured in dict.fromkeys([*(entry for entry in catalog if provider_key(entry) == provider), model]):
        model_prefix = prefix + ("models", configured)
        fields[model_prefix + ("name",)] = configured
        if configured != model:
            preset = model_limits(configured)
            for name in ("context", "output"):
                if preset.get(name):
                    fields[model_prefix + ("limit", name)] = preset[name]
        if configured == model:
            if context:
                fields[model_prefix + ("limit", "context")] = context
            if output:
                fields[model_prefix + ("limit", "output")] = output
            if effort:
                fields[model_prefix + ("options", "reasoningEffort")] = effort
    reserved = (
        0
        if limits.get("reserved") in (0, "0")
        else _integer(limits.get("reserved"), "压缩预留", (context or 2_000_000_000) - 1)
    )
    if reserved is not None or context and compact:
        fields[("compaction", "reserved")] = reserved if reserved is not None else context - context * compact // 100
    return _explicit_clears(agent, draft, {agent: fields})


def visible_fields(target: str, data: dict) -> dict[str, Any]:
    """返回表单所需字段；路径字符串仅用于展示，不参与字段定位。"""
    result = {}

    def include(path):
        value = at_path(data, path)
        if value is not MISSING:
            result[display_path(path)] = value

    if target == "claude":
        include(("permissions", "defaultMode"))
        include(("sandbox", "enabled"))
        include(("skipWebFetchPreflight",))
        for name in (
            *CLAUDE_MODELS,
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            "CLAUDE_CODE_EFFORT_LEVEL",
            *(entry[0] for entry in CLAUDE_MODES.values()),
        ):
            include(("env", name))
    elif target == "codexAuth":
        include(("OPENAI_API_KEY",))
    elif target == "codex":
        for key in (
            "model",
            "model_provider",
            "model_context_window",
            "model_auto_compact_token_limit",
            "service_tier",
            "model_reasoning_effort",
            "sandbox_mode",
            "approval_policy",
        ):
            include((key,))
        include(("features", "memories"))
        for provider in data.get("model_providers", {}) if isinstance(data.get("model_providers", {}), dict) else ():
            for name in ("name", "base_url", "wire_api", "requires_openai_auth"):
                include(("model_providers", provider, name))
    else:
        include(("model",))
        include(("compaction", "reserved"))
        for provider, body in data.get("provider", {}).items() if isinstance(data.get("provider", {}), dict) else ():
            if not isinstance(body, dict):
                continue
            for tail in (("npm",), ("name",), ("options", "baseURL"), ("options", "apiKey")):
                include(("provider", provider) + tail)
            for model in body.get("models", {}) if isinstance(body.get("models", {}), dict) else ():
                include(("provider", provider, "models", model))
    return result
