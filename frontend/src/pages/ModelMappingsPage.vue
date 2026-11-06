<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { mappingsApi, type ModelMapping } from '@/api/model-mappings'
import { useRequest } from '@/features/shared/useRequest'
import { useAction } from '@/features/shared/useAction'
import { moveItem, type RowMove } from '@/features/shared/order'
import { copyText, dateTime } from '@/features/shared/format'
import MappingDialog from '@/features/model-mappings/MappingDialog.vue'
import JsonImportDialog from '@/features/shared/JsonImportDialog.vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmPopover,
  UiHelpPopover,
  UiIconButton,
  UiInput,
  UiSegmented,
  UiSwitch,
  UiTable,
  notify,
  type TableColumn,
} from '@/components/ui'
const request = useRequest(mappingsApi.list)
const action = useAction(request.refresh)
const clipboard = useAction()
onMounted(request.refresh)
const keyword = ref('')
const filter = ref('enabled')
const selected = ref<string[]>([])
const formOpen = ref(false)
const editing = ref<ModelMapping>()
const importing = ref(false)
const mappings = computed(() => request.data.value?.model_mappings || [])
const visible = computed(() =>
  mappings.value.filter(
    (item) =>
      (filter.value === 'all' || item.enabled === (filter.value === 'enabled')) &&
      `${item.id} ${item.targets.map((target) => target.model_id).join(' ')}`
        .toLowerCase()
        .includes(keyword.value.toLowerCase()),
  ),
)
watch([keyword, filter], () => {
  selected.value = []
})
watch(mappings, (rows) => {
  selected.value = selected.value.filter((id) => rows.some((row) => row.id === id))
})
const columns: TableColumn[] = [
  { key: 'id', label: '对外模型', width: 240 },
  { key: 'strategy', label: '策略', width: 155 },
  { key: 'targets', label: '目标模型', width: 440 },
  { key: 'enabled', label: '启用', width: 90 },
  { key: 'actions', label: '操作', width: 120 },
]
const states = {
  available: '可用',
  disabled: '已禁用',
  auto_disabled: '故障禁用',
  cooldown: '冷却中',
  unavailable: '不可用',
}
function edit(mapping?: ModelMapping) {
  editing.value = mapping
  formOpen.value = true
}
async function saved() {
  notify('已保存模型映射')
  await request.refresh()
}
async function reorder(move: RowMove) {
  const source = mappings.value.find((item) => item.id === move.id)
  const target = mappings.value.find((item) => item.id === move.targetId)
  if (!source || !target || source.enabled !== target.enabled) return
  await action.run((signal) =>
    mappingsApi.order(
      moveItem(mappings.value, move, (item) => item.id).map((item) => item.id),
      signal,
    ),
  )
}
async function removeSelected() {
  const ids = [...selected.value]
  if (
    await action.run(
      (signal) => mappingsApi.batchDelete(ids, signal),
      `已删除 ${ids.length} 个映射`,
    )
  )
    selected.value = []
}
async function importMappings(value: unknown, signal: AbortSignal) {
  const result = await mappingsApi.import(value, signal)
  return `已导入 ${result.count} 个映射`
}
function imported(message: string) {
  notify(message)
  void request.refresh()
}
</script>
<template>
  <div class="page">
    <header class="page-heading">
      <h1>模型映射</h1>
      <UiButton variant="primary" icon="plus" @click="edit()">创建映射</UiButton>
    </header>
    <div class="toolbar">
      <UiSegmented
        v-model="filter"
        label="映射状态"
        :items="[
          { value: 'enabled', label: '已启用' },
          { value: 'disabled', label: '已禁用' },
          { value: 'all', label: '全部' },
        ]"
      />
      <div class="list-search">
        <UiInput v-model="keyword" placeholder="搜索模型 ID" aria-label="搜索模型映射" />
      </div>
    </div>
    <div class="toolbar">
      <span class="muted">
        {{ visible.length }} 个映射
        <span v-if="selected.length">· 已选 {{ selected.length }}</span>
      </span>
      <div class="actions">
        <UiConfirmPopover
          v-if="selected.length"
          :message="`删除所选 ${selected.length} 个映射？`"
          @confirm="removeSelected"
        >
          <UiButton variant="danger" :disabled="action.busy.value">删除所选</UiButton>
        </UiConfirmPopover>
        <UiButton icon="upload" @click="importing = true">导入</UiButton>
        <UiButton
          icon="download"
          :disabled="action.busy.value"
          @click="action.run((signal) => mappingsApi.export(selected, signal))"
        >
          {{ selected.length ? '导出所选' : '导出' }}
        </UiButton>
        <UiIconButton
          icon="refresh"
          label="刷新模型映射"
          :loading="request.loading.value"
          @click="request.refresh"
        />
      </div>
    </div>
    <UiAlert v-if="request.error.value || action.error.value || clipboard.error.value">
      {{ action.error.value || clipboard.error.value || request.error.value }}
    </UiAlert>
    <UiAlert v-if="request.data.value && !request.data.value.enabled" variant="warning">
      模型映射功能已关闭，可在系统设置中启用。
    </UiAlert>
    <UiTable
      v-model:selected="selected"
      :rows="visible"
      :columns="columns"
      :row-key="(row) => row.id"
      selectable
      :reorderable="!keyword && !action.busy.value"
      :loading="request.loading.value"
      storage-key="model-mappings"
      empty="没有匹配的模型映射"
      @reorder="reorder"
    >
      <template #cell-id="{ row }">
        <div class="actions">
          <code>{{ row.id }}</code>
          <UiIconButton
            icon="copy"
            label="复制模型 ID"
            @click="clipboard.run(() => copyText(row.id), '已复制模型 ID')"
          />
        </div>
        <small v-if="row.conflict_source" class="danger">
          与 {{ row.conflict_source }} 模型重名
        </small>
      </template>
      <template #cell-strategy="{ row }">
        <div class="stack-small">
          <span>{{ row.strategy === 'sticky_failover' ? '粘滞故障切换' : '最高优先级' }}</span>
          <small class="muted">冷却 {{ row.cooldown_seconds_on_429 }} 秒</small>
          <UiBadge v-if="row.safe_desensitization_enabled">安全脱敏</UiBadge>
        </div>
      </template>
      <template #cell-targets="{ row }">
        <div class="mapping-target-list">
          <div v-for="target in row.targets" :key="target.model_id" class="mapping-target-summary">
            <UiSwitch
              :model-value="target.enabled && target.status === 'available'"
              :label="`${target.model_id} 启用状态`"
              :disabled="target.available_model === false || action.busy.value"
              @update:model-value="
                action.run((signal) =>
                  mappingsApi.toggleTarget(row.id, target.model_id, $event, signal),
                )
              "
            />
            <code>{{ target.model_id }}</code>
            <UiBadge v-if="target.current" variant="success">当前</UiBadge>
            <UiHelpPopover
              v-if="target.status && target.status !== 'available'"
              :label="states[target.status]"
            >
              <p>{{ states[target.status] }}</p>
              <p v-if="target.cooldown_until">恢复时间：{{ dateTime(target.cooldown_until) }}</p>
              <p v-if="target.last_status_code">HTTP {{ target.last_status_code }}</p>
              <p>{{ target.last_error_message || target.last_error_type }}</p>
            </UiHelpPopover>
            <small class="muted number">{{ target.priority }}</small>
          </div>
        </div>
      </template>
      <template #cell-enabled="{ row }">
        <UiSwitch
          :model-value="row.enabled"
          :label="`启用映射 ${row.id}`"
          :disabled="action.busy.value"
          @update:model-value="action.run((signal) => mappingsApi.enabled(row.id, $event, signal))"
        />
      </template>
      <template #cell-actions="{ row }">
        <div class="actions">
          <UiIconButton icon="edit" :label="`编辑 ${row.id}`" @click="edit(row)" />
          <UiIconButton
            icon="copy"
            label="复制映射"
            :disabled="action.busy.value"
            @click="action.run((signal) => mappingsApi.copy(row.id, signal), '已复制映射')"
          />
          <UiConfirmPopover
            :message="`删除映射 ${row.id}？`"
            @confirm="action.run((signal) => mappingsApi.remove(row.id, signal), '已删除映射')"
          >
            <UiIconButton icon="delete" :label="`删除 ${row.id}`" :disabled="action.busy.value" />
          </UiConfirmPopover>
        </div>
      </template>
    </UiTable>
    <MappingDialog
      v-if="formOpen"
      :mapping="editing"
      :models="request.data.value?.available_targets || []"
      @close="formOpen = false"
      @saved="saved"
    />
    <JsonImportDialog
      v-if="importing"
      v-model:open="importing"
      title="导入模型映射"
      :submit="importMappings"
      @imported="imported"
    />
  </div>
</template>
<style scoped>
.mapping-target-list {
  display: grid;
  gap: var(--space-2);
}

.mapping-target-summary {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}

.mapping-target-summary code {
  overflow-wrap: anywhere;
  min-width: 0;
}

.mapping-target-summary small {
  margin-left: auto;
}

.stack-small {
  display: grid;
  gap: var(--space-1);
  justify-items: start;
}
</style>
