"""Claude 环境变量的两阶段删除，仅处理已核实空串可撤销的字段。"""

from .agent_config_documents import MISSING, at_path, edit_document, parse_document

# 与 cc-switch 对 Claude Code 2.1.318 的热加载验证保持一致；不泛化到任意环境变量。
HOT_UNSET_ENV = frozenset(
    {
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        "CLAUDE_CODE_DISABLE_THINKING",
        "CLAUDE_CODE_EXTRA_BODY",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
        "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT",
        "CLAUDE_CODE_ENABLE_FINE_GRAINED_TOOL_STREAMING",
        "CLAUDE_CODE_DISABLE_ARTIFACT",
        "CLAUDE_CODE_AUTO_MODE_SERVER",
        "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        *(
            f"CLAUDE_CODE_USE_{name}"
            for name in ("BEDROCK", "VERTEX", "FOUNDRY", "GATEWAY", "MANTLE", "ANTHROPIC_AWS", "ANTHROPIC_GOOGLE_CLOUD")
        ),
        *(
            f"ANTHROPIC_DEFAULT_{tier}_MODEL{suffix}"
            for tier in ("HAIKU", "SONNET", "OPUS", "FABLE")
            for suffix in ("", "_NAME", "_DESCRIPTION")
        ),
    }
)
UNSET_DELAY = 10


def remove_pending(raw: bytes | None, keys) -> bytes | None:
    """仅移除仍为空串的已登记字段，保留用户后来修改的字段及其余文本。"""
    if raw is None or not keys:
        return raw
    text = raw.decode("utf-8-sig")
    data = parse_document("claude", text)
    edits = {("env", key): MISSING for key in keys if key in HOT_UNSET_ENV and at_path(data, ("env", key)) == ""}
    return edit_document("claude", text, edits).encode() if edits else raw


def stage_unset(before: bytes | None, after: bytes, pending_keys) -> tuple[bytes, list[str]]:
    """删除先写空串；合并仍待删除的字段，重新设置的字段退出清理范围。"""
    try:
        previous = parse_document("claude", (before or b"{}").decode("utf-8-sig"))
    except ValueError:
        previous = {}
    text = after.decode("utf-8-sig")
    desired = parse_document("claude", text)
    keys = sorted(
        key
        for key in HOT_UNSET_ENV
        if at_path(desired, ("env", key)) is MISSING
        and isinstance(old := at_path(previous, ("env", key)), str)
        and (old or key in pending_keys)
    )
    return (edit_document("claude", text, {("env", key): "" for key in keys}).encode(), keys) if keys else (after, [])
