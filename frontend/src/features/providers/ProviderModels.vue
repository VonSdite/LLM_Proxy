<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { providersApi, type AuthGroup, type ModelTestResult, type Provider } from '@/api/providers'
import { errorMessage, isAbort } from '@/api/client'
import { moveItem, type RowMove } from '@/features/shared/order'
import { useDraftChanges } from '@/features/shared/useDraftChanges'
import ModelPickerDialog from '@/features/shared/ModelPickerDialog.vue'
import { modelTpsHelp, modelTtftHelp } from './modelTestHelp'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmPopover,
  UiField,
  UiHelpPopover,
  UiIconButton,
  UiInput,
  UiSelect,
  UiSelectionBar,
  UiTable,
  type TableColumn,
} from '@/components/ui'
const props = defineProps<{ provider: Provider; groups: AuthGroup[]; disabled?: boolean }>()
const emit = defineEmits<{
  change: [models: string[], hidden: string[]]
  'dirty-change': [value: boolean]
}>()
interface ModelRow {
  id: string
  model: string
  visible: boolean
  result?: ModelTestResult
  testing: boolean
}
let sequence = 0
const rows = ref<ModelRow[]>(
  props.provider.model_list.map((model) => ({
    id: String(++sequence),
    model,
    visible: !props.provider.hidden_model_list?.includes(model),
    testing: false,
  })),
)
const dirty = useDraftChanges(() => rows.value.map(({ model, visible }) => ({ model, visible })))
watch(dirty, (value) => emit('dirty-change', value), { flush: 'sync' })
const selected = ref<string[]>([])
const root = ref<HTMLElement>()
const authEntry = ref('')
const fetching = ref(false)
const testing = computed(() => rows.value.some((row) => row.testing))
const bulkTesting = ref(false)
const selectionTesting = computed(() =>
  rows.value.some((row) => selected.value.includes(row.id) && row.testing),
)
const error = ref('')
const fetched = ref<string[]>()
const allVisible = computed(() => rows.value.length > 0 && rows.value.every((row) => row.visible))
function visibilityAll() {
  const visible = !allVisible.value
  rows.value.forEach((row) => {
    row.visible = visible
  })
  publish()
}
const group = computed(() => props.groups.find((item) => item.name === props.provider.auth_group))
watch(
  group,
  (value) => {
    authEntry.value =
      value?.entries.find((entry) => entry.enabled)?.id || value?.entries[0]?.id || ''
  },
  { immediate: true },
)
const fingerprint = computed(() =>
  JSON.stringify({
    ...props.provider,
    model_list: undefined,
    hidden_model_list: undefined,
    auth_entry_id: authEntry.value,
  }),
)
let batch = 0
const controllers = new Map<string, AbortController>()
let fetchController: AbortController | undefined
function cancel() {
  batch++
  controllers.forEach((controller) => controller.abort())
  controllers.clear()
  rows.value.forEach((row) => {
    row.testing = false
  })
  bulkTesting.value = false
  fetchController?.abort()
  fetching.value = false
}
watch(fingerprint, () => {
  cancel()
  rows.value.forEach((row) => {
    row.result = undefined
  })
})
onBeforeUnmount(cancel)
const columns: TableColumn[] = [
  {
    key: 'model',
    label: '模型 ID',
    width: 320,
    help: [
      '填写上游实际模型 ID，保留模型自身的组织或路径前缀。',
      '客户端请求时：加上本页 Provider 名称，例如 main/org/model。',
    ],
  },
  {
    key: 'visible',
    label: '显示',
    width: 80,
    help: ['开启：在对外模型目录中显示。', '隐藏：不在目录中展示，仍可通过完整模型 ID 直接请求。'],
  },
  {
    key: 'result',
    label: '结果',
    width: 80,
  },
  {
    key: 'latency',
    label: '首字延迟',
    width: 130,
    help: modelTtftHelp,
  },
  {
    key: 'tps',
    label: 'TPS',
    width: 110,
    help: modelTpsHelp,
  },
  { key: 'actions', label: '操作', width: 155 },
]
function publish() {
  const models = [...new Set(rows.value.map((row) => row.model.trim()).filter(Boolean))]
  const visible = new Set(rows.value.filter((row) => row.visible).map((row) => row.model.trim()))
  emit(
    'change',
    models,
    models.filter((model) => !visible.has(model)),
  )
}
function change(row: ModelRow, model: string) {
  controllers.get(row.id)?.abort()
  row.model = model
  row.result = undefined
  row.testing = false
  publish()
}
function visibility(row: ModelRow, visible: boolean) {
  row.visible = visible
  publish()
}
function add(models = ['']) {
  for (const model of models) {
    if (model && rows.value.some((row) => row.model.trim() === model)) continue
    rows.value.push({ id: String(++sequence), model, visible: true, testing: false })
  }
  publish()
}
async function addRow() {
  add()
  const id = rows.value.at(-1)!.id
  await nextTick()
  root.value?.querySelector<HTMLInputElement>(`input[aria-label="模型 ${id}"]`)?.focus()
}
function remove(ids: string[]) {
  ids.forEach((id) => controllers.get(id)?.abort())
  rows.value = rows.value.filter((row) => !ids.includes(row.id))
  selected.value = selected.value.filter((id) => !ids.includes(id))
  publish()
}
function reorder(move: RowMove) {
  rows.value = moveItem(rows.value, move, (row) => row.id)
  publish()
}
function tidy() {
  const seen = new Map<string, ModelRow>()
  for (const row of rows.value) {
    const model = row.model.trim()
    if (!model) continue
    const old = seen.get(model)
    if (old) old.visible ||= row.visible
    else {
      row.model = model
      seen.set(model, row)
    }
  }
  rows.value = [...seen.values()].sort((a, b) =>
    a.model < b.model ? -1 : a.model > b.model ? 1 : 0,
  )
  selected.value = []
  publish()
}
function ready() {
  error.value = ''
  if (!props.provider.api.trim()) error.value = '请先填写 API 地址'
  else if (props.provider.auth_group && !authEntry.value)
    error.value = '请选择用于测试的 Auth Entry'
  return !error.value
}
async function fetchModels() {
  if (fetching.value || !ready()) return
  const snapshot = fingerprint.value
  fetchController?.abort()
  const controller = new AbortController()
  fetchController = controller
  fetching.value = true
  try {
    const p = props.provider
    const result = await providersApi.fetchModels(
      {
        name: p.name,
        api: p.api,
        api_key: p.api_key,
        auth_group: p.auth_group,
        auth_entry_id: authEntry.value,
        source_format: p.source_format,
        hook: p.hook,
        proxy_mode: p.proxy_mode,
        proxy: p.proxy,
        timeout_seconds: p.timeout_seconds,
        verify_ssl: p.verify_ssl,
      },
      controller.signal,
    )
    if (snapshot === fingerprint.value && !controller.signal.aborted)
      fetched.value = [...new Set(result.fetched_models)]
  } catch (reason) {
    if (!isAbort(reason) && !controller.signal.aborted) error.value = errorMessage(reason)
  } finally {
    if (fetchController === controller) fetching.value = false
  }
}
function applyFetched(models: string[]) {
  const candidates = new Set(fetched.value || [])
  const chosen = new Set(models)
  const removed = new Set(
    rows.value
      .filter((row) => candidates.has(row.model.trim()) && !chosen.has(row.model.trim()))
      .map((row) => row.id),
  )
  removed.forEach((id) => controllers.get(id)?.abort())
  rows.value = rows.value.filter((row) => !removed.has(row.id))
  selected.value = selected.value.filter((id) => !removed.has(id))
  add(models)
  fetched.value = undefined
}
async function test(ids: string[], bulk = false) {
  if (props.disabled || (bulk && bulkTesting.value) || !ready()) return
  const targets = rows.value.filter(
    (row) => ids.includes(row.id) && row.model.trim() && !row.testing,
  )
  if (!targets.length) {
    error.value = '请选择至少一个模型'
    return
  }
  const current = batch
  const snapshot = fingerprint.value
  const payload = JSON.parse(JSON.stringify(props.provider)) as Provider
  const entry = authEntry.value
  if (bulk) bulkTesting.value = true
  for (const row of targets) {
    if (batch !== current) break
    if (!rows.value.includes(row) || row.testing || !row.model.trim()) continue
    const model = row.model.trim()
    const controller = new AbortController()
    controllers.set(row.id, controller)
    row.result = undefined
    row.testing = true
    const isCurrent = () =>
      !controller.signal.aborted &&
      current === batch &&
      snapshot === fingerprint.value &&
      rows.value.includes(row) &&
      row.model.trim() === model
    try {
      const result = await providersApi.test(
        { ...payload, auth_entry_id: entry, models: [model] },
        controller.signal,
      )
      if (isCurrent()) row.result = result.results[0]
    } catch (reason) {
      if (isCurrent() && !isAbort(reason))
        row.result = {
          requested_model: model,
          available: false,
          first_token_latency_ms: null,
          tps: null,
          response_model: null,
          error: errorMessage(reason),
        }
    } finally {
      if (controllers.get(row.id) === controller) {
        controllers.delete(row.id)
        row.testing = false
      }
    }
  }
  if (bulk && current === batch) bulkTesting.value = false
}
</script>
<template>
  <div ref="root" class="stack">
    <div class="toolbar">
      <div class="actions">
        <h2>模型</h2>
        <UiHelpPopover
          label="模型列表操作说明"
          :text="[
            '拉取：使用当前表单的地址、认证与代理，依次尝试推导出的 /v1/models 和 /models。',
            '选择：已有模型自动勾选，取消勾选后确认会从草稿移除；未在本次拉取结果中的模型保留。',
            '整理：去除首尾空白、空行和重复模型，再按模型 ID 排序。',
            '显示：仅控制模型目录中的展示，隐藏后仍可直接请求模型。',
            '测试：真实请求上游，可能产生用量；仅锁定正在测试行的模型 ID、测试和删除，其他行可操作，拖动排序不影响结果归属。',
            '保存：当前模型列表配置保存后生效。',
          ]"
        />
        <small class="muted">{{ rows.length }} 项</small>
      </div>
      <div class="actions model-toolbar-actions">
        <UiField
          v-if="group"
          label="测试 Auth Entry"
          class="auth-entry-select"
          :help="[
            '拉取模型与模型测试：使用选定条目的 Headers。',
            '作用范围：仅用于当前表单的拉取与测试，不改变正式调度策略或凭据配置。',
          ]"
        >
          <UiSelect
            v-model="authEntry"
            compact
            :options="
              group.entries.map((entry) => ({
                value: entry.id,
                label: entry.id,
                description: entry.enabled ? undefined : '配置已禁用',
              }))
            "
            :disabled="disabled"
          />
        </UiField>
        <UiButton size="sm" :disabled="disabled || testing" @click="tidy">整理</UiButton>
        <UiButton v-if="testing" size="sm" @click="cancel">停止测试</UiButton>
        <UiButton
          size="sm"
          icon="download"
          :disabled="disabled"
          :loading="fetching"
          @click="fetchModels"
        >
          拉取模型
        </UiButton>
      </div>
    </div>
    <UiSelectionBar
      v-if="selected.length"
      :count="selected.length"
      :disabled="disabled"
      @clear="selected = []"
    >
      <UiButton :loading="bulkTesting" :disabled="disabled" @click="test(selected, true)">
        测试
      </UiButton>
      <UiConfirmPopover
        :message="`删除所选 ${selected.length} 个模型？`"
        @confirm="remove(selected)"
      >
        <UiButton variant="danger-outline" :disabled="disabled || selectionTesting">删除</UiButton>
      </UiConfirmPopover>
    </UiSelectionBar>
    <UiAlert v-if="error">{{ error }}</UiAlert>
    <UiTable
      v-model:selected="selected"
      :rows="rows"
      :columns="columns"
      :row-key="(row) => row.id"
      selectable
      reorderable
      :reorder-disabled="disabled"
      add-label="添加"
      :add-disabled="disabled"
      @add="addRow"
      @reorder="reorder"
    >
      <template #header-actions>
        <span class="model-actions-heading">操作</span>
      </template>
      <template #header-visible>
        <UiIconButton
          size="sm"
          :icon="allVisible ? 'eye' : 'eyeOff'"
          :label="allVisible ? '全部隐藏' : '全部显示'"
          :disabled="disabled || !rows.length"
          @click="visibilityAll"
        />
      </template>
      <template #cell-model="{ row }">
        <UiInput
          :model-value="row.model"
          mono
          :aria-label="`模型 ${row.id}`"
          :disabled="disabled || row.testing"
          @update:model-value="change(row, $event)"
        />
      </template>
      <template #cell-visible="{ row }">
        <UiIconButton
          size="sm"
          :icon="row.visible ? 'eye' : 'eyeOff'"
          :label="`${row.visible ? '隐藏' : '显示'} ${row.model || '模型'}`"
          :aria-pressed="row.visible"
          :disabled="disabled"
          @click="visibility(row, !row.visible)"
        />
      </template>
      <template #cell-result="{ row }">
        <UiBadge v-if="row.testing">测试中</UiBadge>
        <UiBadge v-else-if="row.result" :variant="row.result.available ? 'success' : 'danger'">
          {{ row.result.available ? '可用' : '失败' }}
        </UiBadge>
        <span v-else class="muted">—</span>
      </template>
      <template #cell-latency="{ row }">
        <span class="number">
          {{
            row.testing || row.result?.first_token_latency_ms == null
              ? '—'
              : `${row.result.first_token_latency_ms} ms`
          }}
        </span>
      </template>
      <template #cell-tps="{ row }">
        <span class="number">
          {{ row.testing || row.result?.tps == null ? '—' : `${row.result.tps} tok/s` }}
        </span>
      </template>
      <template #cell-actions="{ row }">
        <div class="actions model-actions">
          <UiButton
            size="sm"
            :disabled="disabled || row.testing || !row.model.trim()"
            @click="test([row.id])"
          >
            测试
          </UiButton>
          <UiIconButton
            icon="delete"
            label="移除模型"
            :disabled="disabled || row.testing"
            @click="remove([row.id])"
          />
        </div>
        <span v-if="!row.testing && row.result && !row.result.available" class="danger model-error">
          {{ row.result.error || '上游未返回详细错误，请检查地址、认证与代理后重试。' }}
        </span>
      </template>
    </UiTable>
    <ModelPickerDialog
      v-if="fetched"
      title="选择上游模型"
      :models="fetched"
      :initial="fetched.filter((model) => rows.some((row) => row.model.trim() === model))"
      @close="fetched = undefined"
      @apply="applyFetched"
    />
  </div>
</template>
<style scoped>
.auth-entry-select {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  align-items: center;
  width: min(320px, 100%);
}

.model-toolbar-actions {
  flex: 1 1 480px;
  justify-content: flex-end;
  min-width: 0;
  max-width: 100%;
  margin-left: auto;
}

.model-error {
  display: block;
  margin-top: var(--space-1);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  text-align: center;
}

.model-actions {
  justify-content: center;
}

.model-actions-heading {
  width: 100%;
  text-align: center;
}
</style>
