<script setup lang="ts">
import { useDraftChanges } from '@/features/shared/useDraftChanges'
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { mappingsApi, type ModelMapping, type MappingTarget } from '@/api/model-mappings'
import { errorMessage } from '@/api/client'
import { useLifetime } from '@/features/shared/useLifetime'
import TargetTest from './TargetTest.vue'
import type { ModelTestResult } from '@/api/providers'
import { modelTpsHelp, modelTtftHelp } from '@/features/providers/modelTestHelp'
import DesensitizationHelp from '@/features/shared/DesensitizationHelp.vue'
import TextImportDialog from '@/features/shared/TextImportDialog.vue'
import { moveItem, type RowMove } from '@/features/shared/order'
import {
  UiAlert,
  UiButton,
  UiDialog,
  UiField,
  UiIconButton,
  UiInput,
  UiNumberInput,
  UiSelect,
  UiCombobox,
  UiSwitch,
  UiBadge,
  UiHelpPopover,
  UiTable,
  UiListToolbar,
  UiSelectionBar,
  UiConfirmPopover,
  type TableColumn,
} from '@/components/ui'
const props = defineProps<{ mapping?: ModelMapping; models: string[] }>()
const emit = defineEmits<{ close: []; saved: [] }>()
let sequence = 0
const draft = reactive({
  id: props.mapping?.id || '',
  enabled: props.mapping?.enabled ?? true,
  strategy: props.mapping?.strategy || 'highest_priority',
  cooldown_seconds_on_429: props.mapping?.cooldown_seconds_on_429 ?? 60,
  safe_desensitization_enabled: props.mapping?.safe_desensitization_enabled || false,
})
const targets = ref(
  (props.mapping?.targets || [{ model_id: '', priority: 1, enabled: true }]).map((target) => ({
    ...target,
    key: ++sequence,
  })),
)
const errors = ref<Record<string, string>>({})
const validity = reactive<Record<string, boolean>>({})
const error = ref('')
const busy = ref(false)
const selected = ref<string[]>([])
const testers = new Map<number, InstanceType<typeof TargetTest>>()
const testing = ref(new Set<number>())
const testResults = reactive(new Map<number, ModelTestResult>())
const bulkTesting = ref(false)
let testBatch = 0
function stopTests() {
  testBatch++
  bulkTesting.value = false
  for (const key of [...testing.value]) testers.get(key)?.cancel()
}
onBeforeUnmount(stopTests)
async function testSelected() {
  if (busy.value || bulkTesting.value) return
  const batch = ++testBatch
  const keys = targets.value
    .filter((target) => selected.value.includes(String(target.key)) && !unavailable(target))
    .map((target) => target.key)
  bulkTesting.value = true
  try {
    for (const key of keys) {
      if (batch !== testBatch) break
      await testers.get(key)?.test()
    }
  } finally {
    if (batch === testBatch) bulkTesting.value = false
  }
}
const editingModels = ref(false)
const modelSource = ref('')
const columns: TableColumn[] = [
  { key: 'model', label: '目标模型 ID', width: 320 },
  {
    key: 'priority',
    label: '优先级',
    width: 110,
    fixed: true,
    help: '数字越大越先选择；相同优先级按列表顺序选择。粘滞策略优先继续使用当前可用目标。',
  },
  { key: 'enabled', label: '启用', width: 80, fixed: true },
  { key: 'result', label: '结果', width: 80, fixed: true },
  { key: 'latency', label: '首字延迟', width: 130, fixed: true, help: modelTtftHelp },
  { key: 'tps', label: 'TPS', width: 110, fixed: true, help: modelTpsHelp },
  { key: 'actions', label: '操作', width: 155 },
]
function editModels() {
  modelSource.value = targets.value.map((target) => target.model_id).join('\n')
  editingModels.value = true
}
async function applyModelText(text: string) {
  const models = [
    ...new Set(
      text
        .split(/\r?\n/)
        .map((model) => model.trim())
        .filter(Boolean),
    ),
  ]
  if (!models.length) throw new Error('至少填写一个目标模型 ID')
  const existing = new Map(targets.value.map((target) => [target.model_id.trim(), target]))
  targets.value = models.map((model_id) => {
    const target = existing.get(model_id)
    return target
      ? { ...target, model_id }
      : { key: ++sequence, model_id, priority: 1, enabled: true }
  })
  selected.value = []
  errors.value = {}
}
function removeSelected() {
  for (const key of [...selected.value]) remove(Number(key))
}
const signal = useLifetime()
const options = computed(() => props.models.map((value) => ({ value, label: value })))
function unavailable(target: MappingTarget) {
  return !!target.model_id && !props.models.includes(target.model_id)
}
function add() {
  targets.value.push({ key: ++sequence, model_id: '', priority: 1, enabled: true })
}
function reorder(move: RowMove) {
  targets.value = moveItem(targets.value, move, (target) => String(target.key))
}
function remove(key: number) {
  testResults.delete(key)
  selected.value = selected.value.filter((item) => item !== String(key))
  targets.value = targets.value.filter((target) => target.key !== key)
  delete validity[String(key)]
}
async function submit() {
  errors.value = {}
  const id = draft.id.trim()
  if (!/^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/.test(id))
    errors.value.id = '以字母或下划线开头，最多 64 位，可含数字、点和连字符'
  if (
    !Number.isInteger(draft.cooldown_seconds_on_429) ||
    draft.cooldown_seconds_on_429 < 0 ||
    validity.cooldown === false
  )
    errors.value.cooldown = '请输入非负整数'
  const filledTargets = targets.value.filter((target) => target.model_id.trim())
  if (!filledTargets.length) errors.value.targets = '至少添加一个目标模型'
  const used = new Set<string>()
  for (const target of filledTargets) {
    if (used.has(target.model_id)) errors.value[String(target.key)] = '目标模型重复'
    else if (
      !Number.isInteger(target.priority) ||
      target.priority < 0 ||
      validity[String(target.key)] === false
    )
      errors.value[String(target.key)] = '优先级必须是非负整数'
    used.add(target.model_id)
  }
  if (busy.value || Object.keys(errors.value).length) return
  busy.value = true
  error.value = ''
  try {
    await mappingsApi.save(
      {
        ...draft,
        id,
        targets: filledTargets.map(({ model_id, priority, enabled }) => ({
          model_id,
          priority,
          enabled,
        })),
      },
      props.mapping?.id,
      signal,
    )
    if (signal.aborted) return
    emit('saved')
    emit('close')
  } catch (reason) {
    if (!signal.aborted) error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
const dirty = useDraftChanges(() => ({
  draft,
  targets: targets.value.map(({ model_id, priority, enabled }) => ({
    model_id,
    priority,
    enabled,
  })),
}))
</script>
<template>
  <UiDialog
    :save="submit"
    :dirty="dirty"
    open
    :title="mapping ? '编辑模型映射' : '创建模型映射'"
    size="xl"
    :busy="busy"
    @update:open="emit('close')"
  >
    <form id="mapping-form" class="stack" @submit.prevent="submit">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <div class="form-grid mapping-fields">
        <UiField
          label="对外模型 ID"
          required
          :error="errors.id"
          help="客户端请求时填写的固定模型名，无需另加 Provider 前缀。启用映射后出现在模型目录；同名 OAuth 模型会优先按此映射路由。"
        >
          <UiInput v-model="draft.id" mono autofocus :disabled="busy" />
        </UiField>
        <UiField
          label="选择策略"
          :help="[
            '最高优先级：每次请求重新选择正常目标中优先级最高的一项，高优先级目标恢复后自动切回。',
            '粘滞故障切换：继续使用最近成功的目标；进入冷却、禁用或不可用时，才选择其他目标。',
            '无正常候选时仍尝试运行时存在的最高优先级目标兜底，包括被手动禁用、自动禁用或冷却的目标。彻底停用请关闭整个映射。',
          ]"
        >
          <UiSelect
            v-model="draft.strategy"
            :options="[
              { value: 'highest_priority', label: '最高优先级' },
              { value: 'sticky_failover', label: '粘滞故障切换' },
            ]"
            :disabled="busy"
          />
        </UiField>
        <UiField
          label="故障冷却"
          unit="秒"
          :help="[
            '网络异常、401/403/408/425/429 和 5xx 进入临时冷却。有效 Retry-After 优先，否则使用此处秒数；填 0 不增加等待时间。',
            '重定向、404/405/410 会自动禁用目标；其他 4xx 通常只切换当前请求，不禁用目标。',
            '冷却到期会重新尝试。Codex 额度故障在查询确认相关窗口均有剩余后也可提前恢复；长期禁用可在排除故障后重新启用。',
          ]"
          :error="errors.cooldown"
        >
          <UiNumberInput
            v-model="draft.cooldown_seconds_on_429"
            :min="0"
            :step="1"
            :disabled="busy"
            @validity-change="validity.cooldown = $event"
          />
        </UiField>
        <UiField inline label="安全脱敏" class="mapping-safety">
          <template #help><DesensitizationHelp scope="mapping" /></template>
          <UiSwitch
            v-model="draft.safe_desensitization_enabled"
            label="安全脱敏"
            :disabled="busy"
          />
        </UiField>
      </div>
      <UiListToolbar :selecting="selected.length > 0">
        <template #actions>
          <h2>目标模型</h2>
          <UiHelpPopover label="优先级说明">
            <p>
              数字越大越先选择；相同优先级按列表顺序选择，拖动可调整顺序。粘滞策略有可用的当前目标时继续使用该目标。
            </p>
            <p>
              所有目标均禁用或冷却时，仍以运行时存在的最高优先级目标兜底；目录中已不存在的目标无法调用。
            </p>
          </UiHelpPopover>
          <UiButton
            size="sm"
            :disabled="busy || bulkTesting || testing.size > 0"
            @click="editModels"
          >
            编辑
          </UiButton>
        </template>
        <template #selection>
          <UiSelectionBar
            v-if="selected.length"
            embedded
            :count="selected.length"
            :disabled="busy"
            @clear="selected = []"
          >
            <UiButton
              :loading="bulkTesting"
              :disabled="
                busy ||
                !targets.some(
                  (target) =>
                    selected.includes(String(target.key)) &&
                    target.model_id &&
                    !unavailable(target),
                )
              "
              @click="testSelected"
            >
              测试
            </UiButton>
            <UiConfirmPopover
              :message="`删除所选 ${selected.length} 个目标模型？`"
              @confirm="removeSelected"
            >
              <UiButton
                variant="danger-outline"
                :disabled="busy || selected.some((key) => testing.has(Number(key)))"
              >
                删除
              </UiButton>
            </UiConfirmPopover>
          </UiSelectionBar>
        </template>
        <UiButton v-if="bulkTesting" size="sm" @click="stopTests">停止测试</UiButton>
      </UiListToolbar>
      <UiAlert v-if="errors.targets">{{ errors.targets }}</UiAlert>
      <UiTable
        v-model:selected="selected"
        :rows="targets"
        :columns="columns"
        :row-key="(target) => String(target.key)"
        selectable
        reorderable
        :reorder-disabled="busy"
        add-label="添加目标"
        :add-disabled="busy"
        @add="add"
        @reorder="reorder"
      >
        <template #header-actions><span class="target-actions-heading">操作</span></template>
        <template #cell-model="{ row: target }">
          <UiField
            :label="`目标 ${targets.indexOf(target) + 1}`"
            label-hidden
            :error="errors[String(target.key)]"
          >
            <UiCombobox
              v-model="target.model_id"
              :options="
                options.map((option) => ({
                  ...option,
                  disabled: targets.some(
                    (other) => other.key !== target.key && other.model_id === option.value,
                  ),
                }))
              "
              :disabled="busy || testing.has(target.key)"
              placeholder="搜索目标模型"
            />
          </UiField>
          <div v-if="unavailable(target)" class="actions target-unavailable">
            <UiBadge variant="warning">当前不可用</UiBadge>
            <UiHelpPopover label="目标不可用说明">
              该 ID 已不在运行时模型目录中，可能对应的 Provider
              被禁用或模型已移除。保留此行不会恢复路由，请恢复上游配置或选择其他目标。
            </UiHelpPopover>
          </div>
        </template>
        <template #cell-priority="{ row: target }">
          <UiNumberInput
            v-model="target.priority"
            :min="0"
            :step="1"
            :disabled="busy || unavailable(target)"
            :aria-label="`目标 ${targets.indexOf(target) + 1} 优先级`"
            @validity-change="validity[String(target.key)] = $event"
          />
        </template>
        <template #cell-enabled="{ row: target }">
          <UiSwitch
            v-model="target.enabled"
            :label="`启用目标 ${targets.indexOf(target) + 1}`"
            :disabled="busy || unavailable(target)"
          />
        </template>
        <template #cell-result="{ row: target }">
          <UiBadge v-if="testing.has(target.key)">测试中</UiBadge>
          <UiBadge
            v-else-if="testResults.has(target.key)"
            :variant="testResults.get(target.key)?.available ? 'success' : 'danger'"
          >
            {{ testResults.get(target.key)?.available ? '可用' : '失败' }}
          </UiBadge>
          <span v-else class="muted">—</span>
        </template>
        <template #cell-latency="{ row: target }">
          <span class="number">
            {{
              testing.has(target.key) || testResults.get(target.key)?.first_token_latency_ms == null
                ? '—'
                : `${testResults.get(target.key)?.first_token_latency_ms} ms`
            }}
          </span>
        </template>
        <template #cell-tps="{ row: target }">
          <span class="number">
            {{
              testing.has(target.key) || testResults.get(target.key)?.tps == null
                ? '—'
                : `${testResults.get(target.key)?.tps} tok/s`
            }}
          </span>
        </template>
        <template #cell-actions="{ row: target }">
          <div class="target-actions">
            <TargetTest
              :ref="
                (instance) => {
                  if (instance) testers.set(target.key, instance as InstanceType<typeof TargetTest>)
                  else testers.delete(target.key)
                }
              "
              :model="target.model_id"
              :disabled="busy || unavailable(target)"
              @loading="$event ? testing.add(target.key) : testing.delete(target.key)"
              @result="
                $event ? testResults.set(target.key, $event) : testResults.delete(target.key)
              "
            />
            <UiIconButton
              icon="delete"
              label="移除目标"
              :disabled="busy || testing.has(target.key)"
              @click="remove(target.key)"
            />
          </div>
        </template>
      </UiTable>
    </form>
    <template #footer="{ close }">
      <UiButton :disabled="busy" @click="close()">取消</UiButton>
      <UiButton variant="primary" type="submit" form="mapping-form" :loading="busy">保存</UiButton>
    </template>
  </UiDialog>
  <TextImportDialog
    v-if="editingModels"
    v-model:open="editingModels"
    title="编辑目标模型"
    language="text"
    :allow-file="false"
    :initial-value="modelSource"
    confirm-label="确定"
    :submit="applyModelText"
    :help="[
      '每行一个完整目标模型 ID，按输入顺序替换列表，自动去除空行及重复项。',
      '已有目标保留优先级和启用状态；新目标默认启用、优先级为 1。相同优先级按列表顺序选择。',
      '目录中不存在的 ID 会标记为当前不可用。确定后更新草稿，保存映射后生效。',
    ]"
  />
</template>
<style scoped>
.mapping-fields {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: end;
}

.mapping-safety {
  min-height: var(--control);
  padding-block: var(--space-2);
}

.target-actions-heading {
  display: block;
  width: 100%;
  text-align: center;
}

.target-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
}

.target-unavailable {
  margin-top: var(--space-2);
}

@media (width <= 900px) {
  .mapping-fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (width <= 600px) {
  .mapping-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
