<script setup lang="ts">
import UiSelect from './UiSelect.vue'
defineProps<{ modelValue?: boolean | null; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean | null] }>()
const options = [
  { value: '', label: '不设置' },
  { value: 'true', label: '启用' },
  { value: 'false', label: '禁用' },
]
</script>
<template>
  <UiSelect
    :model-value="modelValue == null ? '' : String(modelValue)"
    :options="options"
    :disabled="disabled"
    @update:model-value="emit('update:modelValue', $event === '' ? null : $event === 'true')"
  />
</template>
