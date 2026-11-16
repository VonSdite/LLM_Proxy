"""Claude 表单与受管配置字段的适配入口。"""

from src.services.agent_config_documents import desired_fields, visible_fields


def desired(draft, catalog):
    return desired_fields("claude", draft, catalog)


def visible(target, data):
    return visible_fields(target, data)
