import { describe, expect, it } from 'vitest'
import type { AgentBootstrap, AgentSnapshot, FileTarget } from '@/api/agents'
import {
  createDraft,
  derivedCompact,
  isOwnServiceUrl,
  limitCandidates,
  lookupModelLimits,
  modelCandidates,
  setAllClaudeModels,
  setLimit,
  setModel,
  setRuntime,
  uniformClaudeModel,
} from '@/features/agents/configuration'

const bootstrap: AgentBootstrap = {
  machines: [],
  providers: [],
  models: ['main/claude-sonnet-4-6', 'main/org/model.v1'],
  modelLimitPresets: [
    {
      ids: ['claude-sonnet-4-6'],
      label: 'Sonnet',
      aliases: ['sonnet-4.6'],
      context: 200000,
      output: 64000,
    },
    { ids: ['gpt-5'], label: 'GPT-5', context: 400000, output: 128000 },
  ],
  interfaces: [{ ip: '192.168.1.10', prefix: '192.168.1.' }],
  serverHost: '0.0.0.0',
  serverPort: 9090,
  serverOrigin: 'https://proxy.example',
  apiKeys: [],
  apiKeyEnabled: false,
}
function snapshot(): AgentSnapshot {
  return {
    machine: {
      id: 'local',
      name: '本机',
      local: true,
      host: '',
      port: '',
      user: '',
      connected: true,
      proxy: { mode: 'direct', url: '' },
    },
    files: Object.fromEntries(
      (['claude', 'codex', 'codexAuth', 'opencode'] as FileTarget[]).map((target) => [
        target,
        {
          path: `/srv/${target}`,
          defaultPath: `/srv/${target}`,
          customPath: '',
          exists: true,
          managed: {},
          version: `version-${target}`,
          parseError: '',
        },
      ]),
    ) as AgentSnapshot['files'],
  }
}
function draft(agent: 'claude' | 'codex' | 'opencode', source = snapshot()) {
  return createDraft(agent, source, bootstrap, [source.machine])
}

