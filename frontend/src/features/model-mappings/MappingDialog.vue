<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { mappingsApi, type ModelMapping, type MappingTarget } from '@/api/model-mappings'
import { errorMessage } from '@/api/client'
import { useLifetime } from '@/features/shared/useLifetime'
import TargetTest from './TargetTest.vue'
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
  UiSortableList,
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
</script>
<template>
  <UiDialog
    open
    :title="mapping ? '编辑模型映射' : '创建模型映射'"
    size="lg"
    :busy="busy"
    @update:open="emit('close')"
  >
    <form id="mapping-form" class="stack" @submit.prevent="submit">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <div class="form-grid">
        <UiField label="对外模型 ID" required :error="errors.id">
          <UiInput v-model="draft.id" mono autofocus :disabled="busy" />
        </UiField>
        <UiField
          label="选择策略"
          help="最高优先级：优先使用数值最高的正常目标，恢复后自动切回。粘滞故障切换：持续使用最近成功的目标，直到它不可用。"
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
          help="临时网络和上游错误进入冷却；429 优先采用上游 Retry-After。长期不可用的目标保持禁用。"
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
        <div class="actions">
          <UiSwitch v-model="draft.safe_desensitization_enabled" :disabled="busy">
            安全脱敏
          </UiSwitch>
          <UiHelpPopover label="安全脱敏说明">
            请求中的常见敏感信息替换为占位符，响应返回时恢复。规则可能误判或漏报；映射级开关会对所有目标生效。
          </UiHelpPopover>
        </div>
      </div>
      <div class="section-heading">
        <div class="actions">
          <h2>目标模型</h2>
          <UiHelpPopover label="优先级说明">
            数字越大越先选择；相同优先级按列表顺序选择。无正常候选时仍尝试运行时存在的最高优先级目标。
          </UiHelpPopover>
        </div>
        <UiButton icon="plus" size="sm" :disabled="busy" @click="add">添加目标</UiButton>
      </div>
      <UiAlert v-if="errors.targets">{{ errors.targets }}</UiAlert>
      <UiSortableList
        :items="targets"
        :row-key="(target) => String(target.key)"
        :disabled="busy"
        label="目标模型顺序"
        @reorder="reorder"
      >
        <template #default="{ item: target, index }">
          <div class="mapping-target">
            <div class="mapping-target-fields">
              <UiField :label="`目标 ${index + 1}`" :error="errors[String(target.key)]">
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
                  :disabled="busy"
                  placeholder="搜索目标模型"
                />
              </UiField>
              <UiField label="优先级">
                <UiNumberInput
                  v-model="target.priority"
                  :min="0"
                  :step="1"
                  :disabled="busy || unavailable(target)"
                  @validity-change="validity[String(target.key)] = $event"
                />
              </UiField>
              <div class="actions">
                <UiSwitch
                  v-model="target.enabled"
                  :label="`启用目标 ${index + 1}`"
                  :disabled="busy || unavailable(target)"
                />
                <UiIconButton
                  icon="delete"
                  label="移除目标"
                  :disabled="busy"
                  @click="remove(target.key)"
                />
              </div>
            </div>
            <UiBadge v-if="unavailable(target)" variant="warning">当前不可用</UiBadge>
            <TargetTest :model="target.model_id" :disabled="busy || unavailable(target)" />
          </div>
        </template>
      </UiSortableList>
    </form>
    <template #footer>
      <UiButton :disabled="busy" @click="emit('close')">取消</UiButton>
      <UiButton variant="primary" type="submit" form="mapping-form" :loading="busy">保存</UiButton>
    </template>
  </UiDialog>
</template>
<style scoped>
.mapping-target {
  display: grid;
  gap: var(--space-2);
}

.mapping-target-fields {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 100px auto;
  align-items: end;
  gap: var(--space-3);
}

.mapping-target-fields > .actions {
  min-height: var(--control);
}

@media (width <= 600px) {
  .mapping-target-fields {
    grid-template-columns: minmax(0, 1fr) 100px;
  }

  .mapping-target-fields > .actions {
    grid-column: 1 / -1;
    justify-content: flex-end;
  }
}
</style>
