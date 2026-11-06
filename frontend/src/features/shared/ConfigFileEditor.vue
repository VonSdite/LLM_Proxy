<script setup lang="ts">
import { computed, ref } from 'vue'
import { errorMessage } from '@/api/client'
import { UiAlert, UiButton, UiCodeEditor, UiDialog, UiSelect } from '@/components/ui'
const props = defineProps<{
  open: boolean
  source: { path: string; text: string; version: string }
  language: 'json' | 'jsonc' | 'toml' | 'yaml' | 'text'
  save: (text: string, version: string) => Promise<void>
  loadBackup?: (backup: number) => Promise<{ text: string; version: string; exists: boolean }>
  saveUnchanged?: boolean
}>()
const emit = defineEmits<{ 'update:open': [value: boolean]; saved: [] }>()
const content = ref(props.source.text.replace(/\r\n?/g, '\n'))
const baseline = ref(content.value)
const version = ref(props.source.version)
const selectedBackup = ref('0')
const busy = ref(false)
const error = ref('')
const closePrompt = ref(false)
const dirty = computed(() => content.value !== baseline.value)
const filename = computed(() => props.source.path.split(/[\\/]/).at(-1) || '配置文件')
const backups = [
  { value: '0', label: '当前文件' },
  ...Array.from({ length: 5 }, (_, i) => ({ value: String(i + 1), label: `备份 ${i + 1}` })),
]
function requestClose() {
  if (dirty.value) {
    closePrompt.value = true
    return false
  }
  return true
}
async function persist() {
  if (busy.value || (!dirty.value && !props.saveUnchanged)) return
  busy.value = true
  closePrompt.value = false
  error.value = ''
  try {
    await props.save(content.value, version.value)
    baseline.value = content.value
    emit('saved')
    emit('update:open', false)
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
async function readBackup(value: string) {
  if (!props.loadBackup || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const source = await props.loadBackup(Number(value))
    if (value !== '0' && !source.exists) throw new Error('该备份不存在')
    content.value = source.text.replace(/\r\n?/g, '\n')
    if (value === '0') {
      baseline.value = content.value
      version.value = source.version
    }
    selectedBackup.value = value
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
function discard() {
  closePrompt.value = false
  emit('update:open', false)
}
</script>
<template>
  <UiDialog
    :open="open"
    :title="filename"
    size="xl"
    :busy="busy"
    :before-close="requestClose"
    @update:open="emit('update:open', $event)"
  >
    <div class="stack">
      <div class="editor-toolbar">
        <code class="muted truncate" :title="source.path">{{ source.path }}</code>
        <div v-if="props.loadBackup" class="editor-backups">
          <UiSelect
            :model-value="selectedBackup"
            :options="backups"
            :disabled="busy"
            aria-label="读取备份"
            @update:model-value="readBackup"
          />
        </div>
      </div>
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <UiCodeEditor
        v-model="content"
        :language="language"
        :read-only="busy"
        :label="`${filename} 内容`"
        @save="persist"
      />
    </div>
    <template #footer>
      <span class="editor-status muted">
        {{ content.split('\n').length }} 行 · {{ dirty ? '有未保存的修改' : '未修改' }}
      </span>
      <UiButton :disabled="busy" @click="requestClose() && emit('update:open', false)">
        关闭
      </UiButton>
      <UiButton
        variant="primary"
        icon="save"
        :loading="busy"
        :disabled="!dirty && !saveUnchanged"
        @click="persist"
      >
        保存
      </UiButton>
    </template>
  </UiDialog>
  <UiDialog
    v-model:open="closePrompt"
    title="保存修改？"
    description="配置文件有未保存的修改。"
    size="sm"
  >
    <template #footer>
      <UiButton @click="closePrompt = false">继续编辑</UiButton>
      <UiButton variant="danger" @click="discard">放弃修改</UiButton>
      <UiButton variant="primary" @click="persist">保存</UiButton>
    </template>
  </UiDialog>
</template>
<style scoped>
.editor-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.editor-toolbar code {
  flex: 1;
}

.editor-backups {
  width: 140px;
  flex: none;
}

.editor-status {
  align-self: center;
  margin-right: auto;
  font-size: var(--text-xs);
}

@media (width <= 600px) {
  .editor-toolbar {
    flex-wrap: wrap;
  }

  .editor-toolbar code {
    flex-basis: 100%;
  }
}
</style>
