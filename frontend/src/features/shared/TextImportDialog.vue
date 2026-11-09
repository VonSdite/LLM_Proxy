<script setup lang="ts">
import { ref } from 'vue'
import { errorMessage } from '@/api/client'
import { useDraftChanges } from './useDraftChanges'
import { useLifetime } from './useLifetime'
import { UiAlert, UiButton, UiCodeEditor, UiDialog, UiField } from '@/components/ui'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    help?: string | readonly string[]
    language: 'json' | 'yaml'
    initialValue?: string
    confirmLabel?: string
    submit: (text: string, signal: AbortSignal) => Promise<string | void>
  }>(),
  { initialValue: '', confirmLabel: '导入' },
)
const emit = defineEmits<{ 'update:open': [value: boolean]; imported: [message: string] }>()
const text = ref(props.initialValue)
const error = ref('')
const busy = ref(false)
const fileInput = ref<HTMLInputElement>()
const signal = useLifetime()
async function loadFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const content = await file.text()
    if (!signal.aborted) text.value = content.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n')
  } catch (reason) {
    if (!signal.aborted) error.value = errorMessage(reason)
  } finally {
    busy.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}
async function performImport() {
  if (busy.value) return
  error.value = ''
  busy.value = true
  try {
    const message = await props.submit(text.value, signal)
    if (signal.aborted) return
    emit('imported', message || '导入完成')
    emit('update:open', false)
  } catch (reason) {
    if (!signal.aborted) error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
const dirty = useDraftChanges(() => text.value)
</script>
<template>
  <UiDialog
    :save="performImport"
    :save-label="`${confirmLabel}并关闭`"
    :dirty="dirty"
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
          {{ language === 'json' ? '选择 JSON 文件' : '选择 YAML / JSON 文件' }}
        </UiButton>
        <input
          ref="fileInput"
          type="file"
          :accept="language === 'json' ? '.json,application/json' : '.yaml,.yml,.json'"
          class="sr-only"
          :aria-label="language === 'json' ? '选择 JSON 文件' : '选择 YAML / JSON 文件'"
          :disabled="busy"
          @change="loadFile"
        />
      </div>
      <UiField label="导入内容" :help="help">
        <UiCodeEditor
          v-model="text"
          :language="language"
          :read-only="busy"
          height="340px"
          @save="performImport"
        />
      </UiField>
    </div>
    <template #footer="{ close }">
      <UiButton :disabled="busy" @click="close()">取消</UiButton>
      <UiButton variant="primary" :loading="busy" :disabled="!text.trim()" @click="performImport">
        {{ confirmLabel }}
      </UiButton>
    </template>
  </UiDialog>
</template>
