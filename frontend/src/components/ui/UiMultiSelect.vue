<script setup lang="ts">
import { computed, nextTick, provide, ref, watch } from 'vue'
import {
  CheckboxIndicator,
  CheckboxRoot,
  PopoverAnchor,
  PopoverContent,
  PopoverPortal,
  PopoverRoot,
  PopoverTrigger,
} from 'reka-ui'
import { fieldKey, useField } from './field'
import type { SelectOption } from './types'
import UiInput from './UiInput.vue'
import UiIcon from './UiIcon.vue'
import UiCheckbox from './UiCheckbox.vue'
import UiButton from './UiButton.vue'
import UiBadge from './UiBadge.vue'
defineOptions({ inheritAttrs: false })
const props = withDefaults(
  defineProps<{
    modelValue: string[]
    options: SelectOption[]
    placeholder?: string
    disabled?: boolean
  }>(),
  { placeholder: '全部' },
)
const emit = defineEmits<{ 'update:modelValue': [value: string[]]; commit: [] }>()
const field = useField()
// 浮层搜索和勾选项各自拥有标签，不继承触发器的字段 ID。
provide(fieldKey, undefined)
const open = ref(false)
let openedValues: string[] = []
const keyword = ref('')
const valuesElement = ref<HTMLElement>()
watch(
  () => props.modelValue.length,
  async (length, previous) => {
    if (length <= previous) return
    await nextTick()
    if (valuesElement.value) valuesElement.value.scrollLeft = valuesElement.value.scrollWidth
  },
)
watch(open, (value) => {
  keyword.value = ''
  if (value) openedValues = [...props.modelValue]
  else if (
    props.modelValue.length !== openedValues.length ||
    props.modelValue.some((item) => !openedValues.includes(item))
  )
    emit('commit')
})
const candidates = computed<SelectOption[]>(() => {
  const known = new Set(props.options.map((option) => option.value))
  return [
    ...props.options,
    ...props.modelValue
      .filter((value) => !known.has(value))
      .map((value) => ({ value, label: value })),
  ]
})
const filtered = computed(() =>
  candidates.value.filter((option) =>
    `${option.label} ${option.description || ''}`
      .toLowerCase()
      .includes(keyword.value.trim().toLowerCase()),
  ),
)
const selectable = computed(() => filtered.value.filter((option) => !option.disabled))
const selectedCount = computed(
  () => selectable.value.filter((option) => props.modelValue.includes(option.value)).length,
)
const allSelected = computed(
  () => selectable.value.length > 0 && selectedCount.value === selectable.value.length,
)
const selected = computed(() =>
  props.modelValue.map((value) => candidates.value.find((option) => option.value === value)!),
)
function toggle(value: string, checked: boolean) {
  updateSelection(
    checked
      ? [...new Set([...props.modelValue, value])]
      : props.modelValue.filter((item) => item !== value),
  )
}
function toggleVisible(checked: boolean) {
  const values = new Set(selectable.value.map((option) => option.value))
  updateSelection(
    checked
      ? [...new Set([...props.modelValue, ...values])]
      : props.modelValue.filter((value) => !values.has(value)),
  )
}
function updateSelection(values: string[]) {
  emit('update:modelValue', values)
  if (!open.value) emit('commit')
}
function remove(value: string) {
  emit(
    'update:modelValue',
    props.modelValue.filter((item) => item !== value),
  )
  emit('commit')
}
</script>
<template>
  <PopoverRoot v-model:open="open">
    <PopoverAnchor as-child>
      <div class="ui-multi-select" :class="{ 'ui-multi-select--disabled': disabled }">
        <div v-if="selected.length" ref="valuesElement" class="ui-multi-values">
          <UiBadge v-for="option in selected" :key="option.value" class="ui-multi-chip">
            <span class="truncate" :title="option.label">{{ option.label }}</span>
            <button
              type="button"
              class="ui-multi-remove"
              :aria-label="`移除 ${option.label}`"
              :disabled="disabled"
              @click.stop="remove(option.value)"
            >
              <UiIcon name="close" :size="12" />
            </button>
          </UiBadge>
        </div>
        <PopoverTrigger as-child>
          <button
            :id="field?.id"
            type="button"
            class="ui-multi-trigger"
            :disabled="disabled"
            :aria-labelledby="field?.id ? `${field.id}-label` : undefined"
            :aria-invalid="!!field?.error.value || undefined"
            :aria-required="field?.required?.value || undefined"
            :aria-describedby="field?.describedBy.value"
            v-bind="$attrs"
          >
            <span v-if="!selected.length" class="truncate muted">
              {{ placeholder }}
            </span>
            <UiIcon name="down" :size="14" />
          </button>
        </PopoverTrigger>
      </div>
    </PopoverAnchor>
    <PopoverPortal>
      <PopoverContent
        class="ui-multi-content"
        :side-offset="6"
        :collision-padding="12"
        align="start"
      >
        <UiInput v-model="keyword" placeholder="搜索" aria-label="搜索选项" />
        <div class="ui-multi-actions">
          <UiCheckbox
            :model-value="allSelected ? true : selectedCount ? 'indeterminate' : false"
            :disabled="!selectable.length"
            @update:model-value="toggleVisible"
          >
            选择当前结果
          </UiCheckbox>
          <UiButton
            size="sm"
            variant="ghost"
            :disabled="!modelValue.length"
            @click="updateSelection([])"
          >
            清空
          </UiButton>
        </div>
        <div class="ui-multi-options" role="group" aria-label="筛选选项">
          <CheckboxRoot
            v-for="option in filtered"
            :key="option.value"
            class="ui-multi-option"
            :model-value="modelValue.includes(option.value)"
            :disabled="option.disabled"
            @update:model-value="toggle(option.value, $event === true)"
          >
            <span class="ui-multi-mark">
              <CheckboxIndicator><UiIcon name="check" :size="13" /></CheckboxIndicator>
            </span>
            <span>
              {{ option.label }}
              <small v-if="option.description" class="muted">{{ option.description }}</small>
            </span>
          </CheckboxRoot>
          <p v-if="!filtered.length" class="muted">没有匹配项</p>
        </div>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
