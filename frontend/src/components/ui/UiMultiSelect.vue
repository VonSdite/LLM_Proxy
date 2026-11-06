<script setup lang="ts">
import { computed, provide, ref, watch } from 'vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { fieldKey, useField } from './field'
import type { SelectOption } from './types'
import UiInput from './UiInput.vue'
import UiIcon from './UiIcon.vue'
import UiCheckbox from './UiCheckbox.vue'
import UiButton from './UiButton.vue'
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
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()
const field = useField()
// 浮层搜索和勾选项各自拥有标签，不继承触发器的字段 ID。
provide(fieldKey, undefined)
const open = ref(false)
const keyword = ref('')
watch(open, () => {
  keyword.value = ''
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
const label = computed(() =>
  props.modelValue.length === 1
    ? candidates.value.find((option) => option.value === props.modelValue[0])?.label
    : `已选 ${props.modelValue.length} 项`,
)
function toggle(value: string, checked: boolean) {
  emit(
    'update:modelValue',
    checked
      ? [...new Set([...props.modelValue, value])]
      : props.modelValue.filter((item) => item !== value),
  )
}
function toggleVisible(checked: boolean) {
  const values = new Set(selectable.value.map((option) => option.value))
  emit(
    'update:modelValue',
    checked
      ? [...new Set([...props.modelValue, ...values])]
      : props.modelValue.filter((value) => !values.has(value)),
  )
}
</script>
<template>
  <PopoverRoot v-model:open="open">
    <PopoverTrigger as-child>
      <button
        :id="field?.id"
        type="button"
        class="ui-multi-select"
        :disabled="disabled"
        :aria-labelledby="field?.id ? `${field.id}-label` : undefined"
        :aria-invalid="!!field?.error.value || undefined"
        :aria-required="field?.required?.value || undefined"
        :aria-describedby="field?.describedBy.value"
        v-bind="$attrs"
      >
        <span class="truncate" :class="{ muted: !modelValue.length }">
          {{ modelValue.length ? label : placeholder }}
        </span>
        <UiIcon name="down" :size="14" />
      </button>
    </PopoverTrigger>
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
            @click="emit('update:modelValue', [])"
          >
            清空
          </UiButton>
        </div>
        <div class="ui-multi-options" role="group" aria-label="筛选选项">
          <div v-for="option in filtered" :key="option.value" class="ui-multi-option">
            <UiCheckbox
              :model-value="modelValue.includes(option.value)"
              :disabled="option.disabled"
              @update:model-value="toggle(option.value, $event)"
            >
              {{ option.label }}
              <small v-if="option.description" class="muted">{{ option.description }}</small>
            </UiCheckbox>
          </div>
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
  gap: var(--space-2);
  width: 100%;
  min-width: 0;
  height: var(--control);
  padding: 0 var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--input);
  font-size: var(--text-sm);
  text-align: left;
}

.ui-multi-select:focus-visible {
  border-color: var(--link);
  box-shadow: var(--focus);
  outline: none;
}

.ui-multi-select:disabled {
  opacity: 0.5;
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
  padding: var(--space-2);
  border-radius: var(--space-1);
  overflow-wrap: anywhere;
}

.ui-multi-option:hover {
  background: var(--hover);
}

.ui-multi-option small {
  display: block;
}
</style>
