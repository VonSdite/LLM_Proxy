<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import {
  agentsApi,
  type AgentId,
  type AgentMachine,
  type FileSource,
  type FileTarget,
} from '@/api/agents'
import { errorMessage, isAbort } from '@/api/client'
import {
  UiAlert,
  UiButton,
  UiCombobox,
  UiConfirmPopover,
  UiDialog,
  UiEmptyState,
  UiField,
  UiIcon,
  UiIconButton,
  UiInput,
  UiSegmented,
  notify,
} from '@/components/ui'
import { useAgentWorkspace } from '@/features/agents/useAgentWorkspace'
import {
  defaultBaseUrl,
  isOwnServiceUrl,
  markClear,
  proxyUrl,
} from '@/features/agents/configuration'
import AgentModelFields from '@/features/agents/AgentModelFields.vue'
import AgentParameters from '@/features/agents/AgentParameters.vue'
import AgentRuntime from '@/features/agents/AgentRuntime.vue'
import AgentPreview from '@/features/agents/AgentPreview.vue'
import MachineDialog from '@/features/agents/MachineDialog.vue'
import PathDialog from '@/features/agents/PathDialog.vue'
import ApiKeyField from '@/features/shared/ApiKeyField.vue'
import ConfigPathRow from '@/features/shared/ConfigPathRow.vue'
import ConfigFileEditor from '@/features/shared/ConfigFileEditor.vue'
const workspace = useAgentWorkspace()
const {
  bootstrap,
  machines,
  selectedId,
  activeAgent,
  machine,
  state,
  draft,
  ready,
  loading,
  error,
  saving,
  preview,
  editorOpen,
  fingerprint,
} = workspace
const search = ref('')
const machineDialog = ref(false)
const editingMachine = ref<AgentMachine>()
const pathAgent = ref<AgentId>()
const refreshConfirm = ref(false)
const raw = shallowRef<{ source: FileSource; id: string; agent: AgentId }>()
const fileLoading = ref(false)
const lifetime = new AbortController()
onBeforeUnmount(() => lifetime.abort())
const agentForm = ref<HTMLFormElement>()
function save() {
  if (agentForm.value?.reportValidity()) void workspace.save()
}
const tabs = [
  { value: 'claude', label: 'Claude' },
  { value: 'codex', label: 'Codex' },
  { value: 'opencode', label: 'opencode' },
]
const visibleMachines = computed(() =>
  machines.value.filter((item) =>
    `${item.name} ${item.host} ${item.user}`.toLowerCase().includes(search.value.toLowerCase()),
  ),
)
const ownService = computed(
  () =>
    !!bootstrap.value &&
    !!draft.value &&
    isOwnServiceUrl(draft.value.baseUrl, bootstrap.value, machines.value),
)
const paths = computed<FileTarget[]>(() =>
  activeAgent.value === 'codex' ? ['codex', 'codexAuth'] : [activeAgent.value],
)
const addressOptions = computed(() => {
  if (!bootstrap.value || !machine.value) return []
  const context = bootstrap.value
  return [
    ...new Set([
      defaultBaseUrl(machine.value, activeAgent.value, context),
      ...['127.0.0.1', ...context.interfaces.map((item) => item.ip)].map((ip) =>
        proxyUrl(ip, activeAgent.value, context),
      ),
    ]),
  ].map((value) => ({ value, label: value }))
})
const unreachable = computed(
  () =>
    machine.value &&
    !machine.value.local &&
    ownService.value &&
    ['', '127.0.0.1', '::1', 'localhost'].includes(bootstrap.value?.serverHost || ''),
)
onMounted(workspace.start)
function editMachine(value?: AgentMachine) {
  editingMachine.value = value
  machineDialog.value = true
}
function setBaseUrl(value: string) {
  if (draft.value) draft.value.baseUrl = value
}
function setKey(value: string) {
  if (draft.value) {
    draft.value.apiKey = value
    markClear(draft.value, 'apiKey', !value)
  }
}
function refresh() {
  if (workspace.dirty.value) refreshConfirm.value = true
  else void workspace.refresh()
}
function confirmRefresh() {
  refreshConfirm.value = false
  void workspace.refresh()
}
async function savePath(agent: AgentId, path: string) {
  const result = await agentsApi.path(selectedId.value, agent, path, lifetime.signal)
  if (lifetime.signal.aborted) return
  workspace.install(result, agent)
  notify(path ? '路径已保存' : '已恢复默认路径')
}
async function openFile(target: FileTarget) {
  if (!ready.value || saving.value || fileLoading.value) return
  const id = selectedId.value
  const agent = activeAgent.value
  fileLoading.value = true
  editorOpen.value = true
  try {
    const result = await agentsApi.files(id, agent, 0, lifetime.signal)
    if (lifetime.signal.aborted) return
    const source = result.files.find((file) => file.target === target)
    if (!source) throw new Error('未找到配置文件')
    raw.value = { source, id, agent }
  } catch (reason) {
    editorOpen.value = false
    if (!lifetime.signal.aborted && !isAbort(reason)) notify(errorMessage(reason), 'danger')
  } finally {
    fileLoading.value = false
  }
}
async function loadBackup(backup: number) {
  const current = raw.value!
  const result = await agentsApi.files(current.id, current.agent, backup, lifetime.signal)
  const source = result.files.find((file) => file.target === current.source.target)
  if (!source) throw new Error('未找到配置文件')
  return source
}
async function saveFile(text: string, version: string) {
  const current = raw.value!
  const target = current.source.target
  const ticket = await agentsApi.preview(
    current.id,
    current.agent,
    {
      versions: { [target]: version },
      raw: { [target]: text },
    },
    lifetime.signal,
  )
  if (lifetime.signal.aborted) return
  if (ticket.hasChanges) {
    const snapshot = await agentsApi.apply(current.id, ticket.token, lifetime.signal)
    if (lifetime.signal.aborted) return
    workspace.install(snapshot, current.agent)
  }
  notify(ticket.hasChanges ? '配置文件已保存' : '文件内容未变化')
}
function closeFile(open: boolean) {
  editorOpen.value = open
  if (!open) raw.value = undefined
}
function trustHost() {
  const current = fingerprint.value
  if (current) void workspace.connect(current.machineId, current.fingerprint)
}
</script>
<template>
  <div class="page">
    <header class="page-heading"><h1>Agent 配置</h1></header>
    <UiAlert v-if="error">{{ error }}</UiAlert>
    <UiEmptyState v-if="!bootstrap" :loading="loading">
      <UiButton v-if="error" @click="workspace.start">重试</UiButton>
    </UiEmptyState>
    <div v-else class="agent-workspace">
      <aside class="agent-sidebar">
        <header class="section-heading">
          <h2>机器</h2>
          <UiIconButton
            icon="plus"
            label="添加 SSH 机器"
            :disabled="saving"
            @click="editMachine()"
          />
        </header>
        <UiInput v-model="search" placeholder="搜索机器" aria-label="搜索机器" />
        <div class="machine-list">
          <div
            v-for="item in visibleMachines"
            :key="item.id"
            class="machine-list-row"
            :class="{ 'is-selected': item.id === selectedId }"
          >
            <button
              class="machine-choice"
              type="button"
              :disabled="saving"
              :aria-pressed="item.id === selectedId"
              @click="workspace.select(item.id)"
            >
              <UiIcon :name="item.local ? 'monitor' : 'server'" :size="18" />
              <span>
                <strong>{{ item.name }}</strong>
                <small>
                  {{ item.local ? item.platform || '本机配置' : `${item.user}@${item.host}` }}
                </small>
              </span>
            </button>
            <UiIconButton
              v-if="!item.local"
              icon="edit"
              :label="`编辑 ${item.name}`"
              :disabled="saving"
              @click="editMachine(item)"
            />
          </div>
          <p v-if="!visibleMachines.length" class="muted machine-empty">没有匹配的机器</p>
        </div>
      </aside>
      <section class="agent-panel surface">
        <header class="agent-panel-toolbar">
          <UiSegmented
            :model-value="activeAgent"
            :items="tabs.map((tab) => ({ ...tab, disabled: saving }))"
            label="Agent 类型"
            @update:model-value="activeAgent = $event as AgentId"
          />
          <div class="actions">
            <UiButton
              v-if="machine && !machine.local && !machine.connected"
              icon="connect"
              size="sm"
              :loading="machine.connecting"
              @click="workspace.connect(machine.id)"
            >
              连接
            </UiButton>
            <UiButton
              v-if="machine && !machine.local && machine.connected"
              icon="disconnect"
              size="sm"
              :disabled="saving"
              @click="workspace.disconnect(machine.id)"
            >
              断开
            </UiButton>
            <UiButton
              icon="refresh"
              size="sm"
              :disabled="!ready || saving"
              :loading="loading"
              @click="refresh"
            >
              重新读取
            </UiButton>
            <UiConfirmPopover
              v-if="machine && !machine.local"
              :message="`删除机器 ${machine.name}？`"
              @confirm="workspace.removeMachine(machine.id)"
            >
              <UiIconButton icon="delete" label="删除机器" :disabled="saving" />
            </UiConfirmPopover>
          </div>
        </header>
        <UiEmptyState
          v-if="!ready || !draft || !state"
          :loading="loading || machine?.connecting"
          :title="machine?.local ? '配置尚未读取' : '连接后编辑配置'"
          icon="server"
        >
          <UiButton
            v-if="machine && !machine.local && !machine.connecting"
            variant="primary"
            icon="connect"
            @click="workspace.connect(machine.id)"
          >
            连接 {{ machine.name }}
          </UiButton>
        </UiEmptyState>
        <template v-else>
          <div class="agent-paths">
            <template v-for="target in paths" :key="target">
              <ConfigPathRow
                :path="state.snapshot.files[target].path"
                :exists="state.snapshot.files[target].exists"
                :disabled="saving || fileLoading"
                @open="openFile(target)"
                @settings="pathAgent = target === 'codexAuth' ? 'codex' : target"
              />
              <UiAlert v-if="state.snapshot.files[target].parseError">
                {{ state.snapshot.files[target].parseError }}
                <UiButton variant="ghost" size="sm" @click="openFile(target)">编辑文件</UiButton>
              </UiAlert>
            </template>
          </div>
          <form
            :key="`${selectedId}:${activeAgent}`"
            ref="agentForm"
            class="agent-form stack"
            :inert="saving || undefined"
            @submit.prevent="save"
          >
            <div class="form-grid">
              <UiField
                label="API 地址"
                help="本服务地址可选择模型目录；外部地址手动输入对应模型 ID。Claude 通常不带 /v1，Codex 和 opencode 使用 /v1。"
              >
                <template #label>
                  <UiIcon
                    v-if="unreachable"
                    name="alert"
                    class="danger"
                    :size="14"
                    aria-label="本服务仅监听回环地址，远程机器可能无法访问"
                  />
                </template>
                <UiCombobox
                  :model-value="draft.baseUrl"
                  :options="addressOptions"
                  allow-custom
                  placeholder="输入 API 地址"
                  @update:model-value="setBaseUrl"
                />
              </UiField>
              <UiField
                label="API Key"
                :help="
                  ownService && bootstrap.apiKeyEnabled
                    ? '选择已有 Key 或手动输入。保存预览中的凭据始终打码。'
                    : '填写目标服务的访问凭据；不校验 Key 的服务可留空。'
                "
              >
                <ApiKeyField
                  :model-value="draft.apiKey"
                  :candidates="bootstrap.apiKeys"
                  :offer-candidates="ownService && bootstrap.apiKeyEnabled"
                  @update:model-value="setKey"
                />
              </UiField>
            </div>
            <AgentModelFields
              :agent="activeAgent"
              :draft="draft"
              :own-service="ownService"
              :bootstrap="bootstrap"
            />
            <AgentParameters :agent="activeAgent" :draft="draft" :bootstrap="bootstrap" />
            <AgentRuntime :agent="activeAgent" :draft="draft" />
          </form>
          <AgentPreview :preview="preview" :saving="saving" :disabled="!ready" @save="save" />
        </template>
      </section>
    </div>
    <MachineDialog
      v-if="machineDialog"
      v-model:open="machineDialog"
      :machine="editingMachine"
      :save="workspace.saveMachine"
      @connect="workspace.connect"
    />
    <PathDialog
      v-if="pathAgent && state"
      :open="true"
      :agent="pathAgent"
      :file="state.snapshot.files[pathAgent]"
      :save="savePath"
      @update:open="!$event && (pathAgent = undefined)"
    />
    <ConfigFileEditor
      v-if="raw"
      :key="`${raw.id}:${raw.source.target}`"
      :open="editorOpen"
      :source="raw.source"
      :language="
        raw.source.target === 'codex'
          ? 'toml'
          : /\.jsonc$/i.test(raw.source.path)
            ? 'jsonc'
            : 'json'
      "
      :save="saveFile"
      :load-backup="loadBackup"
      @update:open="closeFile"
    />
    <UiDialog
      :open="!!fingerprint"
      title="确认 SSH 主机"
      size="sm"
      @update:open="!$event && (fingerprint = undefined)"
    >
      <div class="stack">
        <code class="fingerprint">{{ fingerprint?.fingerprint }}</code>
        <small class="muted">{{ fingerprint?.keyType }}</small>
      </div>
      <template #footer>
        <UiButton @click="fingerprint = undefined">取消</UiButton>
        <UiButton variant="primary" @click="trustHost">信任并连接</UiButton>
      </template>
    </UiDialog>
    <UiDialog
      v-model:open="refreshConfirm"
      title="重新读取配置？"
      description="当前页签有未保存的修改，重新读取后恢复文件中的值。"
      size="sm"
    >
      <template #footer>
        <UiButton @click="refreshConfirm = false">取消</UiButton>
        <UiButton variant="primary" @click="confirmRefresh">重新读取</UiButton>
      </template>
    </UiDialog>
  </div>
