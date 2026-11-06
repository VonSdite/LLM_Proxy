<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { authGroupsApi, providersApi, type AuthGroup, type Provider } from '@/api/providers'
import { useRequest } from '@/features/shared/useRequest'
import { useAction } from '@/features/shared/useAction'
import { moveItem, type RowMove } from '@/features/shared/order'
import { copyText } from '@/features/shared/format'
import JsonImportDialog from '@/features/shared/JsonImportDialog.vue'
import ProviderDialog from '@/features/providers/ProviderDialog.vue'
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
  UiSegmented,
  UiSwitch,
  UiTable,
  UiTabs,
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
const clipboard = useAction()
onMounted(request.refresh)
const tab = ref('providers')
const filter = ref('enabled')
const keyword = ref('')
const selected = ref<string[]>([])
const providerOpen = ref(false)
const editing = ref<Provider>()
const groupOpen = ref(false)
const groupEditing = ref<AuthGroup>()
const importing = ref(false)
const runtime = ref('')
const modelDetail = ref<Provider>()
const modelSearch = ref('')
const providers = computed(() => request.data.value?.providers || [])
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
})
watch(providers, (rows) => {
  selected.value = selected.value.filter((name) => rows.some((row) => row.name === name))
})
const providerColumns: TableColumn[] = [
  { key: 'name', label: 'Provider', width: 170 },
  { key: 'api', label: 'API 地址', width: 300 },
  { key: 'source_format', label: '协议', width: 150 },
  { key: 'models', label: '模型', width: 270 },
  { key: 'auth_group', label: '认证', width: 160 },
  { key: 'enabled', label: '启用', width: 80 },
  { key: 'actions', label: '操作', width: 120 },
]
const groupColumns: TableColumn[] = [
  { key: 'name', label: 'Auth Group', width: 200 },
  { key: 'strategy', label: '策略', width: 180 },
  { key: 'provider_count', label: '关联 Provider', width: 130 },
  { key: 'entries', label: '认证条目', width: 170 },
  { key: 'summary', label: '运行状态', width: 260 },
  { key: 'actions', label: '操作', width: 180 },
]
function editProvider(provider?: Provider) {
  editing.value = provider
  providerOpen.value = true
}
function editGroup(group?: AuthGroup) {
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
  const names = [...selected.value]
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
function imported(message: string) {
  notify(message)
  void request.refresh()
}
</script>
<template>
  <div class="page">
    <header class="page-heading">
      <h1>Provider</h1>
      <UiButton v-if="tab === 'providers'" variant="primary" icon="plus" @click="editProvider()">
        新增 Provider
      </UiButton>
      <UiButton v-else variant="primary" icon="plus" @click="editGroup()">新增 Auth Group</UiButton>
    </header>
    <UiTabs
      v-model="tab"
      :items="[
        { value: 'providers', label: `Providers · ${providers.length}` },
        { value: 'groups', label: `Auth Groups · ${groups.length}` },
      ]"
    />
    <div class="toolbar">
      <UiSegmented
        v-if="tab === 'providers'"
        v-model="filter"
        label="Provider 状态"
        :items="[
          { value: 'enabled', label: '已启用' },
          { value: 'disabled', label: '已禁用' },
          { value: 'all', label: '全部' },
        ]"
      />
      <div class="list-search">
        <UiInput
          v-model="keyword"
          :placeholder="tab === 'providers' ? '搜索名称、地址或模型' : '搜索认证组或条目'"
          aria-label="搜索配置"
        />
      </div>
      <div class="actions">
        <template v-if="tab === 'providers'">
          <UiButton icon="upload" @click="importing = true">导入</UiButton>
          <UiButton
            icon="download"
            :disabled="action.busy.value || !providers.length"
            @click="
              action.run((signal) =>
                providersApi.export(
                  selected.length ? selected : providers.map((item) => item.name),
                  signal,
                ),
              )
            "
          >
            {{ selected.length ? '导出所选' : '导出' }}
          </UiButton>
        </template>
        <UiIconButton
          icon="refresh"
          label="刷新 Provider 与认证组"
          :loading="request.loading.value"
          @click="request.refresh"
        />
      </div>
    </div>
    <div v-if="selected.length && tab === 'providers'" class="actions">
      <span class="muted">已选 {{ selected.length }}</span>
      <UiButton size="sm" :disabled="action.busy.value" @click="batch('enable')">启用</UiButton>
      <UiButton size="sm" :disabled="action.busy.value" @click="batch('disable')">禁用</UiButton>
      <UiConfirmPopover
        :message="`删除所选 ${selected.length} 个 Provider？`"
        @confirm="batch('delete')"
      >
        <UiButton size="sm" variant="danger" :disabled="action.busy.value">删除</UiButton>
      </UiConfirmPopover>
    </div>
    <UiAlert v-if="request.error.value || action.error.value || clipboard.error.value">
      {{ action.error.value || clipboard.error.value || request.error.value }}
    </UiAlert>
    <UiTable
      v-if="tab === 'providers'"
      v-model:selected="selected"
      :rows="visibleProviders"
      :columns="providerColumns"
      :row-key="(row) => row.name"
      selectable
      :reorderable="!keyword && !action.busy.value"
      :loading="request.loading.value"
      storage-key="providers"
      empty="没有匹配的 Provider"
      @reorder="reorder"
    >
      <template #cell-name="{ row }">
        <strong>{{ row.name }}</strong>
        <div v-if="row.safe_desensitization_enabled"><UiBadge>安全脱敏</UiBadge></div>
      </template>
      <template #cell-api="{ row }">
        <span class="mono">{{ row.api }}</span>
      </template>
      <template #cell-source_format="{ row }">
        <small>{{ row.source_format }}</small>
      </template>
      <template #cell-models="{ row }">
        <div class="model-preview">
          <code
            v-for="model in row.model_list.slice(0, 2)"
            :key="model"
            class="truncate"
            :title="model"
          >
            {{ model }}
          </code>
          <UiButton variant="ghost" size="sm" @click="showModels(row)">
            {{ row.model_list.length }} 个模型
          </UiButton>
        </div>
      </template>
      <template #cell-auth_group="{ row }">
        <UiButton v-if="row.auth_group" variant="ghost" size="sm" @click="runtime = row.auth_group">
          {{ row.auth_group }}
        </UiButton>
        <small v-else class="muted">{{ row.api_key ? 'API Key' : '无认证' }}</small>
      </template>
      <template #cell-enabled="{ row }">
        <UiSwitch
          :model-value="row.enabled"
          :label="`启用 ${row.name}`"
          :disabled="action.busy.value"
          @update:model-value="
            action.run((signal) => providersApi.enabled(row.name, $event, signal))
          "
        />
      </template>
      <template #cell-actions="{ row }">
        <div class="actions">
          <UiIconButton icon="edit" :label="`编辑 ${row.name}`" @click="editProvider(row)" />
          <UiIconButton
            icon="copy"
            label="复制 Provider"
            :disabled="action.busy.value"
            @click="action.run((signal) => providersApi.copy(row.name, signal), '已复制 Provider')"
          />
          <UiConfirmPopover
            :message="`删除 Provider ${row.name}？`"
            @confirm="
              action.run((signal) => providersApi.remove(row.name, signal), '已删除 Provider')
            "
          >
            <UiIconButton icon="delete" :label="`删除 ${row.name}`" :disabled="action.busy.value" />
          </UiConfirmPopover>
        </div>
      </template>
    </UiTable>
    <UiTable
      v-else
      :rows="visibleGroups"
      :columns="groupColumns"
      :row-key="(row) => row.name"
      :loading="request.loading.value"
      storage-key="auth-groups"
      empty="还没有 Auth Group"
    >
      <template #cell-name="{ row }">
        <strong>{{ row.name }}</strong>
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
        <div class="actions">
          <UiButton size="sm" @click="runtime = row.name">运行状态</UiButton>
          <UiIconButton icon="edit" :label="`编辑 ${row.name}`" @click="editGroup(row)" />
          <UiConfirmPopover
            :message="`删除认证组 ${row.name}？`"
            @confirm="
              action.run((signal) => authGroupsApi.remove(row.name, signal), '已删除 Auth Group')
            "
          >
            <UiIconButton icon="delete" :label="`删除 ${row.name}`" :disabled="action.busy.value" />
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
      :submit="importProviders"
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
          <UiBadge v-if="modelDetail.hidden_model_list.includes(model)">已隐藏</UiBadge>
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
.model-preview {
  display: grid;
  justify-items: start;
  gap: var(--space-1);
}

.model-preview code {
  max-width: 100%;
}

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
