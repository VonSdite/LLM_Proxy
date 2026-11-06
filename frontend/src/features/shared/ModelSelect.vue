<script setup lang="ts">
import { computed } from 'vue'
import { UiCombobox, UiInput, type SelectOption } from '@/components/ui'
const props = defineProps<{
  modelValue: string
  models: string[]
  ownService?: boolean
  allowUnset?: boolean
  largeContext?: boolean
  disabled?: boolean
  placeholder?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const options = computed<SelectOption[]>(() => {
  const ids = props.largeContext
    ? props.models.flatMap((id) => {
        const base = id.replace(/\[1m\]$/i, '')
        return [base, `${base}[1m]`]
      })
    : props.models
  const values = [...new Set(ids)]
  const result = values.map((value) => ({
    value,
    label: value.replace(/\[1m\]$/i, ' [1M]'),
  })) as SelectOption[]
  if (props.modelValue && !values.includes(props.modelValue))
    result.unshift({
      value: props.modelValue,
      label: props.modelValue,
      description: '当前配置 · 不在可用模型列表',
    })
  if (props.allowUnset) result.unshift({ value: '', label: '不设置' })
  return result
})
</script>
<template>
  <UiCombobox
    v-if="ownService"
    :model-value="modelValue"
    :options="options"
    :disabled="disabled"
    :placeholder="placeholder || (allowUnset ? '不设置' : '选择模型')"
    @update:model-value="emit('update:modelValue', $event)"
  />
  <UiInput
    v-else
    :model-value="modelValue"
    :disabled="disabled"
    :placeholder="placeholder || '输入模型 ID'"
    autocomplete="off"
    :spellcheck="false"
    mono
    @update:model-value="emit('update:modelValue', $event)"
  />
</template>
