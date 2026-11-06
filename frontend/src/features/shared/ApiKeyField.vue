<script setup lang="ts">
import { computed } from 'vue'
import { UiCombobox, UiSecretInput } from '@/components/ui'
const props = defineProps<{
  modelValue: string
  candidates?: { name: string; key: string }[]
  disabled?: boolean
  offerCandidates?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const options = computed(() =>
  (props.candidates || []).map((key) => ({
    value: key.key,
    label: key.name,
    description: key.key.length > 8 ? `${key.key.slice(0, 3)}••••${key.key.slice(-4)}` : '••••••••',
  })),
)
</script>
<template>
  <UiCombobox
    v-if="offerCandidates"
    :model-value="modelValue"
    :options="options"
    allow-custom
    secret
    :disabled="disabled"
    placeholder="选择已有 Key 或手动输入"
    @update:model-value="emit('update:modelValue', $event)"
  />
  <UiSecretInput
    v-else
    :model-value="modelValue"
    :disabled="disabled"
    placeholder="手动输入 API Key"
    @update:model-value="emit('update:modelValue', $event)"
  />
</template>