</template>
<style scoped>
.agent-workspace {
  display: grid;
  grid-template-columns: 224px minmax(0, 1fr);
  gap: var(--space-6);
  align-items: start;
}

.agent-sidebar {
  position: sticky;
  top: 88px;
  display: grid;
  gap: var(--space-3);
}

.agent-sidebar .section-heading {
  margin: 0;
}

.machine-list {
  display: grid;
  gap: var(--space-1);
  max-height: calc(100dvh - 240px);
  overflow: auto;
}

.machine-list-row {
  display: flex;
  align-items: center;
  border-radius: var(--radius-control);
  min-width: 0;
}

.machine-list-row:hover {
  background: var(--hover);
}

.machine-list-row.is-selected {
  background: var(--selected);
}

.machine-choice {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  flex: 1;
  border: 0;
  padding: var(--space-3);
  background: transparent;
  border-radius: inherit;
  text-align: left;
}

.machine-choice > svg {
  color: var(--muted);
}

.machine-choice[aria-pressed='true'] > svg {
  color: var(--link);
}

.machine-choice span {
  display: grid;
  min-width: 0;
}

.machine-choice strong {
  font-size: var(--text-sm);
  font-weight: 550;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.machine-choice small {
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.machine-empty {
  padding: var(--space-3);
  font-size: var(--text-xs);
}

.agent-panel {
  display: grid;
  gap: var(--space-6);
  padding: var(--space-6);
  min-width: 0;
}

.agent-panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.agent-paths {
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  min-width: 0;
  overflow: hidden;
}

.agent-paths > * + * {
  border-top: 1px solid var(--border);
}

.fingerprint {
  overflow-wrap: anywhere;
}

@media (width <= 1024px) {
  .agent-workspace {
    grid-template-columns: 180px minmax(0, 1fr);
    gap: var(--space-4);
  }

  .agent-panel {
    padding: var(--space-4);
  }
}

@media (width <= 760px) {
  .agent-workspace {
    grid-template-columns: minmax(0, 1fr);
  }

  .agent-sidebar {
    position: static;
  }

  .machine-list {
    max-height: 160px;
  }

  .agent-panel {
    gap: var(--space-4);
  }
}
</style>
