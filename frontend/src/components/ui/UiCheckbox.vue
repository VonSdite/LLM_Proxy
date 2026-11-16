<script setup lang="ts">
import { CheckboxIndicator, CheckboxRoot } from 'reka-ui'
import { useId } from 'vue'
import UiIcon from './UiIcon.vue'
defineProps<{
  modelValue?: boolean | 'indeterminate'
  disabled?: boolean
  label?: string
  variant?: 'button'
}>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const id = useId()
</script>
<template>
  <span class="ui-checkbox-label">
    <CheckboxRoot
      :id="id"
      :model-value="modelValue"
      :disabled="disabled"
      :aria-label="label"
      class="ui-checkbox"
      :class="{ 'ui-checkbox--button': variant === 'button' }"
      @update:model-value="emit('update:modelValue', $event === true)"
    >
      <span class="ui-checkbox-mark">
        <CheckboxIndicator>
          <span v-if="modelValue === 'indeterminate'">−</span>
          <UiIcon v-else name="check" :size="13" />
        </CheckboxIndicator>
      </span>
      <span v-if="variant === 'button' && $slots.default"><slot /></span>
    </CheckboxRoot>
    <label v-if="$slots.default && variant !== 'button'" :for="id"><slot /></label>
  </span>
</template>
<style scoped>
.ui-checkbox-label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-base);
}

.ui-checkbox {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  background: transparent;
  border-radius: var(--space-1);
  flex: none;
}

.ui-checkbox-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 17px;
  height: 17px;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--space-1);
  background: var(--input);
  flex: none;
}

.ui-checkbox[data-state='checked'] .ui-checkbox-mark,
.ui-checkbox[data-state='indeterminate'] .ui-checkbox-mark {
  background: var(--primary);
  color: var(--on-primary);
  border-color: var(--primary);
}

.ui-checkbox:disabled {
  opacity: 0.5;
}

.ui-checkbox--button {
  min-height: var(--control);
  gap: var(--space-2);
  padding-inline: var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  white-space: nowrap;
  cursor: pointer;
}

.ui-checkbox--button:hover:not(:disabled) {
  background: var(--hover);
}

.ui-checkbox--button:disabled {
  cursor: not-allowed;
}

@media (pointer: coarse) {
  .ui-checkbox:not(.ui-checkbox--button) {
    width: var(--control);
    height: var(--control);
  }
}
</style>
