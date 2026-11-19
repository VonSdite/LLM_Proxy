import { defineComponent, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  agentsApi,
  type AgentBootstrap,
  type AgentPreview,
  type AgentSnapshot,
  type FileTarget,
} from '@/api/agents'
import { ApiError } from '@/api/client'
import { useAgentWorkspace } from '@/features/agents/useAgentWorkspace'
vi.mock('@/api/agents', () => ({
  agentsApi: {
    bootstrap: vi.fn(),
    snapshot: vi.fn(),
    preview: vi.fn(),
    apply: vi.fn(),
    connect: vi.fn(),
  },
}))

function fixture(): AgentSnapshot {
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
const ticket: AgentPreview = {
  token: 'server-ticket',
  hasChanges: true,
  rows: [
    {
      file: 'settings.json',
      path: 'env.ANTHROPIC_MODEL',
      kind: 'change',
      current: 'before',
      next: 'after',
    },
  ],
}
beforeEach(() => {
  vi.useFakeTimers()
  const snapshot = fixture()
  const bootstrap: AgentBootstrap = {
    machines: [snapshot.machine],
    providers: [],
    models: ['main/model'],
    modelLimitPresets: [],
    interfaces: [],
    serverHost: '0.0.0.0',
    serverPort: 8080,
    serverOrigin: 'http://localhost',
    apiKeyEnabled: false,
    apiKeys: [],
  }
  vi.mocked(agentsApi.bootstrap).mockResolvedValue(bootstrap)
  vi.mocked(agentsApi.snapshot).mockResolvedValue(snapshot)
  vi.mocked(agentsApi.preview).mockResolvedValue(ticket)
  vi.mocked(agentsApi.apply).mockResolvedValue(fixture())
})
afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})
async function harness() {
  let workspace!: ReturnType<typeof useAgentWorkspace>
  const wrapper = mount(
    defineComponent({
      setup() {
        workspace = useAgentWorkspace()
        return () => null
      },
    }),
  )
  await workspace.start()
  await vi.advanceTimersByTimeAsync(250)
  return { workspace, wrapper }
}
describe('Agent 工作区', () => {
  it('修改立即触发预览，同一轮的多个字段更新合并为一次请求', async () => {
    const { workspace, wrapper } = await harness()
    vi.mocked(agentsApi.preview).mockClear()
    workspace.draft.value!.runtime.effortLevel = 'high'
    workspace.draft.value!.runtime.sandboxEnabled = 'disabled'
    await nextTick()
    expect(agentsApi.preview).toHaveBeenCalledTimes(1)
    expect(vi.mocked(agentsApi.preview).mock.calls[0]![2].draft!.runtime).toMatchObject({
      effortLevel: 'high',
      sandboxEnabled: 'disabled',
    })
    wrapper.unmount()
  })

  it('恢复相同草稿时，已取消的同值请求也不能覆盖最新结果', async () => {
    const { workspace, wrapper } = await harness()
    const requests: ((value: AgentPreview) => void)[] = []
    vi.mocked(agentsApi.preview).mockImplementation(
      () => new Promise((resolve) => requests.push(resolve)),
    )
    for (const value of ['first', 'second', 'first']) {
      workspace.draft.value!.apiKey = value
      await nextTick()
    }
    requests[2]!({ ...ticket, token: 'latest' })
    await flushPromises()
    requests[0]!({ ...ticket, token: 'stale-same-draft' })
    requests[1]!({ ...ticket, token: 'stale-other-draft' })
    await flushPromises()
    expect(workspace.preview.value!.token).toBe('latest')
    wrapper.unmount()
  })
  it('切换页签重新读取，干净表单回填新内容，未保存草稿保留原版本', async () => {
    const { workspace, wrapper } = await harness()
    const latest = fixture()
    latest.files.codex!.managed = { model: 'main/new-from-file' }
    latest.files.codex!.version = 'new-version'
    vi.mocked(agentsApi.snapshot).mockResolvedValue(latest)
    workspace.activeAgent.value = 'codex'
    await flushPromises()
    expect(agentsApi.snapshot).toHaveBeenCalledTimes(2)
    expect(workspace.draft.value!.model).toBe('main/new-from-file')
    workspace.draft.value!.model = 'main/unsaved'
    workspace.activeAgent.value = 'claude'
    await flushPromises()
    const changed = fixture()
    changed.files.codex!.version = 'external-change'
    vi.mocked(agentsApi.snapshot).mockResolvedValue(changed)
    workspace.activeAgent.value = 'codex'
    await flushPromises()
    expect(agentsApi.snapshot).toHaveBeenCalledTimes(4)
    expect(workspace.draft.value!.model).toBe('main/unsaved')
    expect(workspace.state.value!.snapshot.files.codex!.version).toBe('new-version')
    wrapper.unmount()
  })

  it('快速切换时过期的读取结果不覆盖当前页签', async () => {
    const { workspace, wrapper } = await harness()
    let finish!: (value: AgentSnapshot) => void
    vi.mocked(agentsApi.snapshot).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    workspace.activeAgent.value = 'codex'
    await flushPromises()
    workspace.activeAgent.value = 'opencode'
    await flushPromises()
    const stale = fixture()
    stale.files.codex!.managed = { model: 'stale/model' }
    finish(stale)
    await flushPromises()
    expect(workspace.activeAgent.value).toBe('opencode')
    expect(workspace.state.value!.drafts.codex!.model).not.toBe('stale/model')
    expect(workspace.loading.value).toBe(false)
    wrapper.unmount()
  })

  it('保存只使用后端票据，其他 Agent 草稿保留', async () => {
    const { workspace, wrapper } = await harness()
    workspace.state.value!.drafts.codex!.model = 'keep/unsaved'
    workspace.draft.value!.apiKey = 'new-key'
    await workspace.save()
    expect(agentsApi.apply).toHaveBeenCalledWith('local', 'server-ticket', expect.any(AbortSignal))
    expect(workspace.state.value!.drafts.codex!.model).toBe('keep/unsaved')
    wrapper.unmount()
  })

  it('保存成功保留预览行并原位更新状态，不重复检查；再次编辑仍会检查', async () => {
    const { workspace, wrapper } = await harness()
    workspace.draft.value!.apiKey = 'new-key'
    await flushPromises()
    const count = vi.mocked(agentsApi.preview).mock.calls.length
    let finish!: (value: AgentSnapshot) => void
    vi.mocked(agentsApi.apply).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const saving = workspace.save()
    await flushPromises()
    expect(workspace.preview.value!.rows).toEqual(ticket.rows)
    const saved = fixture()
    saved.files.claude!.managed['env.ANTHROPIC_AUTH_TOKEN'] = 'new-key'
    saved.files.claude!.version = 'saved-version'
    finish(saved)
    await saving
    await flushPromises()
    expect(agentsApi.preview).toHaveBeenCalledTimes(count + 1)
    expect(workspace.preview.value).toMatchObject({
      pending: false,
      hasChanges: false,
      rows: [{ ...ticket.rows[0], current: 'after', next: 'after', kind: 'same' }],
    })
    workspace.draft.value!.apiKey = 'next-key'
    await flushPromises()
    expect(agentsApi.preview).toHaveBeenCalledTimes(count + 2)
    wrapper.unmount()
  })

  it('保存校验为无修改时也不重新加载，移除字段保留原行并显示空值', async () => {
    const { workspace, wrapper } = await harness()
    const count = vi.mocked(agentsApi.preview).mock.calls.length
    vi.mocked(agentsApi.preview).mockResolvedValueOnce({
      ...ticket,
      hasChanges: false,
      rows: [{ ...ticket.rows[0]!, kind: 'same', current: null, next: null }],
    })
    await workspace.save()
    await flushPromises()
    expect(agentsApi.apply).not.toHaveBeenCalled()
    expect(agentsApi.preview).toHaveBeenCalledTimes(count + 1)
    expect(workspace.preview.value).toMatchObject({ pending: false, hasChanges: false })
    expect(workspace.preview.value!.rows).toHaveLength(1)
    expect(workspace.preview.value!.rows![0]!.current).toBeNull()
    wrapper.unmount()
  })

  it('保存冲突保留当前草稿，准备最新预览并等待覆盖确认', async () => {
    const { workspace, wrapper } = await harness()
    workspace.draft.value!.apiKey = 'unsaved-key'
    vi.mocked(agentsApi.apply).mockRejectedValueOnce(
      new ApiError('文件已修改', 409, { code: 'file_conflict' }),
    )
    await workspace.save()
    expect(workspace.draft.value!.apiKey).toBe('unsaved-key')
    expect(workspace.error.value).toBe('')
    expect(workspace.overwrite.value?.ticket).toEqual(ticket)
    expect(agentsApi.apply).toHaveBeenCalledTimes(1)
    expect(workspace.saving.value).toBe(false)
    await workspace.confirmOverwrite()
    expect(agentsApi.apply).toHaveBeenCalledTimes(2)
    expect(workspace.overwrite.value).toBeUndefined()
    wrapper.unmount()
  })

  it('更新和失败期间保留预览行，迟到结果不覆盖当前草稿', async () => {
    const { workspace, wrapper } = await harness()
    const requests: ((value: AgentPreview) => void)[] = []
    vi.mocked(agentsApi.preview).mockImplementation(
      () => new Promise((resolve) => requests.push(resolve)),
    )
    workspace.draft.value!.apiKey = 'first'
    await vi.advanceTimersByTimeAsync(250)
    expect(workspace.preview.value!.rows).toEqual(ticket.rows)
    workspace.draft.value!.apiKey = 'second'
    await vi.advanceTimersByTimeAsync(250)
    requests[1]!({ ...ticket, token: 'new-ticket' })
    await flushPromises()
    requests[0]!({ ...ticket, token: 'old-ticket' })
    await flushPromises()
    expect(workspace.preview.value!.token).toBe('new-ticket')
    vi.mocked(agentsApi.preview).mockRejectedValueOnce(new Error('validation failed'))
    workspace.draft.value!.apiKey = 'invalid'
    await vi.advanceTimersByTimeAsync(250)
    expect(workspace.preview.value!.rows).toEqual(ticket.rows)
    expect(workspace.preview.value!.error).toBe('validation failed')
    wrapper.unmount()
  })

  it('原始编辑器打开时暂停预览，卸载取消后台更新', async () => {
    const { workspace, wrapper } = await harness()
    const count = vi.mocked(agentsApi.preview).mock.calls.length
    workspace.editorOpen.value = true
    workspace.draft.value!.apiKey = 'edit'
    await vi.advanceTimersByTimeAsync(400)
    expect(agentsApi.preview).toHaveBeenCalledTimes(count)
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(400)
    expect(agentsApi.preview).toHaveBeenCalledTimes(count)
  })
})
