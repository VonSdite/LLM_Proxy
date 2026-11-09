<script setup lang="ts">
import { inject, onBeforeUnmount, ref, watch } from 'vue'
import { PopoverAnchor, PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import UiInput from './UiInput.vue'
import UiIcon from './UiIcon.vue'
import { dialogPendingEdits } from './dialogEdits'
defineOptions({ inheritAttrs: false })
const props = defineProps<{
  modelValue: number | null | undefined
  min?: number
  max?: number
  step?: number
  placeholder?: string
  disabled?: boolean
  suggestions?: { value: number; label: string; description?: string }[]
}>()
const emit = defineEmits<{
  'update:modelValue': [value: number | null]
  commit: [value: number | null]
  'validity-change': [valid: boolean]
}>()
const draft = ref(props.modelValue == null ? '' : String(props.modelValue))
const pendingEdits = inject(dialogPendingEdits, undefined)
const hasPendingEdit = () => {
  const value = draft.value.trim() === '' ? null : Number(draft.value)
  return invalid.value || value !== (props.modelValue ?? null)
}
pendingEdits?.add(hasPendingEdit)
onBeforeUnmount(() => pendingEdits?.delete(hasPendingEdit))
const invalid = ref(false)
const focused = ref(false)
const open = ref(false)
const input = ref<InstanceType<typeof UiInput>>()
watch(
  () => props.modelValue,
  (value) => {
    if (!focused.value) {
      draft.value = value == null ? '' : String(value)
      invalid.value = false
      input.value?.input?.setCustomValidity('')
    }
  },
)
function commit() {
  focused.value = false
  const value = draft.value.trim() === '' ? null : Number(draft.value)
  invalid.value =
    value !== null &&
    (!Number.isFinite(value) ||
      (props.min !== undefined && value < props.min) ||
      (props.max !== undefined && value > props.max) ||
      (props.step === 1 && !Number.isInteger(value)))
  emit('validity-change', !invalid.value)
  input.value?.input?.setCustomValidity(invalid.value ? '请输入有效范围内的数值' : '')
  if (invalid.value) return
  if (value !== (props.modelValue ?? null)) {
    emit('update:modelValue', value)
    emit('commit', value)
  }
}
function choose(value: number) {
  draft.value = String(value)
  commit()
  open.value = false
}
</script>
<template>
  <PopoverRoot v-model:open="open">
    <PopoverAnchor as-child>
      <div class="ui-number" :class="{ 'ui-number--suggestions': suggestions }">
        <UiInput
          ref="input"
          v-model="draft"
          v-bind="$attrs"
          type="text"
          inputmode="decimal"
          :placeholder="placeholder"
          :disabled="disabled"
          :invalid="invalid"
          class="number"
          @focus="focused = true"
          @blur="commit"
          @keydown.enter="commit"
        />
        <PopoverTrigger
          v-if="suggestions"
          class="ui-number-trigger"
          :disabled="disabled"
          aria-label="参数参考值"
        >
          <UiIcon name="down" :size="14" />
        </PopoverTrigger>
      </div>
    </PopoverAnchor>
    <PopoverPortal>
      <PopoverContent
        class="ui-number-options"
        :side-offset="6"
        :collision-padding="12"
        @open-auto-focus.prevent
      >
        <p v-if="!suggestions?.length" class="ui-number-empty">先选择模型</p>
        <button
          v-for="(item, index) in suggestions"
          :key="`${item.value}:${index}`"
          type="button"
          class="ui-number-option"
          :aria-pressed="item.value === modelValue"
          @click="choose(item.value)"
        >
          <strong class="number">{{ item.label }}</strong>
          <small class="muted">{{ item.description }}</small>
        </button>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
<style scoped>
.ui-number {
  position: relative;
  min-width: 0;
}

.ui-number--suggestions :deep(input) {
  padding-right: var(--control-sm);
}

.ui-number-trigger {
  position: absolute;
  inset: 1px 1px 1px auto;
  width: var(--control-sm);
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--muted);
  display: grid;
  place-items: center;
}

:global(.ui-number-options) {
  z-index: var(--z-popup);
  width: max(240px, var(--reka-popover-trigger-width));
  max-width: calc(100vw - 24px);
  max-height: min(320px, var(--reka-popover-content-available-height));
  overflow: auto;
  padding: var(--space-1);
  border-radius: var(--radius-control);
  background: var(--overlay);
  box-shadow: var(--shadow-overlay);
}

.ui-number-option {
  display: grid;
  gap: 2px;
  width: 100%;
  padding: var(--space-2);
  background: transparent;
  border: 0;
  border-radius: var(--space-1);
  text-align: left;
  overflow-wrap: anywhere;
}

.ui-number-option:hover,
.ui-number-option:focus-visible {
  background: var(--hover);
}

.ui-number-option[aria-pressed='true'] {
  color: var(--link);
}

.ui-number-option strong {
  font-size: var(--text-base);
  font-weight: var(--weight-option);
}

.ui-number-empty {
  padding: var(--space-3);
  color: var(--muted);
  font-size: var(--text-xs);
}
</style>
