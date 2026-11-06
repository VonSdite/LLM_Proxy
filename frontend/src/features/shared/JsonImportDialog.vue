<script setup lang="ts">
import { ref } from 'vue'
import { errorMessage } from '@/api/client'
import { useLifetime } from './useLifetime'
import { UiAlert, UiButton, UiCodeEditor, UiDialog, UiField } from '@/components/ui'
const props = defineProps<{
  open: boolean
  title: string
  submit: (value: unknown, signal: AbortSignal) => Promise<string | void>
}>()
const emit = defineEmits<{ 'update:open': [value: boolean]; imported: [message: string] }>()
const text = ref('')
const error = ref('')
const busy = ref(false)
const fileInput = ref<HTMLInputElement>()
const signal = useLifetime()
async function loadFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (file) text.value = await file.text()
  if (fileInput.value) fileInput.value.value = ''
}
async function performImport() {
  if (busy.value) return
  error.value = ''
  let value: unknown
  try {
    value = JSON.parse(text.value.replace(/^\uFEFF/, ''))
  } catch {
    error.value = 'JSON 格式不正确'
    return
  }
  busy.value = true
  try {
    const message = await props.submit(value, signal)
    if (signal.aborted) return
    emit('imported', message || '导入完成')
    emit('update:open', false)
  } catch (reason) {
    if (!signal.aborted) error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <UiDialog
    :open="open"
    :title="title"
    size="lg"
    :busy="busy"
    @update:open="emit('update:open', $event)"
  >
    <div class="stack">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <div class="actions">
        <UiButton icon="upload" :disabled="busy" @click="fileInput?.click()">
          选择 JSON 文件
        </UiButton>
        <input
          ref="fileInput"
          type="file"
          accept=".json,application/json"
          class="sr-only"
          aria-label="选择 JSON 文件"
          @change="loadFile"
        />
      </div>
      <UiField label="导入内容">
        <UiCodeEditor v-model="text" language="json" :read-only="busy" height="340px" />
      </UiField>
    </div>
    <template #footer>
      <UiButton :disabled="busy" @click="emit('update:open', false)">取消</UiButton>
      <UiButton variant="primary" :loading="busy" :disabled="!text.trim()" @click="performImport">
        导入
      </UiButton>
    </template>
  </UiDialog>
</template>
