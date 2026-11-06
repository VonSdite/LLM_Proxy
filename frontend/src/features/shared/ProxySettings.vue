<script setup lang="ts">
import { UiField, UiInput, UiSelect } from '@/components/ui'
defineProps<{
  mode: string
  url: string
  error?: string
  disabled?: boolean
  modes?: { value: string; label: string }[]
}>()
const emit = defineEmits<{ 'update:mode': [value: string]; 'update:url': [value: string] }>()
const defaults = [
  { value: 'system', label: '系统代理' },
  { value: 'direct', label: '直连' },
  { value: 'custom', label: '自定义代理' },
]
</script>
<template>
  <UiField label="连接方式">
    <UiSelect
      :model-value="mode"
      :options="modes || defaults"
      :disabled="disabled"
      @update:model-value="emit('update:mode', $event)"
    />
  </UiField>
  <UiField
    v-if="mode === 'custom'"
    label="代理地址"
    :error="error"
    help="支持 HTTP、HTTPS、SOCKS5 和 SOCKS5H；含认证信息时可使用 协议://用户:密码@主机:端口。"
  >
    <UiInput
      :model-value="url"
      :disabled="disabled"
      placeholder="socks5://127.0.0.1:1080"
      autocomplete="off"
      data-lpignore="true"
      data-1p-ignore="true"
      @update:model-value="emit('update:url', $event)"
    />
  </UiField>
</template>
