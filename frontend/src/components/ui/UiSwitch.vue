<script setup lang="ts">
import { SwitchRoot, SwitchThumb } from 'reka-ui'
import { useId } from 'vue'
import { useField } from './field'
defineProps<{ modelValue: boolean; disabled?: boolean; label?: string }>()
defineOptions({ inheritAttrs: false })
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const field = useField()
const id = field?.id || useId()
</script>
<template>
  <span class="ui-switch-control">
    <SwitchRoot
      :id="id"
      :model-value="modelValue"
      :disabled="disabled"
      :aria-label="label"
      class="ui-switch"
      v-bind="$attrs"
      @update:model-value="emit('update:modelValue', $event)"
    >
      <span class="ui-switch-track"><SwitchThumb class="ui-switch-thumb" /></span>
    </SwitchRoot>
    <label v-if="$slots.default" :for="id"><slot /></label>
  </span>
</template>
<style scoped>
.ui-switch-control {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.ui-switch-control label {
  font-size: var(--text-sm);
  cursor: pointer;
}

.ui-switch {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  background: transparent;
  border-radius: var(--radius-pill);
  flex: none;
}

.ui-switch-track {
  display: block;
  width: 34px;
  height: 20px;
  padding: 2px;
  border: 0;
  border-radius: var(--radius-pill);
  background: var(--border);
  transition: background var(--motion);
  flex: none;
}

.ui-switch[data-state='checked'] .ui-switch-track {
  background: var(--primary);
}

.ui-switch-thumb {
  display: block;
  width: 16px;
  height: 16px;
  border-radius: var(--radius-pill);
  background: var(--on-primary);
  transition: transform var(--motion);
}

.ui-switch-thumb[data-state='checked'] {
  transform: translateX(14px);
}

.ui-switch:disabled {
  opacity: 0.5;
}

@media (pointer: coarse) {
  .ui-switch {
    width: var(--control);
    height: var(--control);
  }
}
</style>
