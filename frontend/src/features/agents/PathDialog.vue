<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AgentId, FileSnapshot } from '@/api/agents'
import { errorMessage } from '@/api/client'
import { UiButton, UiDialog, UiField, UiInput } from '@/components/ui'
const props = defineProps<{
  open: boolean
  agent: AgentId
  file: FileSnapshot
  save: (agent: AgentId, path: string) => Promise<void>
}>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
const path = ref(props.file.customPath || '')
const busy = ref(false)
const error = ref('')
const label = computed(() =>
  props.agent === 'codex'
    ? 'Codex 配置目录'
    : `${props.agent === 'claude' ? 'Claude' : 'opencode'} 配置文件`,
)
const defaultPath = computed(() =>
  props.agent === 'codex'
    ? props.file.defaultDirectory || props.file.defaultPath.replace(/[\\/][^\\/]*$/, '')
    : props.file.defaultPath,
)
async function submit(value = path.value) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await props.save(props.agent, value.trim())
    emit('update:open', false)
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <UiDialog :open="open" :title="label" :busy="busy" @update:open="emit('update:open', $event)">
    <form @submit.prevent="submit()">
      <UiField
        :label="agent === 'codex' ? '目录路径' : '文件路径'"
        :error="error"
        :hint="`默认：${defaultPath}`"
        :help="
          agent === 'codex'
            ? '该目录同时关联 config.toml 和 auth.json。按机器保存，下次打开继续使用。'
            : '支持绝对路径和用户目录路径。按机器保存，下次打开继续使用。'
        "
      >
        <UiInput
          v-model="path"
          :placeholder="defaultPath"
          :disabled="busy"
          autocomplete="off"
          mono
        />
      </UiField>
    </form>
    <template #footer>
      <UiButton :disabled="busy" icon="reset" @click="submit('')">恢复默认</UiButton>
      <UiButton :disabled="busy" @click="emit('update:open', false)">取消</UiButton>
      <UiButton variant="primary" :loading="busy" @click="submit()">保存</UiButton>
    </template>
  </UiDialog>
</template>
