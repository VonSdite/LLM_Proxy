"""将 V2 当前服务与模型映射到通用表单，保留 JSONC 和其他版本字段。"""

import re
from copy import deepcopy

from src.services.agent_config_documents import (
    MISSING,
    PROXY_PROVIDER,
    at_path,
    edit_document,
    parse_document,
    provider_ids,
)

COMPATIBLE = "@opencode/ai/providers/openai-compatible"


def _location(data):
    reference = data.get("model", "")
    if not isinstance(reference, str):
        raise ValueError("Opencode V2 model must be a string")
    provider, separator, model = reference.partition("/")
    return (PROXY_PROVIDER, model) if separator and provider == PROXY_PROVIDER and model else (PROXY_PROVIDER, "")


def _paths(data):
    provider, model = _location(data)
    root = ("providers", provider)
    entry = (*root, "models", model)
    result = {
        ("connection", "package"): (*root, "package"),
    }
    if model:
        result.update(
            {
                ("modelSettings", "reasoningEffort"): (*entry, "settings", "reasoningEffort"),
                ("limits", "context"): (*entry, "limit", "context"),
                ("limits", "output"): (*entry, "limit", "output"),
            }
        )
    for key in ("baseURL", "apiKey", "package"):
        override = (*entry, key) if key == "package" else (*entry, "settings", key)
        if model and at_path(data, override) is not MISSING:
            result[("connection", key)] = override
        elif key != "package":
            result[("connection", key)] = (*root, "settings", key)
    return result


def parse(target, text):
    data = parse_document("json", text)
    view = {
        key: deepcopy(data[key])
        for key in ("permissions", "compaction", "snapshots", "update", "disabled_providers", "share")
        if key in data
    }
    for path, source in _paths(data).items():
        value = at_path(data, source)
        if value is not MISSING:
            view.setdefault(path[0], {})[path[1]] = deepcopy(value)
    provider, model = _location(data)
    if model:
        actual = at_path(data, ("providers", provider, "models", model, "modelID"))
        view["selection"] = {"model": model if actual is MISSING else actual}
    buffer = at_path(data, ("compaction", "buffer"))
    if buffer is not MISSING:
        view["buffer"] = str(buffer) if type(buffer) is int else deepcopy(buffer)
    return view


def visible(target, data):
    paths = {
        "base-url": ("connection", "baseURL"),
        "api-key": ("connection", "apiKey"),
        "model": ("selection", "model"),
        "protocol": ("connection", "package"),
        "effort": ("modelSettings", "reasoningEffort"),
        "context": ("limits", "context"),
        "output": ("limits", "output"),
        "buffer": ("buffer",),
        "permission": ("permissions",),
        "auto-compress": ("compaction", "auto"),
        "snapshots": ("snapshots",),
        "update": ("update",),
        "disabled-providers": ("disabled_providers",),
        "share": ("share",),
    }
    result = {key: deepcopy(value) for key, path in paths.items() if (value := at_path(data, path)) is not MISSING}
    disabled = result.get("disabled-providers")
    if isinstance(disabled, list) and all(isinstance(item, str) for item in disabled):
        result["disabled-providers"] = ", ".join(disabled)
    elif "disabled-providers" in result:
        result["disabled-providers"] = {"custom": True}
    rules = result.get("permission")
    if (
        isinstance(rules, list)
        and len(rules) == 1
        and isinstance(rules[0], dict)
        and set(rules[0]) == {"action", "resource", "effect"}
        and rules[0]["action"] == rules[0]["resource"] == "*"
        and rules[0]["effect"] in ("allow", "ask", "deny")
    ):
        result["permission"] = rules[0]["effect"]
    elif "permission" in result:
        result["permission"] = {"custom": True}
    return result


def prepare(draft, fields):
    edits = fields["opencodeV2"]
    disabled = edits.get(("disabled_providers",), MISSING)
    if disabled is not MISSING:
        edits[("disabled_providers",)] = provider_ids(disabled)
    permission = edits.get(("permissions",), MISSING)
    if permission is not MISSING:
        edits[("permissions",)] = [{"action": "*", "resource": "*", "effect": permission}]
    buffer = edits.get(("buffer",), MISSING)
    if buffer is not MISSING and not re.fullmatch(r"(?:[1-9]\d?|[1-9]\d?\.\d+)%|[1-9]\d*", buffer):
        raise ValueError("压缩预留请填写 1% 至小于 100% 的比例，或正整数 Token 数")
    return fields


def display_path(target, path):
    if path == ("selection", "model"):
        return "model / modelID"
    if path == ("buffer",):
        return "compaction.buffer"
    if path == ("connection", "package"):
        return "providers.proxy.package（或模型级覆盖）"
    prefixes = {
        "connection": "providers.proxy.settings",
        "modelSettings": "providers.proxy.models.<当前模型>.settings",
        "limits": "providers.proxy.models.<当前模型>.limit",
    }
    return ".".join((prefixes.get(path[0], path[0]), *path[1:]))


def edit(target, text, edits):
    data = parse_document("json", text)
    original = parse(target, text)
    provider, alias = _location(data)
    model = edits.get(("selection", "model"), at_path(original, ("selection", "model")))
    translated = {}
    paths = _paths(data)
    if edits.get(("selection", "model")) is MISSING:
        translated[("model",)] = MISSING
        alias = ""
    elif ("selection", "model") in edits:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Opencode V2 模型 ID 无效")
        alias = model
        translated[("model",)] = f"{provider}/{alias}"
        translated[("providers", provider, "models", alias, "modelID")] = model
        # 切换模型时将表单仍显示的容量和思考强度写入新条目，原模型保持原样。
        for path in paths:
            if path[0] in ("limits", "modelSettings"):
                value = edits.get(path, at_path(original, path))
                if value is not MISSING:
                    suffix = ("limit", path[1]) if path[0] == "limits" else ("settings", path[1])
                    translated[("providers", provider, "models", alias, *suffix)] = value
            elif path[0] == "connection" and "models" in paths[path]:
                value = edits.get(path, at_path(original, path))
                if value is not MISSING:
                    suffix = ("package",) if path[1] == "package" else ("settings", path[1])
                    translated[("providers", provider, "models", alias, *suffix)] = value
        updated = {**data, "model": f"{provider}/{alias}"}
        paths = _paths(updated)
        for path in paths:
            if path[0] == "connection" and "models" in _paths(data)[path]:
                suffix = ("package",) if path[1] == "package" else ("settings", path[1])
                paths[path] = ("providers", provider, "models", alias, *suffix)
    for path, value in edits.items():
        if path == ("selection", "model"):
            continue
        if path[0] in ("limits", "modelSettings") and not alias:
            continue
        if path == ("buffer",):
            translated[("compaction", "buffer")] = int(value) if isinstance(value, str) and value.isdigit() else value
        else:
            translated[paths.get(path, path)] = value
    if (
        provider == PROXY_PROVIDER
        and any(path[0] == "providers" for path in translated)
        and at_path(data, ("providers", provider)) is MISSING
    ):
        translated.setdefault(("providers", provider, "package"), COMPATIBLE)
    result = edit_document("json", text, translated)
    limits = parse(target, result).get("limits", {})
    context, output = limits.get("context"), limits.get("output")
    if type(context) in (int, float) and type(output) in (int, float) and output > context:
        raise ValueError("最大输出不能超过模型上下文")
    return result
