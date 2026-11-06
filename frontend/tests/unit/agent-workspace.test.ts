import { defineComponent } from 'vue'
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
  it('保存只使用后端票据，其他 Agent 草稿保留', async () => {
    const { workspace, wrapper } = await harness()
    workspace.state.value!.drafts.codex.model = 'keep/unsaved'
    workspace.draft.value!.apiKey = 'new-key'
    await workspace.save()
    expect(agentsApi.apply).toHaveBeenCalledWith('local', 'server-ticket', expect.any(AbortSignal))
    expect(workspace.state.value!.drafts.codex.model).toBe('keep/unsaved')
    wrapper.unmount()
  })

  it('保存冲突保留当前草稿并显示错误', async () => {
    const { workspace, wrapper } = await harness()
    workspace.draft.value!.apiKey = 'unsaved-key'
    vi.mocked(agentsApi.apply).mockRejectedValueOnce(
      new ApiError('文件已修改', 409, { code: 'file_conflict' }),
    )
    await workspace.save()
    expect(workspace.draft.value!.apiKey).toBe('unsaved-key')
    expect(workspace.error.value).toBe('文件已修改')
    expect(workspace.saving.value).toBe(false)
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
