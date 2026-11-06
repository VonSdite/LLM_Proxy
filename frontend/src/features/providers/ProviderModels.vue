<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { providersApi, type AuthGroup, type ModelTestResult, type Provider } from '@/api/providers'
import { errorMessage, isAbort } from '@/api/client'
import { moveItem, type RowMove } from '@/features/shared/order'
import ModelPickerDialog from '@/features/shared/ModelPickerDialog.vue'
import ModelTestStatus from './ModelTestResult.vue'
import {
  UiAlert,
  UiButton,
  UiCheckbox,
  UiConfirmPopover,
  UiField,
  UiIconButton,
  UiInput,
  UiSelect,
  UiTable,
  type TableColumn,
} from '@/components/ui'
const props = defineProps<{ provider: Provider; groups: AuthGroup[]; disabled?: boolean }>()
const emit = defineEmits<{ change: [models: string[], hidden: string[]] }>()
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
    visible: !props.provider.hidden_model_list.includes(model),
    testing: false,
  })),
)
const selected = ref<string[]>([])
const authEntry = ref('')
const fetching = ref(false)
const testing = ref(false)
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
  testing.value = false
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
  { key: 'model', label: '模型 ID', width: 360 },
  { key: 'visible', label: '显示', width: 80 },
  { key: 'result', label: '测试结果 · 首 Token / 吞吐量', width: 310 },
  { key: 'actions', label: '操作', width: 115 },
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
  add(models)
  fetched.value = undefined
}
async function test(ids: string[]) {
  if (testing.value || !ready()) return
  const targets = rows.value.filter((row) => ids.includes(row.id) && row.model.trim())
  if (!targets.length) {
    error.value = '请选择至少一个模型'
    return
  }
  const current = ++batch
  const snapshot = fingerprint.value
  const payload = JSON.parse(JSON.stringify(props.provider)) as Provider
  const entry = authEntry.value
  testing.value = true
  for (const row of targets) {
    if (batch !== current) break
    if (!rows.value.includes(row)) continue
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
  if (current === batch) testing.value = false
}
</script>
<template>
  <div class="stack">
    <div class="toolbar">
      <div class="actions">
        <h2>模型</h2>
        <small class="muted">{{ rows.length }} 项 · 已选 {{ selected.length }}</small>
      </div>
      <div class="actions">
        <UiButton size="sm" icon="plus" :disabled="disabled" @click="add()">添加</UiButton>
        <UiButton size="sm" :disabled="disabled || testing" @click="tidy">整理</UiButton>
        <UiButton size="sm" :disabled="disabled || !rows.length" @click="visibilityAll">
          {{ allVisible ? '全部隐藏' : '全部显示' }}
        </UiButton>
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
    <div class="toolbar">
      <div class="actions">
        <UiButton
          size="sm"
          :loading="testing"
          :disabled="disabled || !selected.length"
          @click="test(selected)"
        >
          测试所选
        </UiButton>
        <UiConfirmPopover
          :message="`移除所选 ${selected.length} 个模型？`"
          @confirm="remove(selected)"
        >
          <UiButton size="sm" :disabled="disabled || !selected.length">移除所选</UiButton>
        </UiConfirmPopover>
        <UiButton v-if="testing" size="sm" @click="cancel">停止测试</UiButton>
      </div>
      <UiField v-if="group" label="测试 Auth Entry" class="auth-entry-select">
        <UiSelect
          v-model="authEntry"
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
    </div>
    <UiAlert v-if="error">{{ error }}</UiAlert>
    <UiTable
      v-model:selected="selected"
      :rows="rows"
      :columns="columns"
      :row-key="(row) => row.id"
      selectable
      :reorderable="!disabled"
      empty="添加或拉取模型"
      @reorder="reorder"
    >
      <template #cell-model="{ row }">
        <UiInput
          :model-value="row.model"
          mono
          :aria-label="`模型 ${row.id}`"
          :disabled="disabled"
          @update:model-value="change(row, $event)"
        />
      </template>
      <template #cell-visible="{ row }">
        <UiCheckbox
          :model-value="row.visible"
          :label="`${row.model || '模型'} 在目录中显示`"
          :disabled="disabled"
          @update:model-value="visibility(row, $event)"
        />
      </template>
      <template #cell-result="{ row }">
        <ModelTestStatus :result="row.result" :loading="row.testing" />
      </template>
      <template #cell-actions="{ row }">
        <div class="actions">
          <UiButton
            size="sm"
            :disabled="disabled || testing || !row.model.trim()"
            @click="test([row.id])"
          >
            测试
          </UiButton>
          <UiIconButton
            icon="delete"
            label="移除模型"
            :disabled="disabled"
            @click="remove([row.id])"
          />
        </div>
      </template>
    </UiTable>
    <ModelPickerDialog
      v-if="fetched"
      title="添加上游模型"
      :models="fetched"
      :initial="fetched.filter((model) => provider.model_list.includes(model))"
      @close="fetched = undefined"
      @apply="applyFetched"
    />
  </div>
</template>
<style scoped>
.auth-entry-select {
  width: min(280px, 100%);
}
</style>
