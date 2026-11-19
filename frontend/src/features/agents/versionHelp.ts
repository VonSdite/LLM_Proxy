import type { AgentId } from '@/api/agents'

// 版本依据及未确认项见 docs/agent-configuration.md；功能发布不等于字段首次支持。
const claude: Record<string, string> = {
  ANTHROPIC_DEFAULT_HAIKU_MODEL: '已确认支持：Claude Code 2.0.17。',
  ANTHROPIC_DEFAULT_OPUS_MODEL: '最低支持版本：Claude Code 1.0.88。',
  ANTHROPIC_DEFAULT_SONNET_MODEL: '最低支持版本：Claude Code 1.0.88。',
  ANTHROPIC_DEFAULT_FABLE_MODEL: '当前官方文档支持自定义 Fable 别名，字段首次支持版本未确认。',
  effort: '版本：xhigh 档位从 Claude Code 2.1.111 引入，档位是否有效还取决于模型。',
  context: '已确认支持：Claude Code 2.1.304。',
  permissionMode:
    '版本：Claude Code 2.1.257 起，bypassPermissions 不再从项目级或本地项目配置生效；auto 也应设置在用户级配置。',
  sandboxEnabled: '最低支持版本：Claude Code 2.0.24（macOS / Linux）；原生 Windows 不支持。',
  attribution:
    '最低支持版本：本页“禁用”写入 attribution: false，需要 Claude Code 2.1.281。对象形式从 2.0.62 支持；旧版遇到 false 可能跳过整个设置文件。',
  attributionHeader: '版本：Claude Code 2.1.181 之前，自定义 API 地址的标识块含逐请求 Token。',
  experimentalAgentTeams: '最低支持版本：Claude Code 2.1.32；仍属于实验性功能。',
  enableToolSearch:
    '版本：Claude Code 2.1.7 默认启用自动工具搜索，2.1.9 支持 auto:N；使用自定义 API 地址时，建议至少 2.1.72，该版支持通过变量显式开启。',
}

export function agentVersionHelp(agent: AgentId, field: string): string[] {
  if (agent === 'claude') return claude[field] ? [claude[field]] : []
  if (agent === 'codex') return []
  return agent === 'opencode' ? ['适用版本：Opencode v1。'] : []
}