<style scoped>
.ui-multi-select {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-1);
  width: 100%;
  min-width: 0;
  height: var(--control);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--input);
  font-size: var(--text-base);
  text-align: left;
}

.ui-multi-select:focus-within {
  border-color: var(--link);
  box-shadow: var(--focus);
  outline: none;
}

.ui-multi-select--disabled {
  opacity: 0.5;
}

.ui-multi-trigger {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-width: 30px;
  height: 100%;
  padding: 0 var(--space-3);
  border: 0;
  border-radius: inherit;
  background: transparent;
  color: var(--text);
  text-align: left;
  font: inherit;
}

.ui-multi-trigger :deep(svg) {
  flex: none;
  margin-left: auto;
}

.ui-multi-trigger:focus-visible {
  outline: none;
}

.ui-multi-values {
  display: flex;
  align-items: center;
  flex: 0 1 auto;
  max-width: calc(100% - 40px);
  gap: var(--space-1);
  margin-left: 5px;
  padding: 3px 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.ui-multi-chip {
  flex: 0 0 auto;
  max-width: min(220px, 100%);
  gap: 2px;
  height: 24px;
  padding: 0 4px 0 8px;
  border-radius: 6px;
  background: var(--surface-soft);
  color: var(--text);
  font-size: var(--text-sm);
}

.ui-multi-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 18px;
  width: 18px;
  height: 20px;
  padding: 2px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
}

.ui-multi-remove:hover:not(:disabled) {
  background: var(--hover);
  color: var(--text);
}

.ui-multi-remove:focus-visible {
  outline-offset: -1px;
}

:global(.ui-multi-content) {
  z-index: var(--z-popup);
  display: grid;
  gap: var(--space-2);
  width: max(280px, var(--reka-popover-trigger-width));
  max-width: calc(100vw - 24px);
  max-height: var(--reka-popover-content-available-height);
  padding: var(--space-2);
  border-radius: var(--radius-control);
  background: var(--overlay);
  color: var(--text);
  box-shadow: var(--shadow-overlay);
  overflow: auto;
}

.ui-multi-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.ui-multi-options {
  max-height: 260px;
  overflow: auto;
}

.ui-multi-option {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  min-height: var(--control);
  padding: var(--space-2);
  border: 0;
  border-radius: var(--space-1);
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  overflow-wrap: anywhere;
}

.ui-multi-option:hover:not(:disabled) {
  background: var(--hover);
}

.ui-multi-option:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ui-multi-option:focus-visible {
  outline-offset: -2px;
}

.ui-multi-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 17px;
  height: 17px;
  border: 1px solid var(--border);
  border-radius: var(--space-1);
  background: var(--input);
}

.ui-multi-option[data-state='checked'] .ui-multi-mark {
  background: var(--primary);
  border-color: var(--primary);
  color: var(--on-primary);
}

.ui-multi-option small {
  display: block;
}

@media (pointer: coarse) {
  .ui-multi-chip {
    height: 32px;
  }

  .ui-multi-remove {
    flex-basis: 28px;
    width: 28px;
    height: 28px;
  }
}
</style>
