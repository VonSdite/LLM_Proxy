import type {
  AgentBootstrap,
  AgentDraft,
  AgentId,
  AgentMachine,
  AgentSnapshot,
  LimitCandidate,
  LimitKind,
  ModelPreset,
} from '@/api/agents'

export const CLAUDE_MODEL_KEYS = [
  'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'ANTHROPIC_DEFAULT_OPUS_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_MODEL',
] as const
export const CLAUDE_MODES = [
  {
    key: 'attributionHeader',
    label: 'Attribution Header',
    env: 'CLAUDE_CODE_ATTRIBUTION_HEADER',
    enabled: '1',
    disabled: '0',
    help: '控制系统提示开头的客户端版本和提示指纹。关闭后通常省略该标识；部分直连自动审核请求仍会保留。',
  },
  {
    key: 'disableNonessentialTraffic',
    label: '非必要流量',
    env: 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC',
    enabled: '1',
    disabled: null,
    help: '禁用后关闭自动更新、遥测和错误报告等。任意非空字符串都表示禁用；选择允许会移除该变量。进程环境中的同名变量仍可能生效。',
  },
  {
    key: 'experimentalAgentTeams',
    label: '实验性 Agent Teams',
    env: 'CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS',
    enabled: '1',
    disabled: '0',
    help: '启用实验性的多 Agent 协作；实际可用性取决于目标机器的 Claude Code 版本。',
  },
  {
    key: 'enableToolSearch',
    label: 'Tool Search',
    env: 'ENABLE_TOOL_SEARCH',
    enabled: 'true',
    disabled: 'false',
    help: '按需加载 MCP 工具，需要模型和网关支持。禁用时提前加载全部工具；未选择时保留文件已有的 auto 或 auto:N 自动阈值。',
  },
  {
    key: 'sandbox',
    label: '隔离环境标记',
    env: 'IS_SANDBOX',
    enabled: '1',
    disabled: '0',
    help: '声明当前进程处于隔离环境，本身不建立隔离。Linux/macOS 的 root 用户启用 bypassPermissions 时仍有环境检查；普通用户与原生 Windows 不受此项 root 检查影响。',
  },
] as const

export function textValue(value: unknown) {
  return typeof value === 'string' ? value : ''
}
export function positiveInt(value: unknown): number | null {
  const n = Number(value)
  return Number.isInteger(n) && n > 0 ? n : null
}
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}
function triState(value: unknown) {
  return value === true ? 'enabled' : value === false ? 'disabled' : 'unset'
}
export function providerKey(model: string) {
  const index = model.indexOf('/')
  return index > 0 ? model.slice(0, index) : 'main'
}

