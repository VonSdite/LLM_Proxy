<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { mappingsApi, type ModelMapping } from '@/api/model-mappings'
import { useRequest } from '@/features/shared/useRequest'
import { useAction } from '@/features/shared/useAction'
import { useToggle } from '@/features/shared/useToggle'
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
  UiListToolbar,
  UiPageHeader,
  UiSearchInput,
  UiSegmented,
  UiSelectionBar,
  UiSwitch,
  UiTable,
  UiTooltip,
  notify,
  type TableColumn,
} from '@/components/ui'
const request = useRequest(mappingsApi.list)
const action = useAction(request.refresh)
const exporting = useAction()
const toggles = useToggle()
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
watch(visible, (rows) => {
  selected.value = selected.value.filter((id) => rows.some((row) => row.id === id))
})
const columns: TableColumn[] = [
  { key: 'id', label: '对外模型', width: 240 },
  { key: 'strategy', label: '策略', width: 155 },
  {
    key: 'targets',
    label: '目标模型',
    width: 440,
    help: '当前标记是最近选中的目标，数字是优先级，越大越优先。单个目标被禁用或冷却时通常跳过；无正常候选时仍会尝试存在于运行时目录中的目标兜底。停止整个映射请关闭映射的启用开关。',
  },
  { fixed: true, key: 'enabled', label: '启用', width: 90 },
  { fixed: true, key: 'actions', label: '操作', width: 168, minWidth: 168 },
]
const states = {
  available: '可用',
  disabled: '已禁用',
  auto_disabled: '故障禁用',
  cooldown: '冷却中',
  unavailable: '不可用',
}
function edit(mapping?: ModelMapping) {
  if (toggles.busy.value || action.busy.value) return
  editing.value = mapping
  formOpen.value = true
}
async function saved() {
  notify('已保存模型映射')
  await request.refresh()
}
async function reorder(move: RowMove) {
  if (toggles.busy.value || action.busy.value || request.loading.value || keyword.value) return
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
  if (toggles.busy.value) return
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
function exportMappings(ids: string[]) {
  if (!ids.length || request.loading.value || action.busy.value || toggles.busy.value) return
  const snapshot = [...ids]
  void exporting.run((signal) => mappingsApi.export(snapshot, signal))
}
const targetKey = (id: string, model: string) => JSON.stringify([id, model])
function updateMapping(updated: ModelMapping) {
  request.patch((data) => ({
    ...data,
    model_mappings: data.model_mappings.map((row) => (row.id === updated.id ? updated : row)),
  }))
}
function setEnabled(id: string, value: boolean) {
  return toggles.run(id, value, (signal) => mappingsApi.enabled(id, value, signal), updateMapping)
}
function setTarget(id: string, model: string, value: boolean) {
  return toggles.run(
    targetKey(id, model),
    value,
    (signal) => mappingsApi.toggleTarget(id, model, value, signal),
    updateMapping,
    id,
  )
}
</script>
<template>
  <div class="page">
    <UiPageHeader title="模型映射" :count="visible.length" />
    <UiListToolbar>
      <template #actions>
        <UiButton
          variant="primary"
          icon="plus"
          :disabled="action.busy.value || toggles.busy.value"
          @click="edit()"
        >
          创建映射
        </UiButton>
        <UiSegmented
          v-model="filter"
          label="映射状态"
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
        <UiTooltip :text="`导出当前状态和搜索条件下的 ${visible.length} 个模型映射`">
          <UiButton
            icon="export"
            :loading="exporting.busy.value"
            :disabled="
              !visible.length || request.loading.value || action.busy.value || toggles.busy.value
            "
            @click="exportMappings(visible.map((item) => item.id))"
          >
            导出
          </UiButton>
        </UiTooltip>
      </template>
      <template #search>
        <UiSearchInput v-model="keyword" placeholder="搜索模型 ID" aria-label="搜索模型映射" />
      </template>
      <UiIconButton
        icon="refresh"
        label="刷新模型映射"
        :loading="request.loading.value"
        @click="request.refresh"
      />
    </UiListToolbar>
    <UiSelectionBar
      v-if="selected.length"
      :count="selected.length"
      :disabled="action.busy.value || toggles.busy.value"
      @clear="selected = []"
    >
      <UiButton
        :disabled="
          action.busy.value || toggles.busy.value || exporting.busy.value || request.loading.value
        "
        @click="exportMappings(selected)"
      >
        导出所选
      </UiButton>
      <UiConfirmPopover :message="`删除所选 ${selected.length} 个映射？`" @confirm="removeSelected">
        <UiButton variant="danger-outline" :disabled="action.busy.value || toggles.busy.value">
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
    <UiAlert v-if="request.data.value && !request.data.value.enabled" variant="warning">
      模型映射功能已关闭，可在系统设置中启用。
    </UiAlert>
    <UiTable
      v-model:selected="selected"
      :rows="visible"
      :columns="columns"
      :row-key="(row) => row.id"
      selectable
      reorderable
      :reorder-group="(row) => String(row.enabled)"
      :reorder-disabled="
        !!keyword || action.busy.value || toggles.busy.value || request.loading.value
      "
      :loading="request.loading.value"
      storage-key="model-mappings"
      empty="没有匹配的模型映射"
      @reorder="reorder"
    >
      <template #cell-id="{ row }">
        <div class="actions">
          <UiButton
            variant="link"
            :aria-label="`编辑模型映射 ${row.id}`"
            aria-haspopup="dialog"
            :disabled="action.busy.value || toggles.busy.value"
            @click="edit(row)"
          >
            <code>{{ row.id }}</code>
          </UiButton>
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
              :model-value="
                toggles.value(
                  targetKey(row.id, target.model_id),
                  target.enabled && target.status === 'available',
                )
              "
              :loading="toggles.pending(targetKey(row.id, target.model_id))"
              :label="`${target.model_id} 启用状态`"
              :disabled="
                target.available_model === false ||
                action.busy.value ||
                (toggles.blocked(row.id) && !toggles.pending(targetKey(row.id, target.model_id)))
              "
              @update:model-value="setTarget(row.id, target.model_id, $event)"
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
          :model-value="toggles.value(row.id, row.enabled)"
          :loading="toggles.pending(row.id)"
          :label="`启用映射 ${row.id}`"
          :disabled="action.busy.value || (toggles.blocked(row.id) && !toggles.pending(row.id))"
          @update:model-value="setEnabled(row.id, $event)"
        />
      </template>
      <template #cell-actions="{ row }">
        <div class="table-actions">
          <UiIconButton
            icon="edit"
            :label="`编辑 ${row.id}`"
            :disabled="action.busy.value || toggles.busy.value"
            @click="edit(row)"
          />
          <UiIconButton
            icon="copy"
            label="复制映射"
            :disabled="action.busy.value || toggles.busy.value"
            @click="action.run((signal) => mappingsApi.copy(row.id, signal), '已复制映射')"
          />
          <UiConfirmPopover
            :message="`删除映射 ${row.id}？`"
            @confirm="action.run((signal) => mappingsApi.remove(row.id, signal), '已删除映射')"
          >
            <UiIconButton
              icon="delete"
              :label="`删除 ${row.id}`"
              :disabled="action.busy.value || toggles.busy.value"
            />
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
      help="选择本系统导出的 JSON 文件，或粘贴相同结构的内容。映射 ID 与现有映射冲突时自动添加后缀，保留原映射；导入不会自动创建缺失的 Provider 或目标模型，请在列表中检查目标是否可用。"
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
