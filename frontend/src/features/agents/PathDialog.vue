<script setup lang="ts">
import { computed, ref } from 'vue'
import type { FileTarget, FileSnapshot } from '@/api/agents'
import { errorMessage } from '@/api/client'
import { useDraftChanges } from '@/features/shared/useDraftChanges'
import { UiButton, UiDialog, UiField, UiInput } from '@/components/ui'
import { AGENT_DEFINITIONS } from './registry'
const props = defineProps<{
  open: boolean
  agent: FileTarget
  file: FileSnapshot
  save: (agent: FileTarget, path: string) => Promise<void>
}>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
const path = ref(props.file.customPath || '')
const busy = ref(false)
const error = ref('')
const label = computed(() =>
  props.agent === 'codex'
    ? 'Codex 配置文件（config.toml）'
    : props.agent === 'codexAuth'
      ? 'Codex 认证文件（auth.json）'
      : `${AGENT_DEFINITIONS.find((item) => item.files.includes(props.agent))?.label || props.agent} 配置文件`,
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
const dirty = useDraftChanges(() => path.value)
</script>
<template>
  <UiDialog
    :save="() => submit()"
    :open="open"
    :title="label"
    :busy="busy"
    :dirty="dirty"
    @update:open="emit('update:open', $event)"
  >
    <form @submit.prevent="submit()">
      <UiField
        label="文件路径"
        :error="error"
        :help="
          agent === 'codex' || agent === 'codexAuth'
            ? '填写当前文件的完整路径，支持绝对路径和 ~ 用户目录。config.toml 与 auth.json 分别保存路径，留空只恢复当前文件的默认路径。这里只决定网页编辑的位置，不会改变 Codex 自身加载配置或凭据的规则。'
            : '填写目标机器上的配置文件路径，支持绝对路径和 ~ 用户目录。按机器保存，下次打开继续使用；留空恢复默认。这里只决定网页编辑的位置，不会自动让 Agent 加载一个任意的新路径。'
        "
      >
        <UiInput
          v-model="path"
          :placeholder="file.defaultPath"
          :disabled="busy"
          autocomplete="off"
          mono
        />
      </UiField>
    </form>
    <template #footer="{ close }">
      <UiButton :disabled="busy" icon="reset" @click="submit('')">恢复默认</UiButton>
      <UiButton :disabled="busy" @click="close()">取消</UiButton>
      <UiButton variant="primary" :loading="busy" @click="submit()">保存</UiButton>
    </template>
  </UiDialog>
</template>