export function proxyUrl(ip: string, agent: AgentId, bootstrap: AgentBootstrap) {
  const host = ip.includes(':') ? `[${ip.replace(/^\[|\]$/g, '')}]` : ip
  return `http://${host}:${bootstrap.serverPort}${agent === 'claude' ? '' : '/v1'}`
}
export function normalizedApiUrl(value: string) {
  try {
    const parsed = new URL(value.trim())
    return ['http:', 'https:'].includes(parsed.protocol) &&
      !parsed.username &&
      !parsed.password &&
      !parsed.search &&
      !parsed.hash
      ? parsed.origin + parsed.pathname.replace(/\/+$/, '')
      : ''
  } catch {
    return ''
  }
}
export function isOwnServiceUrl(url: string, bootstrap: AgentBootstrap, machines: AgentMachine[]) {
  const normalized = normalizedApiUrl(url)
  if (!normalized) return false
  const hosts = [
    '127.0.0.1',
    'localhost',
    '::1',
    ...bootstrap.interfaces.map((item) => item.ip),
    ...machines.map((machine) => machine.serviceHost || ''),
  ].filter(Boolean)
  if (bootstrap.serverHost && !['0.0.0.0', '::'].includes(bootstrap.serverHost))
    hosts.push(bootstrap.serverHost)
  const bases = [
    bootstrap.serverOrigin,
    ...hosts.map((host) => proxyUrl(host, 'claude', bootstrap)),
  ]
  return [...bases, ...bases.map((base) => `${base}/v1`)].some(
    (base) => normalizedApiUrl(base) === normalized,
  )
}
export function defaultBaseUrl(machine: AgentMachine, agent: AgentId, bootstrap: AgentBootstrap) {
  if (machine.local) return proxyUrl('127.0.0.1', agent, bootstrap)
  const origin = new URL(bootstrap.serverOrigin)
  if (!['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname))
    return bootstrap.serverOrigin + (agent === 'claude' ? '' : '/v1')
  return proxyUrl(
    machine.serviceHost || bootstrap.interfaces[0]?.ip || '127.0.0.1',
    agent,
    bootstrap,
  )
}
function defaultModel(agent: AgentId, bootstrap: AgentBootstrap) {
  const main = bootstrap.models.find((id) => id.startsWith('main/')) || bootstrap.models[0] || ''
  return agent === 'claude' ? bootstrap.models.find((id) => /claude-sonnet/.test(id)) || main : main
}

export function createDraft(
  agent: AgentId,
  snapshot: AgentSnapshot,
  bootstrap: AgentBootstrap,
  machines: AgentMachine[],
): AgentDraft {
  const files = snapshot.files
  const m = files[agent].managed
  const baseDefault = defaultBaseUrl(snapshot.machine, agent, bootstrap)
  if (agent === 'claude') {
    const models = Object.fromEntries(
      CLAUDE_MODEL_KEYS.map((key) => [key, textValue(m[`env.${key}`])]),
    )
    const effort = textValue(m['env.CLAUDE_CODE_EFFORT_LEVEL'])
    const permission = textValue(m['permissions.defaultMode'])
    const runtime: Record<string, string> = {
      effortLevel: effort === 'unset' ? 'auto' : effort,
      permissionMode: permission === 'manual' ? 'default' : permission,
      sandboxEnabled: triState(m['sandbox.enabled']),
      attribution:
        m.attribution === undefined ? 'unset' : m.attribution === false ? 'disabled' : 'custom',
      skipWebFetchPreflight: triState(m.skipWebFetchPreflight),
    }
    for (const setting of CLAUDE_MODES) {
      const value = m[`env.${setting.env}`]
      runtime[setting.key] =
        setting.disabled === null && typeof value === 'string'
          ? value
            ? 'enabled'
            : 'disabled'
          : value === setting.enabled
            ? 'enabled'
            : value === setting.disabled
              ? 'disabled'
              : 'unset'
    }
    if (!files.claude.exists) {
      Object.assign(runtime, {
        sandboxEnabled: 'unset',
        attributionHeader: 'disabled',
        disableNonessentialTraffic: 'enabled',
        experimentalAgentTeams: 'enabled',
        enableToolSearch: 'enabled',
        sandbox: 'enabled',
        skipWebFetchPreflight: 'enabled',
      })
      if (!Object.values(models).some((model) => model.trim()))
        models.ANTHROPIC_MODEL = defaultModel(agent, bootstrap)
    }
    return {
      baseUrl: textValue(m['env.ANTHROPIC_BASE_URL']) || baseDefault,
      apiKey: textValue(m['env.ANTHROPIC_AUTH_TOKEN']),
      models,
      runtime,
      limits: {
        context: positiveInt(m['env.CLAUDE_CODE_AUTO_COMPACT_WINDOW']),
        compact: positiveInt(m['env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE']),
        output: positiveInt(m['env.CLAUDE_CODE_MAX_OUTPUT_TOKENS']),
      },
    }
  }
  if (agent === 'codex') {
    const baseUrl =
      textValue(m[`model_providers.${textValue(m.model_provider)}.base_url`]) || baseDefault
    const context = positiveInt(m.model_context_window)
    const compactTokens = positiveInt(m.model_auto_compact_token_limit)
    return {
      baseUrl,
      apiKey: textValue(files.codexAuth.managed.OPENAI_API_KEY),
      model:
        textValue(m.model) ||
        (isOwnServiceUrl(baseUrl, bootstrap, machines) ? defaultModel(agent, bootstrap) : ''),
      runtime: {
        serviceTier: textValue(m.service_tier),
        modelReasoningEffort: textValue(m.model_reasoning_effort),
        sandboxMode: textValue(m.sandbox_mode),
        approvalPolicy: textValue(m.approval_policy),
        memories: triState(m['features.memories']),
      },
      limits: {
        context,
        compact: context && compactTokens ? Math.round((compactTokens / context) * 100) : null,
        output: null,
        ...(compactTokens ? { compactTokens } : {}),
      },
    }
  }
  const storedModel = textValue(m.model)
  const storedProvider = storedModel.split('/')[0] || ''
  const candidate = storedModel.slice(storedProvider.length + 1)
  let model = m[`provider.${storedProvider}.models.${candidate}`] ? candidate : storedModel
  model ||= defaultModel(agent, bootstrap)
  const provider = providerKey(model)
  const baseUrl = textValue(m[`provider.${provider}.options.baseURL`]) || baseDefault
  if (!storedModel && !isOwnServiceUrl(baseUrl, bootstrap, machines)) model = ''
  const entry = object(m[`provider.${provider}.models.${model}`])
  const limits = object(entry.limit)
  const context = positiveInt(limits.context)
  const reserved = m['compaction.reserved'] === 0 ? 0 : positiveInt(m['compaction.reserved'])
  return {
    baseUrl,
    apiKey: textValue(m[`provider.${provider}.options.apiKey`]),
    model,
    runtime: { reasoningEffort: textValue(object(entry.options).reasoningEffort) },
    limits: {
      context,
      output: positiveInt(limits.output),
      compact: context && reserved !== null ? Math.round((1 - reserved / context) * 100) : null,
      reserved,
    },
  }
}

export function markClear(draft: AgentDraft, field: string, cleared: boolean) {
  const fields = (draft.clearFields || []).filter((name) => name !== field)
  if (cleared) fields.push(field)
  if (fields.length) draft.clearFields = fields
  else delete draft.clearFields
}
export function setModel(draft: AgentDraft, value: string, key?: string) {
  if (key) {
    draft.models![key] = value
    markClear(draft, `models.${key}`, !value)
  } else {
    draft.model = value
    markClear(draft, 'model', !value)
  }
}
export function uniformClaudeModel(draft: AgentDraft) {
  const value = draft.models?.[CLAUDE_MODEL_KEYS[0]] || ''
  return CLAUDE_MODEL_KEYS.every((key) => (draft.models?.[key] || '') === value) ? value : ''
}
export function setAllClaudeModels(draft: AgentDraft, value: string) {
  CLAUDE_MODEL_KEYS.forEach((key) => setModel(draft, value, key))
}
export function setRuntime(draft: AgentDraft, key: string, value: string) {
  draft.runtime[key] = value
  markClear(draft, `runtime.${key}`, !value || value === 'unset')
}
export function setLimit(draft: AgentDraft, key: LimitKind, value: number | null) {
  if (draft.limits[key] === value) return
  draft.limits[key] = value
  markClear(draft, `limits.${key}`, value === null)
  if (key === 'context' || key === 'compact') {
    delete draft.limits.compactTokens
    delete draft.limits.reserved
  }
}
export function derivedCompact(agent: AgentId, draft: AgentDraft) {
  const { context, compact, compactTokens, reserved } = draft.limits
  if (agent === 'codex')
    return {
      label: '上限',
      value:
        compactTokens ||
        (context
          ? compact != null
            ? Math.floor((context * compact) / 100)
            : Math.min(350000, Math.floor(context * 0.95))
          : null),
    }
  return {
    label: '预留',
    value:
      reserved ??
      (context && compact != null ? context - Math.floor((context * compact) / 100) : null),
  }
}

export function lookupModelLimits(model: string, presets: ModelPreset[]) {
  const base = model
    .trim()
    .toLowerCase()
    .replace(/\[1m\]$/i, '')
    .replace(/^.*\//, '')
  if (!base) return undefined
  const variants = [...new Set([base, base.replace(/\./g, '-'), base.replace(/-/g, '.')])]
  for (const variant of variants) {
    const match = presets.find((preset) => preset.ids.includes(variant))
    if (match) return match
  }
  for (const variant of variants) {
    const match = presets.find((preset) => preset.aliases?.includes(variant))
    if (match) return match
  }
  for (const variant of variants) {
    const match = presets.find((preset) =>
      preset.ids.some(
        (id) =>
          variant.startsWith(`${id}-`) &&
          /^\d{4}(-?\d{2}){0,2}$/.test(variant.slice(id.length + 1)),
      ),
    )
    if (match) return match
  }
  return undefined
}
export function modelCandidates(model: string, bootstrap: AgentBootstrap) {
  const preset = lookupModelLimits(model, bootstrap.modelLimitPresets)
  const provider =
    bootstrap.providers.find(
      (p) => p.enabled && (p.key === providerKey(model) || p.name === providerKey(model)),
    ) ||
    bootstrap.providers.find((p) => p.key === providerKey(model) || p.name === providerKey(model))
  const normalized = model.replace(/\[1m\]$/i, '')
  const config =
    provider?.models.find((m) => m.id.replace(/\[1m\]$/i, '') === normalized) ||
    provider?.models.find(
      (m) => m.id.replace(/\[1m\]$/i, '').toLowerCase() === normalized.toLowerCase(),
    )
  const fields = {
    context: 'contextWindow',
    compact: 'compactThresholdPercent',
    output: 'maxOutputTokens',
  } as const
  const fallback = { context: 200000, compact: 60, output: 32000 }
  const result: Record<LimitKind, LimitCandidate[]> & { matched: boolean } = {
    context: [],
    compact: [],
    output: [],
    matched: !!preset,
  }
  if (!model) return result
  for (const key of ['context', 'compact', 'output'] as const) {
    const candidates = preset?.candidates?.[key]
    result[key] = candidates?.length
      ? [...candidates]
      : [
          {
            value: preset?.[key] || fallback[key],
            label: preset?.[key] ? '模型参考值' : '通用参考值',
            kind: 'fallback',
          },
        ]
    const value = positiveInt(config?.[fields[key]])
    if (value) {
      result[key].unshift({ value, label: 'Provider 设置', kind: 'configured' })
      result.matched = true
    }
    result[key] = result[key].filter(
      (item, index, list) =>
        positiveInt(item.value) && list.findIndex((x) => x.value === item.value) === index,
    )
  }
  return result
}
export function referenceModels(agent: AgentId, draft: AgentDraft) {
  if (agent !== 'claude') return draft.model?.trim() ? [draft.model.trim()] : []
  const main = draft.models?.ANTHROPIC_MODEL?.trim()
  if (main) return [main]
  return [
    ...new Set(CLAUDE_MODEL_KEYS.map((key) => draft.models?.[key]?.trim() || '').filter(Boolean)),
  ]
}
export function limitCandidates(
  agent: AgentId,
  draft: AgentDraft,
  key: LimitKind,
  bootstrap: AgentBootstrap,
) {
  return referenceModels(agent, draft).flatMap((model) =>
    modelCandidates(model, bootstrap)[key].map((candidate) => ({
      ...candidate,
      referenceModel: model,
    })),
  )
}
export function fileVersions(snapshot: AgentSnapshot) {
  return Object.fromEntries(
    Object.entries(snapshot.files).map(([key, value]) => [key, value.version]),
  )
}
