<script setup lang="ts">
import TextImportDialog from './TextImportDialog.vue'
const props = defineProps<{
  open: boolean
  title: string
  help?: string | readonly string[]
  submit: (value: unknown, signal: AbortSignal) => Promise<string | void>
}>()
const emit = defineEmits<{ 'update:open': [value: boolean]; imported: [message: string] }>()
async function submitText(text: string, signal: AbortSignal) {
  let value: unknown
  try {
    value = JSON.parse(text.replace(/^\uFEFF/, ''))
  } catch {
    throw new Error('JSON 格式不正确')
  }
  return props.submit(value, signal)
}
</script>
<template>
  <TextImportDialog
    :open="open"
    :title="title"
    :help="help"
    language="json"
    :submit="submitText"
    @update:open="emit('update:open', $event)"
    @imported="emit('imported', $event)"
  />
</template>
