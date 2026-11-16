<script setup lang="ts">
import { computed } from 'vue'
import { UiTagInput } from '@/components/ui'
const props = defineProps<{ modelValue: string; disabled?: boolean; placeholder?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const selected = computed(() => [...new Set(props.modelValue.split(/[,，\s]+/).filter(Boolean))])
const options = [
  'openai',
  'anthropic',
  'google',
  'amazon-bedrock',
  'azure',
  'openrouter',
  'github-copilot',
  'deepseek',
  'groq',
  'mistral',
  'xai',
  'opencode',
].map((value) => ({ value, label: value }))
</script>
<template>
  <UiTagInput
    :model-value="selected"
    :options="options"
    :disabled="disabled"
    :placeholder="placeholder"
    @update:model-value="emit('update:modelValue', $event.join(', '))"
  />
</template>
