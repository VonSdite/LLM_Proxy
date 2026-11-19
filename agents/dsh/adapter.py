"""把 DSH 全局或 profile 补丁映射到表单，保留未编辑的路由和插件。"""

from copy import deepcopy

from src.services.agent_config_documents import MISSING, PROXY_PROVIDER
from src.services.agent_extensions import apply_edits, yaml_dump, yaml_load

ROUTE = PROXY_PROVIDER
PERMISSION_PRESETS = {
    "read-only": {"sandbox": "read-only", "approval": "ask"},
    "workspace-write": {"sandbox": "workspace-write", "approval": "ask"},
    "danger-full-access": {"sandbox": "danger-full-access", "approval": "never"},
}


def _load(target, text):
    data = yaml_load(text) if text.strip() else ([] if target == "dsh" else {})
    if target != "dsh":
        if not isinstance(data, dict) or data.get("version", 1) != 1:
            raise ValueError("DSH credentials must be a version 1 YAML mapping")
        return data
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError("DSH profile must contain a YAML patch list")
    for name in ("llm-pi-ai", "agent-default-model", "permission"):
        matches = [row for row in data if row.get("id") == name]
        if len(matches) > 1:
            raise ValueError(f"DSH patch contains duplicate {name} entries; edit the file first")
        if matches and not isinstance(matches[0].get("config", {}), dict):
            raise ValueError(f"DSH {name} config must be a mapping")
    return data


def _config(rows, name, create=False):
    for row in rows:
        if row.get("id") == name:
            if create:
                return row.setdefault("config", {})
            return row.get("config", {})
    if create:
        row = {"id": name, "config": {}}
        rows.append(row)
        return row["config"]
    return {}


def parse(target, text):
    data = _load(target, text)
    if target != "dsh":
        return data
    selection = _config(data, "agent-default-model")
    providers = _config(data, "llm-pi-ai").get("providers", {})
    if not isinstance(providers, dict) or not isinstance(providers.get(ROUTE, {}), dict):
        raise ValueError("DSH providers must be a mapping")
    connection = providers.get(ROUTE, {})
    models = connection.get("models", [])
    if not isinstance(models, list) or not all(isinstance(model, dict) for model in models):
        raise ValueError("DSH models must contain model mappings")
    model = next((model for model in models if model.get("id") == selection.get("model")), {})
    permission = _config(data, "permission")
    permission_view = {}
    if "defaultPreset" in permission:
        mode = permission["defaultPreset"]
        presets = permission.get("presets", {})
        # 同名预设可被用户改写；仅将语义一致的组合回显为标准选项。
        expected = PERMISSION_PRESETS.get(mode) if isinstance(mode, str) else None
        configured = presets.get(mode) if isinstance(presets, dict) and isinstance(mode, str) else None
        if (
            expected
            and configured is not None
            and (
                not isinstance(configured, dict) or any(configured.get(key) != value for key, value in expected.items())
            )
        ):
            mode = {"custom": True}
        permission_view["defaultPreset"] = deepcopy(mode)
    return {
        "connection": deepcopy(connection),
        "selection": deepcopy(selection),
        "limits": {key: model[key] for key in ("contextWindow", "maxTokens") if key in model},
        "permission": permission_view,
    }


def prepare(draft, fields):
    """空凭据允许无认证端点，已有环境凭据引用在未清空时保留。"""
    values = draft["values"]
    if "model" in values:
        selected = fields["dsh"][("selection", "model")]
        fields["dsh"][("selection", "provider")] = ROUTE if selected is not MISSING else MISSING
        if selected is MISSING:
            fields["dsh"][("selection", "reasoningEffort")] = MISSING
    if values.get("api-key"):
        fields["dsh"][("connection", "apiKeyEnv")] = "LLM_PROXY_API_KEY"
    elif "api-key" in values:
        fields["dsh"][("connection", "apiKeyEnv")] = MISSING
    return fields


def display_path(target, path):
    if target != "dsh":
        return ".".join(path)
    prefixes = {
        "connection": "llm-pi-ai.config.providers.proxy",
        "selection": "agent-default-model.config",
        "limits": "llm-pi-ai.config.providers.proxy.models[]",
        "permission": "permission.config",
    }
    return ".".join((prefixes[path[0]], *path[1:]))


def edit(target, text, edits):
    data = _load(target, text)
    if target != "dsh":
        return yaml_dump(apply_edits(data, edits))
    permission_path = ("permission", "defaultPreset")
    if permission_path in edits:
        mode = edits[permission_path]
        permission = _config(data, "permission", mode is not MISSING)
        if mode is MISSING:
            permission.pop("defaultPreset", None)
        else:
            # 补丁替换整行 config；显式保留三个标准预设，包含 schema 默认缺少的只读项。
            presets = permission.setdefault("presets", deepcopy(PERMISSION_PRESETS))
            if not isinstance(presets, dict):
                raise ValueError("DSH 权限预设列表格式无效，请先在配置文件中整理")
            chosen = presets.setdefault(mode, {})
            if not isinstance(chosen, dict):
                raise ValueError("DSH 权限预设格式无效，请先在配置文件中整理")
            chosen.update(PERMISSION_PRESETS[mode])
            permission["defaultPreset"] = mode
    original = parse(target, text)
    view = apply_edits(deepcopy(original), edits)
    model_id = view["selection"].get("model")
    if edits.get(("selection", "model")) is MISSING:
        # DSH 补丁替换整组 config；删除 config 才能继承下层完整默认选择。
        for row in list(data):
            if row.get("id") == "agent-default-model":
                row.pop("config", None)
                if set(row) <= {"id", "name"}:
                    data.remove(row)
    elif model_id:
        selection = _config(data, "agent-default-model", True)
        apply_edits(selection, {path[1:]: value for path, value in edits.items() if path[0] == "selection"})
        selection["provider"] = ROUTE
    config = _config(data, "llm-pi-ai", True)
    providers = config.setdefault("providers", {})
    connection = providers.setdefault(ROUTE, {})
    apply_edits(connection, {path[1:]: value for path, value in edits.items() if path[0] == "connection"})
    models = connection.setdefault("models", [])
    if not model_id:
        return yaml_dump(data)
    model = next((item for item in models if item.get("id") == model_id), None)
    if model is None:
        model = {"id": model_id}
        models.append(model)
    apply_edits(model, {path[1:]: value for path, value in edits.items() if path[0] == "limits"})
    context, output = model.get("contextWindow"), model.get("maxTokens")
    if isinstance(context, (int, float)) and isinstance(output, (int, float)) and output > context:
        raise ValueError("最大输出容量不能超过模型上下文")
    return yaml_dump(data)