describe('Agent 配置语义', () => {
  it('外部地址没有配置模型时不默认选本服务模型，异常字段保持可编辑', () => {
    const source = snapshot()
    source.files.claude.managed = {
      'env.ANTHROPIC_BASE_URL': 'https://external.test',
      'env.ANTHROPIC_MODEL': { malformed: true },
    }
    source.files.codex.managed = {
      model_provider: 'external',
      'model_providers.external.base_url': 'https://external.test/v1',
      model: ['malformed'],
    }
    expect(draft('claude', source).models?.ANTHROPIC_MODEL).toBe('')
    expect(draft('codex', source).model).toBe('')
  })
  it('Provider 候选优先展示，所有模型表候选保留且不覆盖编辑或清空的参数', () => {
    const context = {
      ...bootstrap,
      providers: [
        {
          key: 'main',
          name: 'main',
          enabled: true,
          models: [{ id: 'main/gpt-5', contextWindow: 300000 }],
        },
      ],
      modelLimitPresets: [
        {
          ids: ['gpt-5'],
          label: 'GPT-5',
          candidates: {
            context: [
              { value: 400000, label: '标准', kind: 'documented' },
              { value: 1000000, label: '扩展', kind: 'documented' },
            ],
          },
        },
      ],
    }
    expect(modelCandidates('main/gpt-5', context).context.map((item) => item.value)).toEqual([
      300000, 400000, 1000000,
    ])
    const value = draft('codex')
    setLimit(value, 'context', 150000)
    setLimit(value, 'output', 50000)
    setLimit(value, 'output', null)
    setModel(value, 'main/gpt-5')
    expect(value.limits.context).toBe(150000)
    expect(value.limits.output).toBeNull()
    expect(value.clearFields).toContain('limits.output')
  })
  it('已有文件的缺项保持不设置，新建 Claude 文件单独使用默认值', () => {
    const source = snapshot()
    expect(draft('claude', source).runtime.attribution).toBe('unset')
    expect(draft('claude', source).runtime.attributionHeader).toBe('unset')
    expect(draft('claude', source).runtime.sandboxEnabled).toBe('unset')
    source.files.claude.exists = false
    expect(draft('claude', source).runtime).toMatchObject({
      sandboxEnabled: 'unset',
      attribution: 'unset',
      attributionHeader: 'disabled',
      disableNonessentialTraffic: 'enabled',
      experimentalAgentTeams: 'enabled',
      enableToolSearch: 'enabled',
      sandbox: 'enabled',
      skipWebFetchPreflight: 'enabled',
    })
  })

  it('Attribution 区分禁用与文件自定义，清空只移除署名设置', () => {
    const source = snapshot()
    source.files.claude.managed = {
      attribution: false,
      'env.CLAUDE_CODE_ATTRIBUTION_HEADER': '1',
    }
    const value = draft('claude', source)
    expect(value.runtime.attribution).toBe('disabled')
    setRuntime(value, 'attribution', 'unset')
    expect(value.clearFields).toEqual(['runtime.attribution'])
    expect(value.runtime.attributionHeader).toBe('enabled')
    setRuntime(value, 'attribution', 'disabled')
    expect(value.clearFields).toBeUndefined()
    for (const attribution of [
      {},
      { commit: 'Co-Authored-By: Example <example@example.com>' },
      { commit: '', pr: '', sessionUrl: false },
    ]) {
      source.files.claude.managed.attribution = attribution
      const custom = draft('claude', source)
      expect(custom.runtime.attribution).toBe('custom')
      setRuntime(custom, 'sandboxEnabled', 'enabled')
      expect(custom.runtime.attribution).toBe('custom')
      expect(custom.clearFields).toBeUndefined()
    }
  })

  it('命令沙箱、记忆布尔值和流量字符串按实际文件解释', () => {
    const source = snapshot()
    source.files.claude.managed = {
      'sandbox.enabled': false,
      'env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': 'false',
      'env.CLAUDE_CODE_EFFORT_LEVEL': 'unset',
    }
    source.files.codex.managed['features.memories'] = false
    expect(draft('claude', source).runtime).toMatchObject({
      sandboxEnabled: 'disabled',
      disableNonessentialTraffic: 'enabled',
      effortLevel: 'auto',
    })
    expect(draft('codex', source).runtime.memories).toBe('disabled')
  })

  it('一键模型立即同步，单个模型不同时显示空值，清空有显式移除标记', () => {
    const value = draft('claude')
    setAllClaudeModels(value, 'external/new-model')
    expect(new Set(Object.values(value.models!))).toEqual(new Set(['external/new-model']))
    expect(uniformClaudeModel(value)).toBe('external/new-model')
    setModel(value, 'other', 'ANTHROPIC_MODEL')
    expect(uniformClaudeModel(value)).toBe('')
    setAllClaudeModels(value, '')
    expect(value.clearFields).toHaveLength(4)
    setRuntime(value, 'sandboxEnabled', 'unset')
    expect(value.clearFields).toContain('runtime.sandboxEnabled')
  })

  it('显示相同百分比时保留原始精确压缩 Token，真正修改才重新计算', () => {
    const source = snapshot()
    source.files.codex.managed = {
      model_context_window: 372000,
      model_auto_compact_token_limit: 350000,
    }
    const value = draft('codex', source)
    expect(value.limits.compact).toBe(94)
    setLimit(value, 'compact', 94)
    expect(derivedCompact('codex', value).value).toBe(350000)
    setLimit(value, 'compact', 90)
    expect(value.limits.compactTokens).toBeUndefined()
    expect(derivedCompact('codex', value).value).toBe(334800)
  })

  it('opencode 完整路由模型和零预留值往返保留', () => {
    const source = snapshot()
    source.files.opencode.managed = {
      model: 'main/main/org/model.v1',
      'provider.main.models.main/org/model.v1': {
        limit: { context: 200000, output: 32000 },
        options: { reasoningEffort: 'high' },
      },
      'compaction.reserved': 0,
    }
    const value = draft('opencode', source)
    expect(value.model).toBe('main/org/model.v1')
    expect(value.limits.reserved).toBe(0)
    expect(derivedCompact('opencode', value).value).toBe(0)
    expect(value.runtime.reasoningEffort).toBe('high')
  })

  it('参考候选包含多个模型名，选择模型不会填入参数', () => {
    const value = draft('claude')
    setModel(value, 'main/claude-sonnet-4-6', 'ANTHROPIC_DEFAULT_SONNET_MODEL')
    setModel(value, 'main/gpt-5', 'ANTHROPIC_DEFAULT_OPUS_MODEL')
    const candidates = limitCandidates('claude', value, 'context', bootstrap)
    expect(new Set(candidates.map((c) => c.referenceModel))).toEqual(
      new Set(['main/claude-sonnet-4-6', 'main/gpt-5']),
    )
    expect(value.limits.context).toBeNull()
    setModel(value, 'main/gpt-5', 'ANTHROPIC_MODEL')
    expect(limitCandidates('claude', value, 'context', bootstrap)).toHaveLength(1)
  })

  it('模型表支持别名、日期后缀，未知模型只提供通用候选', () => {
    expect(
      lookupModelLimits('main/claude-sonnet-4.6-20261001[1m]', bootstrap.modelLimitPresets)
        ?.context,
    ).toBe(200000)
    expect(lookupModelLimits('main/sonnet-4.6', bootstrap.modelLimitPresets)?.context).toBe(200000)
    expect(lookupModelLimits('unknown', bootstrap.modelLimitPresets)).toBeUndefined()
    const value = draft('codex')
    value.model = 'unknown'
    expect(limitCandidates('codex', value, 'output', bootstrap)[0]).toMatchObject({
      value: 32000,
      kind: 'fallback',
      referenceModel: 'unknown',
    })
  })

  it('本服务判断包含协议、端口和路径，支持局域网与 IPv6', () => {
    for (const url of [
      'https://proxy.example/v1',
      'http://192.168.1.10:9090',
      'http://[::1]:9090/v1',
    ])
      expect(isOwnServiceUrl(url, bootstrap, [])).toBe(true)
    for (const url of [
      'https://proxy.example/v2',
      'https://external.example/v1',
      'http://localhost:8081',
      'https://proxy.example/v1?key=x',
    ])
      expect(isOwnServiceUrl(url, bootstrap, [])).toBe(false)
  })
})
