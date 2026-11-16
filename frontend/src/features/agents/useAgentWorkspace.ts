import { computed, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
import {
  agentsApi,
  type AgentBootstrap,
  type AgentDraft,
  type AgentId,
  type AgentMachine,
  type AgentPreview,
  type AgentSnapshot,
  type MachineInput,
} from '@/api/agents'
import { ApiError, errorMessage, isAbort } from '@/api/client'
import { createDraft, fileVersions } from './configuration'
import { notify } from '@/components/ui'
import { AGENT_DEFINITIONS, agentDefinition, installAgentDefinitions } from './registry'

interface MachineState {
  snapshot: AgentSnapshot
  drafts: Record<AgentId, AgentDraft>
  baselines: Record<AgentId, string>
}
interface PreviewState extends Partial<AgentPreview> {
  key: string
  pending: boolean
  error: string
  saved?: boolean
}
export function useAgentWorkspace() {
  const bootstrap = shallowRef<AgentBootstrap>()
  const machines = ref<AgentMachine[]>([])
  const selectedId = ref('local')
  const activeAgent = ref<AgentId>('claude')
  const states = reactive<Record<string, MachineState>>({})
  const previews = reactive<Record<string, PreviewState>>({})
  const loading = ref(false)
  const error = ref('')
  const saving = ref(false)
  const editorOpen = ref(false)
  const fingerprint = ref<{ machineId: string; fingerprint: string; keyType: string }>()
  const machine = computed(() => machines.value.find((item) => item.id === selectedId.value))
  const state = computed(() => states[selectedId.value])
  const draft = computed(() => state.value?.drafts[activeAgent.value])
  const previewId = computed(() => `${selectedId.value}:${activeAgent.value}`)
  const preview = computed(() => previews[previewId.value])
  const dirty = computed(
    () => state.value && JSON.stringify(draft.value) !== state.value.baselines[activeAgent.value],
  )
  const ready = computed(() => machine.value?.connected && !!draft.value)
  let previewController: AbortController | undefined
  let tabReadController: AbortController | undefined
  const loadController = new AbortController()
  let disposed = false

  function install(snapshot: AgentSnapshot, reset: AgentId | 'all' = 'all') {
    const id = snapshot.machine.id
    const index = machines.value.findIndex((item) => item.id === id)
    if (index >= 0) machines.value[index] = snapshot.machine
    else machines.value.push(snapshot.machine)
    const existing = states[id]
    if (!existing || reset === 'all') {
      const drafts = Object.fromEntries(
        AGENT_DEFINITIONS.map((agent) => [
          agent.id,
          createDraft(agent.id, snapshot, bootstrap.value!, machines.value),
        ]),
      ) as Record<AgentId, AgentDraft>
      states[id] = {
        snapshot,
        drafts,
        baselines: Object.fromEntries(
          Object.entries(drafts).map(([id, draft]) => [id, JSON.stringify(draft)]),
        ),
      }
      for (const agent of AGENT_DEFINITIONS) delete previews[`${id}:${agent.id}`]
    } else {
      existing.snapshot.machine = snapshot.machine
      for (const target of agentDefinition(reset)?.files || [])
        existing.snapshot.files[target] = snapshot.files[target]!
      existing.drafts[reset] = createDraft(
        reset,
        existing.snapshot,
        bootstrap.value!,
        machines.value,
      )
      existing.baselines[reset] = JSON.stringify(existing.drafts[reset])
      delete previews[`${id}:${reset}`]
    }
  }
  function fail(reason: unknown, id = selectedId.value) {
    if (disposed || isAbort(reason)) return
    if (reason instanceof ApiError && reason.details.code === 'disconnected') {
      const item = machines.value.find((entry) => entry.id === id)
      if (item) item.connected = false
    }
    error.value = errorMessage(reason)
  }
  async function start() {
    loading.value = true
    error.value = ''
    try {
      const context = await agentsApi.bootstrap(loadController.signal)
      if (disposed) return
      bootstrap.value = context
      installAgentDefinitions(context.agents)
      machines.value = bootstrap.value.machines
      const result = await agentsApi.snapshot('local', loadController.signal)
      if (!disposed) install(result)
    } catch (reason) {
      if (!isAbort(reason)) fail(reason)
    } finally {
      loading.value = false
    }
  }
  async function select(id: string) {
    if (saving.value) return
    selectedId.value = id
    error.value = ''
    if (machine.value?.connected && !states[id]) {
      loading.value = true
      try {
        const snapshot = await agentsApi.snapshot(id, loadController.signal)
        if (!disposed) install(snapshot)
      } catch (reason) {
        if (!isAbort(reason)) fail(reason, id)
      } finally {
        loading.value = false
      }
    }
  }
  async function connect(id: string, trust?: string) {
    const item = machines.value.find((entry) => entry.id === id)
    if (!item || item.connecting || item.connected) return
    item.connecting = true
    error.value = ''
    fingerprint.value = undefined
    try {
      const result = await agentsApi.connect(id, trust, loadController.signal)
      if (!disposed) {
        install(result)
        notify(`已连接 ${item.name}`)
      }
    } catch (reason) {
      if (disposed || isAbort(reason)) return
      if (reason instanceof ApiError && reason.details.code === 'host_key_required')
        fingerprint.value = {
          machineId: id,
          fingerprint: String(reason.details.fingerprint),
          keyType: String(reason.details.key_type || ''),
        }
      else fail(reason, id)
    } finally {
      item.connecting = false
    }
  }
  async function disconnect(id: string) {
    try {
      await agentsApi.disconnect(id, loadController.signal)
      if (disposed) return
      const item = machines.value.find((entry) => entry.id === id)
      if (item) item.connected = false
      delete states[id]
      for (const agent of AGENT_DEFINITIONS) delete previews[`${id}:${agent.id}`]
    } catch (reason) {
      fail(reason, id)
    }
  }
  async function saveMachine(input: MachineInput, id?: string) {
    const result = await agentsApi.saveMachine(input, id, loadController.signal)
    if (disposed) throw new DOMException('Page closed', 'AbortError')
    const index = machines.value.findIndex((item) => item.id === result.machine.id)
    if (index >= 0) machines.value[index] = result.machine
    else machines.value.push(result.machine)
    delete states[result.machine.id]
    selectedId.value = result.machine.id
    return result.machine.id
  }
  async function removeMachine(id: string) {
    try {
      await agentsApi.removeMachine(id, loadController.signal)
      if (disposed) return
      machines.value = machines.value.filter((item) => item.id !== id)
      delete states[id]
      if (selectedId.value === id) selectedId.value = 'local'
      notify('机器已删除')
    } catch (reason) {
      fail(reason, id)
    }
  }
  async function refresh() {
    const id = selectedId.value
    const agent = activeAgent.value
    loading.value = true
    try {
      const snapshot = await agentsApi.snapshot(id, loadController.signal)
      if (!disposed) {
        install(snapshot, states[id] ? agent : 'all')
        error.value = ''
        notify('已重新读取配置')
      }
    } catch (reason) {
      if (!isAbort(reason)) fail(reason, id)
    } finally {
      loading.value = false
    }
  }
  watch([selectedId, activeAgent], async ([id, agent]) => {
    if (tabReadController) {
      tabReadController.abort()
      tabReadController = undefined
      loading.value = false
    }
    if (!states[id] || !machine.value?.connected || saving.value || editorOpen.value) return
    const controller = new AbortController()
    tabReadController = controller
    loading.value = true
    error.value = ''
    try {
      const snapshot = await agentsApi.snapshot(id, controller.signal)
      if (
        disposed ||
        controller.signal.aborted ||
        id !== selectedId.value ||
        agent !== activeAgent.value
      )
        return
      const current = states[id]
      if (!current) return
      if (JSON.stringify(current.drafts[agent]) === current.baselines[agent]) {
        install(snapshot, agent)
      } else {
        // 草稿保留旧版本指纹；文件有外部变化时，后续保存仍会触发冲突保护。
        const changed = (agentDefinition(agent)?.files || []).some(
          (target) =>
            current.snapshot.files[target]?.version !== snapshot.files[target]?.version ||
            current.snapshot.files[target]?.path !== snapshot.files[target]?.path,
        )
        if (changed) notify('配置文件已变化，已保留未保存的修改；请重新读取后编辑')
      }
    } catch (reason) {
      if (!disposed && !controller.signal.aborted && !isAbort(reason)) fail(reason, id)
    } finally {
      if (tabReadController === controller) {
        tabReadController = undefined
        loading.value = false
      }
    }
  })
  const formKey = computed(() =>
    ready.value && state.value
      ? JSON.stringify({ draft: draft.value, versions: fileVersions(state.value.snapshot) })
      : '',
  )
  watch(
    [formKey, previewId, saving, editorOpen, loading],
    async () => {
      previewController?.abort()
      if (!formKey.value || saving.value || loading.value || editorOpen.value || !state.value)
        return
      const key = formKey.value
      const id = selectedId.value
      const agent = activeAgent.value
      const cache = `${id}:${agent}`
      const previous = previews[cache]
      // 保存已确认当前文件与草稿；后续编辑或重新读取时再生成预览。
      if (previous?.saved && previous.key === key) return
      previews[cache] = { ...previous, key, pending: true, error: '', saved: false }
      const body = JSON.parse(key) as { draft: AgentDraft; versions: Record<string, string> }
      const controller = new AbortController()
      previewController = controller
      try {
        const result = await agentsApi.preview(id, agent, body, controller.signal)
        if (!disposed && !controller.signal.aborted && previews[cache]?.key === key)
          previews[cache] = { ...result, key, pending: false, error: '' }
      } catch (reason) {
        if (
          !disposed &&
          !controller.signal.aborted &&
          !isAbort(reason) &&
          previews[cache]?.key === key
        ) {
          previews[cache] = {
            ...previews[cache],
            key,
            pending: false,
            error: errorMessage(reason),
          }
          if (reason instanceof ApiError && reason.details.code === 'disconnected') fail(reason, id)
        }
      }
    },
    { immediate: true },
  )
  function settleSavedPreview(id: string, agent: AgentId, ticket: AgentPreview) {
    const current = states[id]!
    previews[`${id}:${agent}`] = {
      key: JSON.stringify({
        draft: current.drafts[agent],
        versions: fileVersions(current.snapshot),
      }),
      rows: ticket.rows.map((row) => ({ ...row, current: row.next, kind: 'same' })),
      hasChanges: false,
      pending: false,
      error: '',
      saved: true,
    }
  }
  async function save() {
    if (saving.value || loading.value || !state.value || !draft.value || !ready.value) return
    const id = selectedId.value
    const agent = activeAgent.value
    const payload = JSON.parse(formKey.value) as {
      draft: AgentDraft
      versions: Record<string, string>
    }
    saving.value = true
    error.value = ''
    try {
      const ticket = await agentsApi.preview(id, agent, payload, loadController.signal)
      if (disposed) return
      if (!ticket.hasChanges) {
        settleSavedPreview(id, agent, ticket)
        notify('配置已是最新')
        return
      }
      const result = await agentsApi.apply(id, ticket.token, loadController.signal)
      if (!disposed) {
        install(result, agent)
        settleSavedPreview(id, agent, ticket)
        notify('配置已保存，已有文件保留最近 5 份备份')
      }
    } catch (reason) {
      fail(reason, id)
    } finally {
      saving.value = false
    }
  }
  onBeforeUnmount(() => {
    disposed = true
    previewController?.abort()
    tabReadController?.abort()
    loadController.abort()
    machines.value = []
    bootstrap.value = undefined
  })
  return {
    bootstrap,
    machines,
    selectedId,
    activeAgent,
    machine,
    state,
    draft,
    ready,
    dirty,
    loading,
    error,
    saving,
    preview,
    editorOpen,
    fingerprint,
    start,
    select,
    connect,
    disconnect,
    saveMachine,
    removeMachine,
    refresh,
    save,
    install,
    fail,
  }
}
