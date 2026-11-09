<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { authGroupsApi, providersApi, type AuthGroup, type Provider } from '@/api/providers'
import { useRequest } from '@/features/shared/useRequest'
import { useAction } from '@/features/shared/useAction'
import { useToggle } from '@/features/shared/useToggle'
import { moveItem, type RowMove } from '@/features/shared/order'
import { copyText } from '@/features/shared/format'
import JsonImportDialog from '@/features/shared/JsonImportDialog.vue'
import ProviderDialog from '@/features/providers/ProviderDialog.vue'
import ProviderTable from '@/features/providers/ProviderTable.vue'
import AuthGroupDialog from '@/features/providers/AuthGroupDialog.vue'
import AuthRuntimeDialog from '@/features/providers/AuthRuntimeDialog.vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmPopover,
  UiDialog,
  UiIconButton,
  UiInput,
  UiListToolbar,
  UiPageHeader,
  UiSearchInput,
  UiSegmented,
  UiSelectionBar,
  UiSwitch,
  UiTable,
  UiTabs,
  UiTooltip,
  notify,
  type TableColumn,
} from '@/components/ui'
const request = useRequest(async (signal) => {
  const [providers, groups] = await Promise.all([
    providersApi.list(signal),
    authGroupsApi.list(signal),
  ])
  return { providers, groups }
})
const action = useAction(request.refresh)
const exporting = useAction()
const toggles = useToggle()
const clipboard = useAction()
onMounted(request.refresh)
const tab = ref('providers')
const filter = ref('enabled')
const keyword = ref('')
const selected = ref<string[]>([])
const selectedGroups = ref<string[]>([])
const providerOpen = ref(false)
const editing = ref<Provider>()
const groupOpen = ref(false)
const groupEditing = ref<AuthGroup>()
const importing = ref(false)
const importingGroups = ref(false)
const runtime = ref('')
const modelDetail = ref<Provider>()
const modelSearch = ref('')
const providers = computed(() => request.data.value?.providers || [])
const selectedProviders = computed(() => {
  const names = new Set(selected.value)
  return providers.value.filter((provider) => names.has(provider.name))
})
const canEnableSelected = computed(() =>
  selectedProviders.value.some((provider) => !provider.enabled),
)
const canDisableSelected = computed(() =>
  selectedProviders.value.some((provider) => provider.enabled),
)
const groups = computed(() => request.data.value?.groups || [])
const visibleProviders = computed(() =>
  providers.value.filter(
    (item) =>
      (filter.value === 'all' || item.enabled === (filter.value === 'enabled')) &&
      `${item.name} ${item.api} ${item.model_list.join(' ')}`
        .toLowerCase()
        .includes(keyword.value.toLowerCase()),
  ),
)
const visibleGroups = computed(() =>
  groups.value.filter((item) =>
    `${item.name} ${item.entries.map((entry) => entry.id).join(' ')}`
      .toLowerCase()
      .includes(keyword.value.toLowerCase()),
  ),
)
const detailModels = computed(
  () =>
    modelDetail.value?.model_list.filter((model) =>
      model.toLowerCase().includes(modelSearch.value.toLowerCase()),
    ) || [],
)
watch([keyword, filter, tab], () => {
  selected.value = []
  selectedGroups.value = []
})
watch(visibleProviders, (rows) => {
  selected.value = selected.value.filter((name) => rows.some((row) => row.name === name))
})
watch(visibleGroups, (rows) => {
  selectedGroups.value = selectedGroups.value.filter((name) =>
    rows.some((row) => row.name === name),
  )
})
const groupColumns: TableColumn[] = [
  {
    key: 'name',
    label: 'Auth Group',
    width: 200,
    help: [
      '作用：一组可供多个 Provider 共用的认证凭据。',
      '条目：分别配置 Headers、并发上限与请求配额。',
      '调度：正式请求按组策略选择可用条目。',
    ],
  },
  { key: 'strategy', label: '策略', width: 180 },
  { key: 'provider_count', label: '关联 Provider', width: 130 },
  { key: 'entries', label: '认证条目', width: 170 },
  {
    key: 'summary',
    label: '运行状态',
    width: 260,
    help: [
      '汇总：展示可用、冷却与禁用条目数量。',
      '暂时不可用：并发已满或配额耗尽的条目也会停止分配新请求。',
      '详情：运行状态按当前时刻计算。',
    ],
  },
  { fixed: true, key: 'actions', label: '操作', width: 224, minWidth: 224 },
]
function editProvider(provider?: Provider) {
  if (toggles.busy.value || action.busy.value) return
  editing.value = provider
  providerOpen.value = true
}
function editGroup(group?: AuthGroup) {
  if (toggles.busy.value || action.busy.value) return
  groupEditing.value = group
  groupOpen.value = true
}
function showModels(provider: Provider) {
  modelSearch.value = ''
  modelDetail.value = provider
}
async function saved() {
  notify('已保存')
  await request.refresh()
}
async function reorder(move: RowMove) {
  if (toggles.busy.value || action.busy.value || request.loading.value || keyword.value) return
  const source = providers.value.find((item) => item.name === move.id)
  const target = providers.value.find((item) => item.name === move.targetId)
  if (!source || !target || source.enabled !== target.enabled) return
  await action.run((signal) =>
    providersApi.order(
      moveItem(providers.value, move, (item) => item.name).map((item) => item.name),
      signal,
    ),
  )
}
async function batch(actionName: 'enable' | 'disable' | 'delete') {
  if (toggles.busy.value) return
  const names = selectedProviders.value
    .filter((provider) => actionName === 'delete' || provider.enabled !== (actionName === 'enable'))
    .map((provider) => provider.name)
  if (!names.length) return
  if (
    await action.run(
      (signal) => providersApi.batch(names, actionName, signal),
      `已处理 ${names.length} 个 Provider`,
    )
  )
    selected.value = []
}
async function importProviders(value: unknown, signal: AbortSignal) {
  const result = await providersApi.import(value, signal)
  return `已导入 ${result.count} 个 Provider、${result.auth_groups_count} 个 Auth Group`
}
async function importGroups(value: unknown, signal: AbortSignal) {
  const result = await authGroupsApi.import(value, signal)
  return `已导入 ${result.count} 个 Auth Group`
}
function imported(message: string) {
  notify(message)
  void request.refresh()
}
function exportProviders(names: string[]) {
  if (!names.length || request.loading.value || action.busy.value || toggles.busy.value) return
  const snapshot = [...names]
  void exporting.run((signal) => providersApi.export(snapshot, signal))
}
function exportGroups(names: string[]) {
  if (!names.length || request.loading.value || action.busy.value || toggles.busy.value) return
  const snapshot = [...names]
  void exporting.run((signal) => authGroupsApi.export(snapshot, signal))
}
async function deleteGroups() {
  if (request.loading.value || toggles.busy.value) return
  const names = [...selectedGroups.value]
  if (!names.length) return
  if (
    await action.run(
      (signal) => authGroupsApi.batchDelete(names, signal),
      `已删除 ${names.length} 个 Auth Group`,
    )
  ) {
    selectedGroups.value = selectedGroups.value.filter((name) => !names.includes(name))
  }
}
function setEnabled(provider: Provider, value: boolean) {
  return toggles.run(
    provider.name,
    value,
    (signal) => providersApi.enabled(provider.name, value, signal),
    (updated) =>
      request.patch((data) => ({
        ...data,
        providers: data.providers.map((row) => (row.name === provider.name ? updated : row)),
      })),
  )
}
</script>
<template>
  <div class="page">
    <UiPageHeader title="Provider">
      <template #navigation>
        <UiTabs
          v-model="tab"
          :items="[
            { value: 'providers', label: `Providers · ${providers.length}` },
            { value: 'groups', label: `Auth Groups · ${groups.length}` },
          ]"
        />
      </template>
    </UiPageHeader>
    <UiListToolbar>
      <template #actions>
        <UiButton
          v-if="tab === 'providers'"
          variant="primary"
          icon="plus"
          :disabled="action.busy.value || toggles.busy.value"
          @click="editProvider()"
        >
          新增 Provider
        </UiButton>
        <UiButton
          v-else
          variant="primary"
          icon="plus"
          :disabled="action.busy.value || toggles.busy.value"
          @click="editGroup()"
        >
          新增 Auth Group
        </UiButton>
        <template v-if="tab === 'providers'">
          <UiSegmented
            v-model="filter"
            label="Provider 状态"
            :items="[
              { value: 'enabled', label: '已启用' },
              { value: 'disabled', label: '已禁用' },
              { value: 'all', label: '全部' },
            ]"
          />
          <UiButton
            icon="import"
            :disabled="action.busy.value || toggles.busy.value"
            @click="importing = true"
          >
            导入
          </UiButton>
          <UiTooltip :text="`导出当前状态和搜索条件下的 ${visibleProviders.length} 个 Provider`">
            <UiButton
              icon="export"
              :loading="exporting.busy.value"
              :disabled="
                !visibleProviders.length ||
                request.loading.value ||
                action.busy.value ||
                toggles.busy.value
              "
              @click="exportProviders(visibleProviders.map((item) => item.name))"
            >
              导出
            </UiButton>
          </UiTooltip>
        </template>
        <template v-else>
          <UiButton
            icon="import"
            :disabled="action.busy.value || toggles.busy.value"
            @click="importingGroups = true"
          >
            导入
          </UiButton>
          <UiTooltip :text="`导出当前搜索条件下的 ${visibleGroups.length} 个 Auth Group`">
            <UiButton
              icon="export"
              :loading="exporting.busy.value"
              :disabled="
                !visibleGroups.length ||
                request.loading.value ||
                action.busy.value ||
                toggles.busy.value
              "
              @click="exportGroups(visibleGroups.map((item) => item.name))"
            >
              导出
            </UiButton>
          </UiTooltip>
        </template>
      </template>
      <template #search>
        <UiSearchInput
          v-model="keyword"
          :placeholder="tab === 'providers' ? '搜索名称、地址或模型' : '搜索认证组或条目'"
          aria-label="搜索配置"
        />
      </template>
      <UiIconButton
        icon="refresh"
        label="刷新 Provider 与认证组"
        :loading="request.loading.value"
        @click="request.refresh"
      />
    </UiListToolbar>
    <UiSelectionBar
      v-if="selected.length && tab === 'providers'"
      :count="selected.length"
      :disabled="action.busy.value || toggles.busy.value"
      @clear="selected = []"
    >
      <UiButton
        v-if="canEnableSelected"
        :disabled="action.busy.value || toggles.busy.value"
        @click="batch('enable')"
      >
        启用
      </UiButton>
      <UiButton
        v-if="canDisableSelected"
        :disabled="action.busy.value || toggles.busy.value"
        @click="batch('disable')"
      >
        禁用
      </UiButton>
      <UiButton
        :disabled="
          action.busy.value || toggles.busy.value || exporting.busy.value || request.loading.value
        "
        @click="exportProviders(selected)"
      >
        导出所选
      </UiButton>
      <UiConfirmPopover
        :message="`删除所选 ${selected.length} 个 Provider？`"
        @confirm="batch('delete')"
      >
        <UiButton variant="danger-outline" :disabled="action.busy.value || toggles.busy.value">
          删除
        </UiButton>
      </UiConfirmPopover>
    </UiSelectionBar>
    <UiSelectionBar
      v-if="selectedGroups.length && tab === 'groups'"
      :count="selectedGroups.length"
      :disabled="action.busy.value || toggles.busy.value"
      @clear="selectedGroups = []"
    >
      <UiButton
        :disabled="
          action.busy.value || toggles.busy.value || exporting.busy.value || request.loading.value
        "
        @click="exportGroups(selectedGroups)"
      >
        导出所选
      </UiButton>
      <UiConfirmPopover
        :message="`删除所选 ${selectedGroups.length} 个 Auth Group？`"
        @confirm="deleteGroups"
      >
        <UiButton
          variant="danger-outline"
          :disabled="action.busy.value || toggles.busy.value || request.loading.value"
        >
          删除
        </UiButton>
      </UiConfirmPopover>
    </UiSelectionBar>
    <UiAlert
      v-if="
        request.error.value || action.error.value || clipboard.error.value || exporting.error.value
      "
    >
      {{
        exporting.error.value || action.error.value || clipboard.error.value || request.error.value
      }}
    </UiAlert>
    <ProviderTable
      v-if="tab === 'providers'"
      v-model:selected="selected"
      :rows="visibleProviders"
      :edit-disabled="action.busy.value || toggles.busy.value"
      :reorder-disabled="
        !!keyword || action.busy.value || toggles.busy.value || request.loading.value
      "
      :loading="request.loading.value"
      @reorder="reorder"
      @edit="editProvider"
      @models="showModels"
      @auth="runtime = $event"
    >
      <template #enabled="{ row }">
        <UiSwitch
          :model-value="toggles.value(row.name, row.enabled)"
          :loading="toggles.pending(row.name)"
          :label="`启用 ${row.name}`"
          :disabled="action.busy.value"
          @update:model-value="setEnabled(row, $event)"
        />
      </template>
      <template #actions="{ row }">
        <div class="table-actions">
          <UiIconButton
            icon="edit"
            :label="`编辑 ${row.name}`"
            :disabled="action.busy.value || toggles.busy.value"
            @click="editProvider(row)"
          />
          <UiIconButton
            icon="copy"
            label="复制 Provider"
            :disabled="action.busy.value || toggles.busy.value"
            @click="action.run((signal) => providersApi.copy(row.name, signal), '已复制 Provider')"
          />
          <UiConfirmPopover
            message="是否删除？"
            @confirm="
              action.run((signal) => providersApi.remove(row.name, signal), '已删除 Provider')
            "
          >
            <UiIconButton
              icon="delete"
              :label="`删除 ${row.name}`"
              :disabled="action.busy.value || toggles.busy.value"
            />
          </UiConfirmPopover>
        </div>
      </template>
    </ProviderTable>
    <UiTable
      v-else
      v-model:selected="selectedGroups"
      selectable
      :rows="visibleGroups"
      :columns="groupColumns"
      :row-key="(row) => row.name"
      :loading="request.loading.value"
      storage-key="auth-groups"
      empty="还没有 Auth Group"
    >
      <template #cell-name="{ row }">
        <UiButton
          variant="link"
          :aria-label="`编辑 Auth Group ${row.name}`"
          aria-haspopup="dialog"
          :disabled="action.busy.value || toggles.busy.value"
          @click="editGroup(row)"
        >
          <strong>{{ row.name }}</strong>
        </UiButton>
      </template>
      <template #cell-strategy="{ row }">
        {{ row.strategy === 'sticky_failover' ? '粘滞故障切换' : '最小并发' }}
        <div class="muted">
          <small>默认冷却 {{ row.cooldown_seconds_on_429 }} 秒</small>
        </div>
      </template>
      <template #cell-entries="{ row }">{{ row.entries.length }} 个</template>
      <template #cell-summary="{ row }">
        <div class="actions">
          <UiBadge variant="success">可用 {{ row.summary?.available_count || 0 }}</UiBadge>
          <UiBadge variant="warning">冷却 {{ row.summary?.cooldown_count || 0 }}</UiBadge>
          <UiBadge>禁用 {{ row.summary?.disabled_count || 0 }}</UiBadge>
        </div>
      </template>
      <template #cell-actions="{ row }">
        <div class="table-actions">
          <UiButton size="sm" @click="runtime = row.name">运行状态</UiButton>
          <UiIconButton
            icon="edit"
            :label="`编辑 ${row.name}`"
            :disabled="action.busy.value || toggles.busy.value"
            @click="editGroup(row)"
          />
          <UiConfirmPopover
            :message="`删除认证组 ${row.name}？`"
            @confirm="
              action.run((signal) => authGroupsApi.remove(row.name, signal), '已删除 Auth Group')
            "
          >
            <UiIconButton
              icon="delete"
              :label="`删除 ${row.name}`"
              :disabled="action.busy.value || toggles.busy.value"
            />
          </UiConfirmPopover>
        </div>
      </template>
    </UiTable>
    <ProviderDialog
      v-if="providerOpen"
      :provider="editing"
      :groups="groups"
      @close="providerOpen = false"
      @saved="saved"
    />
    <AuthGroupDialog
      v-if="groupOpen"
      :group="groupEditing"
      @close="groupOpen = false"
      @saved="saved"
    />
    <AuthRuntimeDialog
      v-if="runtime"
      :name="runtime"
      @close="runtime = ''"
      @changed="request.refresh"
    />
    <JsonImportDialog
      v-if="importing"
      v-model:open="importing"
      title="导入 Provider"
      :help="[
        '内容：选择本系统导出的 JSON 文件，或粘贴相同结构的内容。',
        'Provider 重名：自动生成新名称。',
        'Auth Group 同名：保留现有设置，只补入缺少的凭据条目。',
        '生效：提交后写入配置并重载 Provider。',
        '文件内容：可能包含上游密钥与认证请求头。',
      ]"
      :submit="importProviders"
      @imported="imported"
    />
    <JsonImportDialog
      v-if="importingGroups"
      v-model:open="importingGroups"
      title="导入 Auth Groups"
      :help="[
        '内容：选择本系统导出的 Auth Groups 或 Provider JSON 文件，也可粘贴包含 auth_groups 列表的 JSON 对象。',
        '同名组：保留现有设置与同 ID 凭据，只补入缺少的条目。',
        '范围：导入认证组配置与完整凭据，不导入 Provider、用量记录或临时运行状态。',
        '生效：提交后统一写入配置并重载。',
      ]"
      :submit="importGroups"
      @imported="imported"
    />
    <UiDialog
      v-if="modelDetail"
      open
      :title="`${modelDetail.name} · 模型`"
      @update:open="modelDetail = undefined"
    >
      <div class="stack">
        <UiInput v-model="modelSearch" placeholder="搜索模型" aria-label="搜索模型详情" />
        <div v-for="model in detailModels" :key="model" class="model-detail">
          <code>{{ modelDetail.name }}/{{ model }}</code>
          <UiBadge v-if="modelDetail.hidden_model_list?.includes(model)">已隐藏</UiBadge>
          <UiIconButton
            icon="copy"
            label="复制模型 ID"
            @click="clipboard.run(() => copyText(`${modelDetail!.name}/${model}`), '已复制模型 ID')"
          />
        </div>
      </div>
    </UiDialog>
  </div>
</template>
<style scoped>
.model-detail {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.model-detail code {
  min-width: 0;
  overflow-wrap: anywhere;
  margin-right: auto;
}
</style>
