<script setup lang="ts">
import { computed } from 'vue'
import {
  SelectContent,
  SelectIcon,
  SelectItem,
  SelectItemIndicator,
  SelectItemText,
  SelectPortal,
  SelectRoot,
  SelectTrigger,
  SelectValue,
  SelectViewport,
} from 'reka-ui'
import UiIcon from './UiIcon.vue'
import { useField } from './field'
import type { SelectOption } from './types'
defineOptions({ inheritAttrs: false })
const props = withDefaults(
  defineProps<{
    modelValue?: string
    options: SelectOption[]
    placeholder?: string
    disabled?: boolean
    invalid?: boolean
  }>(),
  { placeholder: '请选择' },
)
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const field = useField()
const empty = '__ui_empty__'
const value = computed(() => (props.modelValue === '' ? empty : props.modelValue))
const label = computed(
  () => props.options.find((option) => option.value === props.modelValue)?.label,
)
function update(next: unknown) {
  emit('update:modelValue', next === empty ? '' : String(next))
}
</script>
<template>
  <SelectRoot
    :model-value="value"
    :disabled="disabled"
    :required="field?.required?.value"
    @update:model-value="update"
  >
    <SelectTrigger
      :id="field?.id"
      class="ui-select"
      :aria-invalid="invalid || !!field?.error.value || undefined"
      :aria-required="field?.required?.value || undefined"
      :aria-describedby="field?.describedBy.value"
      v-bind="$attrs"
    >
      <SelectValue :placeholder="placeholder" class="truncate">{{ label }}</SelectValue>
      <SelectIcon>
        <UiIcon name="down" :size="14" />
      </SelectIcon>
    </SelectTrigger>
    <SelectPortal>
      <SelectContent
        class="ui-select-content"
        position="popper"
        :side-offset="6"
        :collision-padding="12"
      >
        <SelectViewport>
          <SelectItem
            v-for="option in options"
            :key="option.value"
            :value="option.value || empty"
            :text-value="option.label"
            :disabled="option.disabled"
            class="ui-select-item"
          >
            <div class="ui-select-text">
              <slot name="option" :option="option">
                <SelectItemText>{{ option.label }}</SelectItemText>
                <small v-if="option.description" class="muted">{{ option.description }}</small>
              </slot>
            </div>
            <SelectItemIndicator>
              <UiIcon name="check" :size="14" />
            </SelectItemIndicator>
          </SelectItem>
        </SelectViewport>
      </SelectContent>
    </SelectPortal>
  </SelectRoot>
</template>
<style scoped>
.ui-select {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  width: 100%;
  min-width: 0;
  height: var(--control);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--input);
  padding: 0 var(--space-3);
  text-align: left;
  font-size: var(--text-sm);
}

.ui-select:focus-visible {
  outline: none;
  border-color: var(--link);
  box-shadow: var(--focus);
}

.ui-select[aria-invalid='true'] {
  border-color: var(--danger);
}

.ui-select:disabled {
  opacity: 0.5;
}

:global(.ui-select-content) {
  z-index: var(--z-popup);
  width: var(--reka-select-trigger-width);
  min-width: 160px;
  max-width: calc(100vw - 24px);
  max-height: min(320px, var(--reka-select-content-available-height));
  overflow: auto;
  padding: var(--space-1);
  border-radius: var(--radius-control);
  background: var(--overlay);
  color: var(--text);
  box-shadow: var(--shadow-overlay);
}

.ui-select-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  min-height: var(--control-sm);
  padding: var(--space-2);
  border-radius: var(--space-1);
  outline: none;
  font-size: var(--text-sm);
  cursor: pointer;
}

.ui-select-item[data-highlighted] {
  background: var(--hover);
}

.ui-select-item[data-state='checked'] {
  color: var(--link);
}

.ui-select-item[data-disabled] {
  opacity: 0.5;
  cursor: not-allowed;
}

.ui-select-text {
  display: grid;
  min-width: 0;
  overflow-wrap: anywhere;
  gap: 2px;
}
</style>
