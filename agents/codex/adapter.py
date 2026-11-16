"""Codex 的配置及认证文件共用保存事务。"""

from src.services.agent_config_documents import desired_fields, visible_fields


def desired(draft, catalog):
    return desired_fields("codex", draft, catalog)


def visible(target, data):
    return visible_fields(target, data)
