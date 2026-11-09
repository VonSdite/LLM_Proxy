<script setup lang="ts">
import { ref } from 'vue'
import { useField } from './field'
defineOptions({ inheritAttrs: false })
withDefaults(
  defineProps<{
    modelValue?: string | number | null
    type?: string
    mono?: boolean
    invalid?: boolean
  }>(),
  { modelValue: '', type: 'text' },
)
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const field = useField()
const input = ref<HTMLInputElement>()
defineExpose({ focus: () => input.value?.focus(), input })
</script>
<template>
  <input
    :id="field?.id"
    ref="input"
    class="ui-input"
    :class="{ mono }"
    :type="type"
    :value="modelValue ?? ''"
    :aria-invalid="invalid || !!field?.error.value || undefined"
    :aria-required="field?.required?.value || undefined"
    :aria-describedby="field?.describedBy.value"
    v-bind="$attrs"
    @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  />
</template>
<style scoped>
.ui-input {
  width: 100%;
  min-width: 0;
  height: var(--control);
  padding: 0 var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--input);
  color: var(--text);
  outline: none;
  font-size: var(--text-base);
  transition:
    border-color var(--motion),
    box-shadow var(--motion);
}

.ui-input::placeholder {
  color: var(--muted);
  opacity: var(--placeholder-opacity);
}

.ui-input:focus {
  border-color: var(--link);
  box-shadow: var(--focus);
}

.ui-input[aria-invalid='true'] {
  border-color: var(--danger);
}

.ui-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
