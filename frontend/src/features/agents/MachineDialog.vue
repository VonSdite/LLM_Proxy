<script setup lang="ts">
import { nextTick, reactive, ref } from 'vue'
import type { AgentMachine, MachineInput } from '@/api/agents'
import { errorMessage } from '@/api/client'
import { UiAlert, UiButton, UiDialog, UiField, UiInput, UiSecretInput } from '@/components/ui'
import ProxySettings from '@/features/shared/ProxySettings.vue'
const props = defineProps<{
  open: boolean
  machine?: AgentMachine
  save: (input: MachineInput, id?: string) => Promise<string>
}>()
const emit = defineEmits<{ 'update:open': [value: boolean]; connect: [id: string] }>()
const draft = reactive<MachineInput>({
  name: props.machine?.name || '',
  host: props.machine?.host || '',
  port: props.machine?.port || '22',
  user: props.machine?.user || '',
  password: '',
  proxy: { mode: props.machine?.proxy.mode || 'direct', url: props.machine?.proxy.url || '' },
})
const busy = ref(false)
const error = ref('')
const errors = reactive<Record<string, string>>({})
const form = ref<HTMLElement>()
function validate() {
  Object.keys(errors).forEach((key) => delete errors[key])
  if (!draft.host.trim() || /[\s/\\@?#]/.test(draft.host)) errors.host = '请填写有效的 IP 或主机名'
  if (!draft.user.trim()) errors.user = '请填写 SSH 用户名'
  const port = Number(draft.port)
  if (!Number.isInteger(port) || port < 1 || port > 65535) errors.port = '端口需为 1–65535 的整数'
  if (draft.proxy.mode === 'custom') {
    try {
      const url = new URL(draft.proxy.url)
      if (!['http:', 'https:', 'socks5:', 'socks5h:'].includes(url.protocol) || !url.hostname)
        errors.proxy = '请输入 HTTP、HTTPS 或 SOCKS5 代理地址'
    } catch {
      errors.proxy = '请输入完整代理地址'
    }
  }
  return !Object.keys(errors).length
}
async function submit(connect: boolean) {
  if (busy.value) return
  if (!validate()) {
    await nextTick()
    form.value?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
    return
  }
  busy.value = true
  error.value = ''
  try {
    const id = await props.save(
      {
        ...draft,
        proxy: { ...draft.proxy, url: draft.proxy.mode === 'custom' ? draft.proxy.url : '' },
      },
      props.machine?.id,
    )
    draft.password = ''
    draft.proxy.url = ''
    emit('update:open', false)
    if (connect) emit('connect', id)
  } catch (reason) {
    error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <UiDialog
    :open="open"
    :title="machine ? '编辑 SSH 机器' : '添加 SSH 机器'"
    :busy="busy"
    @update:open="emit('update:open', $event)"
  >
    <form ref="form" class="stack" @submit.prevent="submit(true)">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <UiField label="名称">
        <UiInput v-model="draft.name" placeholder="默认使用主机地址" :disabled="busy" />
      </UiField>
      <div class="machine-address">
        <UiField label="IP / 主机名" :error="errors.host" required>
          <UiInput
            v-model="draft.host"
            placeholder="192.168.1.20"
            :disabled="busy"
            autocomplete="off"
          />
        </UiField>
        <UiField label="端口" :error="errors.port" required>
          <UiInput v-model="draft.port" inputmode="numeric" :disabled="busy" />
        </UiField>
      </div>
      <div class="form-grid">
        <UiField label="SSH 用户" :error="errors.user" required>
          <UiInput v-model="draft.user" :disabled="busy" autocomplete="off" data-lpignore="true" />
        </UiField>
        <UiField
          label="SSH 密码"
          help="留空时使用当前会话凭据、本机 SSH 密钥或 SSH Agent。密码仅在当前浏览器会话中暂存。"
        >
          <UiSecretInput
            v-model="draft.password"
            :disabled="busy"
            placeholder="留空使用 SSH 密钥"
          />
        </UiField>
        <ProxySettings
          v-model:mode="draft.proxy.mode"
          v-model:url="draft.proxy.url"
          :error="draft.proxy.mode === 'custom' ? errors.proxy : undefined"
        />
      </div>
      <button type="submit" class="sr-only" tabindex="-1">保存并连接</button>
    </form>
    <template #footer>
      <UiButton :disabled="busy" @click="emit('update:open', false)">取消</UiButton>
      <UiButton :loading="busy" @click="submit(false)">保存</UiButton>
      <UiButton variant="primary" icon="connect" :loading="busy" @click="submit(true)">
        保存并连接
      </UiButton>
    </template>
  </UiDialog>
</template>
<style scoped>
.machine-address {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px;
  gap: var(--space-4);
}

@media (width <= 600px) {
  .machine-address {
    grid-template-columns: minmax(0, 1fr) 100px;
  }
}
</style>
