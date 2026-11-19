import { describe, expect, it } from 'vitest'
import type { AgentBootstrap, AgentSnapshot, FileTarget } from '@/api/agents'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse } from 'yaml'
import {
  addOwnServiceToNoProxy,
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
      (['claude', 'codex', 'codexAuth', 'codexEnv', 'opencode'] as FileTarget[]).map((target) => [
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
  it('OpenCode 权限识别全局策略，保留自定义规则并支持显式清空', () => {
    const source = snapshot()
    source.files.opencode!.managed.permissions = [{ action: '*', resource: '*', effect: 'deny' }]
    expect(draft('opencode', source).runtime.permissionPolicy).toBe('')
    for (const effect of ['allow', 'ask', 'deny']) {
      source.files.opencode!.managed.permission = effect
      expect(draft('opencode', source).runtime.permissionPolicy).toBe(effect)
    }
    for (const rules of [[], { bash: { 'git push *': 'ask' } }, { '*': 'allow', edit: 'deny' }]) {
      source.files.opencode!.managed.permission = rules
      expect(draft('opencode', source).runtime.permissionPolicy).toBe('custom')
    }
    const form = draft('opencode', source)
    setRuntime(form, 'permissionPolicy', '')
    expect(form.clearFields).toContain('runtime.permissionPolicy')
    setRuntime(form, 'permissionPolicy', 'allow')
    expect(form.clearFields).toBeUndefined()
  })
  it('外部地址没有配置模型时不默认选本服务模型，异常字段保持可编辑', () => {
    const source = snapshot()
    source.files.claude!.managed = {
      'env.ANTHROPIC_BASE_URL': 'https://external.test',
      'env.ANTHROPIC_MODEL': { malformed: true },
    }
    source.files.codex!.managed = {
      model_provider: 'external',
      'model_providers.external.base_url': 'https://external.test/v1',
      model: ['malformed'],
    }
    expect(draft('claude', source).models?.ANTHROPIC_MODEL).toBe('')
    expect(draft('codex', source).model).toBe('')
  })
  it('默认模型优先使用无 Provider 前缀的候选', () => {
    const source = snapshot()
    source.files.codex!.exists = false
    const context: AgentBootstrap = {
      ...bootstrap,
      models: ['main/claude-sonnet-4-6', 'glm-5.3', 'other/claude-sonnet-4-6', 'gpt-5.6-luna'],
    }
    expect(createDraft('codex', source, context, [source.machine]).model).toBe('glm-5.3')
    source.files.claude!.exists = false
    expect(createDraft('claude', source, context, [source.machine]).models?.ANTHROPIC_MODEL).toBe(
      'glm-5.3',
    )
  })
  it('已有 Codex 文件未配置模型时保持不设置，显式清空可保存并重新选择', () => {
    const source = snapshot()
    expect(draft('codex', source).model).toBe('')
    source.files.codex!.managed.model = 'main/gpt-5'
    const form = draft('codex', source)
    setModel(form, '')
    expect(form.model).toBe('')
    expect(form.clearFields).toContain('model')
    delete source.files.codex!.managed.model
    expect(draft('codex', source).model).toBe('')
    setModel(form, 'main/gpt-5')
    expect(form.clearFields).toBeUndefined()
  })
  it('已有 Opencode v1 文件缺少默认模型时保留不设置', () => {
    const source = snapshot()
    source.files.opencode!.managed = {
      'provider.proxy.options.baseURL': 'https://proxy.example/v1',
      'provider.proxy.models.main/org/model.v1': { limit: { context: 200000 } },
    }
    expect(draft('opencode', source).model).toBe('')
    expect(draft('opencode', source).limits.context).toBeNull()
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
    expect(draft('claude', source).runtime.autoMemoryEnabled).toBe('unset')
    source.files.claude!.exists = false
    expect(draft('claude', source).runtime).toMatchObject({
      sandboxEnabled: 'unset',
      autoMemoryEnabled: 'disabled',
      attribution: 'disabled',
      attributionHeader: 'disabled',
      disableNonessentialTraffic: 'enabled',
      experimentalAgentTeams: 'enabled',
      enableToolSearch: 'enabled',
      sandbox: 'enabled',
      skipWebFetchPreflight: 'enabled',
    })
  })

  it('新建 Opencode 和 Hermes 使用默认值，已有文件缺项或显式值保持原样', () => {
    const source = snapshot()
    expect(draft('opencode', source).runtime).toMatchObject({ share: '', autoupdate: '' })
    source.files.opencode!.exists = false
    expect(draft('opencode', source).runtime).toMatchObject({
      share: 'disabled',
      autoupdate: 'false',
    })
    source.files.opencode!.exists = true
    source.files.opencode!.managed = { share: 'auto', autoupdate: true }
    expect(draft('opencode', source).runtime).toMatchObject({ share: 'auto', autoupdate: 'true' })
    const context: AgentBootstrap = {
      ...bootstrap,
      agents: ['opencode_v2', 'hermes'].map((id) => {
        const definition = parse(readFileSync(resolve('../agents', id, 'agent.yaml'), 'utf8'))
        return {
          id,
          label: definition.name,
          files: Object.keys(definition.files),
          fields: definition.fields,
        }
      }),
    }
    for (const [agent, target, defaults, existing] of [
      [
        'opencode_v2',
        'opencodeV2',
        { share: 'disabled', update: 'disable' },
        { share: 'auto', update: 'auto' },
      ],
      [
        'hermes',
        'hermes',
        { memory: true, 'user-profile': true },
        { memory: false, 'user-profile': false },
      ],
    ] as const) {
      source.files[target] = { ...source.files.claude!, exists: false, managed: {} }
      expect(createDraft(agent, source, context, []).values).toMatchObject(defaults)
      source.files[target]!.exists = true
      for (const key of Object.keys(defaults))
        expect(createDraft(agent, source, context, []).values).not.toHaveProperty(key)
      source.files[target]!.managed = { ...existing }
      expect(createDraft(agent, source, context, []).values).toMatchObject(existing)
    }
  })

  it('Claude 自动记忆读取布尔值，清空时明确移除当前文件设置', () => {
    const source = snapshot()
    for (const [stored, expected] of [
      [true, 'enabled'],
      [false, 'disabled'],
    ] as const) {
      source.files.claude!.managed.autoMemoryEnabled = stored
      const value = draft('claude', source)
      expect(value.runtime.autoMemoryEnabled).toBe(expected)
      setRuntime(value, 'autoMemoryEnabled', 'unset')
      expect(value.clearFields).toContain('runtime.autoMemoryEnabled')
    }
  })

  it('Attribution 区分禁用与文件自定义，清空只移除署名设置', () => {
    const source = snapshot()
    source.files.claude!.managed = {
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
      source.files.claude!.managed.attribution = attribution
      const custom = draft('claude', source)
      expect(custom.runtime.attribution).toBe('custom')
      setRuntime(custom, 'sandboxEnabled', 'enabled')
      expect(custom.runtime.attribution).toBe('custom')
      expect(custom.clearFields).toBeUndefined()
    }
  })

  it('命令沙箱、记忆布尔值和流量字符串按实际文件解释', () => {
    const source = snapshot()
    source.files.claude!.managed = {
      'sandbox.enabled': false,
      'env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': 'false',
      'env.CLAUDE_CODE_EFFORT_LEVEL': 'unset',
    }
    source.files.codex!.managed['features.memories'] = false
    expect(draft('claude', source).runtime).toMatchObject({
      sandboxEnabled: 'disabled',
      disableNonessentialTraffic: 'enabled',
      effortLevel: 'auto',
    })
    expect(draft('codex', source).runtime.memories).toBe('disabled')
  })

  it('代理绕过与 TLS 字段按文件内容读取，清空有显式移除标记', () => {
    const source = snapshot()
    source.files.claude!.managed['env.NO_PROXY'] = '127.0.0.1,localhost'
    source.files.claude!.managed['env.NODE_TLS_REJECT_UNAUTHORIZED'] = '0'
    source.files.codexEnv!.managed.NO_PROXY = '192.168.1.10'
    const claudeDraft = draft('claude', source)
    expect(claudeDraft.runtime.noProxy).toBe('127.0.0.1,localhost')
    expect(claudeDraft.runtime.nodeTlsRejectUnauthorized).toBe('0')
    const codexDraft = draft('codex', source)
    expect(codexDraft.runtime.noProxy).toBe('192.168.1.10')
    setRuntime(codexDraft, 'noProxy', '')
    expect(codexDraft.clearFields).toContain('runtime.noProxy')
    setRuntime(claudeDraft, 'nodeTlsRejectUnauthorized', '1')
    expect(claudeDraft.clearFields).toBeUndefined()
    expect(draft('claude').runtime.noProxy).toBe('')
    expect(draft('claude').runtime.nodeTlsRejectUnauthorized).toBe('')
    expect(draft('codex').runtime.noProxy).toBe('')
  })

  it('填入本服务地址保留已有列表并去重追加，无效地址不修改草稿', () => {
    const value = draft('claude')
    value.baseUrl = 'http://192.168.1.10:9090'
    value.runtime.noProxy = 'localhost, 127.0.0.1'
    expect(addOwnServiceToNoProxy(value)).toBe(true)
    expect(value.runtime.noProxy).toBe('localhost,127.0.0.1,192.168.1.10')
    expect(value.clearFields).toBeUndefined()
    expect(addOwnServiceToNoProxy(value)).toBe(true)
    expect(value.runtime.noProxy).toBe('localhost,127.0.0.1,192.168.1.10')
    value.baseUrl = 'not a url'
    expect(addOwnServiceToNoProxy(value)).toBe(false)
    expect(value.runtime.noProxy).toBe('localhost,127.0.0.1,192.168.1.10')
    value.baseUrl = 'http://[::1]:9090'
    value.runtime.noProxy = ''
    expect(addOwnServiceToNoProxy(value)).toBe(true)
    expect(value.runtime.noProxy).toBe('[::1]')
  })

  it('一键模型立即同步，单个模型不同时显示空值，清空有显式移除标记', () => {
    const value = draft('claude')
    setAllClaudeModels(value, 'external/new-model')
    expect(value.models?.ANTHROPIC_DEFAULT_FABLE_MODEL).toBe('external/new-model')
    expect(new Set(Object.values(value.models!))).toEqual(new Set(['external/new-model']))
    expect(uniformClaudeModel(value)).toBe('external/new-model')
    setModel(value, 'other', 'ANTHROPIC_MODEL')
    expect(uniformClaudeModel(value)).toBe('')
    setAllClaudeModels(value, '')
    expect(value.clearFields).toHaveLength(5)
    expect(value.clearFields).toContain('models.ANTHROPIC_DEFAULT_FABLE_MODEL')
    setRuntime(value, 'sandboxEnabled', 'unset')
    expect(value.clearFields).toContain('runtime.sandboxEnabled')
  })

  it('Fable 已有模型回显，并作为别名模型提供参数参考', () => {
    const source = snapshot()
    source.files.claude!.managed = { 'env.ANTHROPIC_DEFAULT_FABLE_MODEL': 'main/claude-sonnet-4-6' }
    const value = draft('claude', source)
    expect(value.models?.ANTHROPIC_DEFAULT_FABLE_MODEL).toBe('main/claude-sonnet-4-6')
    expect(
      limitCandidates('claude', value, 'context', bootstrap).map((item) => item.referenceModel),
    ).toContain('main/claude-sonnet-4-6')
  })

  it('显示相同百分比时保留原始精确压缩 Token，真正修改才重新计算', () => {
    const source = snapshot()
    source.files.codex!.managed = {
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

  it('opencode 固定 Provider 保留完整路由模型和零预留值', () => {
    const source = snapshot()
    const provider = 'proxy'
    source.files.opencode!.managed = {
      model: `${provider}/main/org/model.v1`,
      [`provider.${provider}.options.baseURL`]: 'http://127.0.0.1:8080/v1',
      [`provider.${provider}.models.main/org/model.v1`]: {
        limit: { context: 200000, output: 32000 },
        options: { reasoningEffort: 'high' },
      },
      'compaction.reserved': 0,
    }
    const value = draft('opencode', source)
    expect(value.model).toBe('main/org/model.v1')
    expect(value.baseUrl).toBe('http://127.0.0.1:8080/v1')
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
