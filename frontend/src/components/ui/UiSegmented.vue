<script setup lang="ts">
import UiTabs from './UiTabs.vue'
import type { SelectOption } from './types'
defineProps<{ modelValue: string; items: SelectOption[]; label?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
</script>
<template>
  <UiTabs
    :model-value="modelValue"
    :items="items"
    :label="label"
    segmented
    @update:model-value="emit('update:modelValue', $event)"
  />
</template>
