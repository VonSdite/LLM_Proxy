"""兼容 Hermes 的字符串模型简写，保留已有 YAML 注释。"""

from src.services.agent_extensions import apply_edits, yaml_dump, yaml_load


def parse(target, text):
    data = yaml_load(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("Hermes config must contain a YAML mapping")
    model = data.get("model")
    if isinstance(model, str) or model is None:
        data["model"] = {"default": model} if model else {}
    elif not isinstance(model, dict):
        raise ValueError("Hermes model must be a string or mapping")
    return data


def edit(target, text, edits):
    return yaml_dump(apply_edits(parse(target, text), edits))
