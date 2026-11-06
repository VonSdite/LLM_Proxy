<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { oauthApi } from '@/api/oauth'
import { errorMessage, isAbort } from '@/api/client'
import { copyText } from '@/features/shared/format'
import { UiAlert, UiButton, UiDialog, UiField, UiInput, notify } from '@/components/ui'
const emit = defineEmits<{ close: []; completed: [name: string]; working: [busy: boolean] }>()
const authorizationUrl = ref('')
const callback = ref('')
const busy = ref(false)
const error = ref('')
const fieldError = ref('')
const lifetime = new AbortController()
watch(busy, (value) => emit('working', value), { flush: 'sync' })
onBeforeUnmount(() => {
  lifetime.abort()
  emit('working', false)
})
async function generate() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await oauthApi.start(lifetime.signal)
    if (!lifetime.signal.aborted) authorizationUrl.value = result.authorization_url
  } catch (reason) {
    if (!isAbort(reason)) error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
async function copy() {
  try {
    await copyText(authorizationUrl.value)
    notify('已复制授权链接')
  } catch (reason) {
    error.value = errorMessage(reason)
  }
}
async function submit() {
  fieldError.value = callback.value.trim() ? '' : '请粘贴登录后的完整回调 URL'
  if (busy.value || fieldError.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await oauthApi.callback(callback.value.trim(), lifetime.signal)
    if (lifetime.signal.aborted) return
    notify('已生成认证文件')
    emit('completed', result.auth_file.name)
    emit('close')
  } catch (reason) {
    if (!isAbort(reason)) error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <UiDialog open title="添加 Codex 账号" :busy="busy" @update:open="emit('close')">
    <form id="oauth-login-form" class="stack" @submit.prevent="submit">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <div class="actions">
        <UiButton icon="refresh" :loading="busy" @click="generate">
          {{ authorizationUrl ? '重新生成链接' : '生成授权链接' }}
        </UiButton>
        <UiButton v-if="authorizationUrl" icon="copy" :disabled="busy" @click="copy">
          复制链接
        </UiButton>
        <a
          v-if="authorizationUrl"
          :href="authorizationUrl"
          target="_blank"
          rel="noopener noreferrer"
        >
          打开登录页
        </a>
      </div>
      <UiField v-if="authorizationUrl" label="授权链接">
        <UiInput :model-value="authorizationUrl" readonly aria-label="授权链接" />
      </UiField>
      <UiField label="回调 URL" :error="fieldError" hint="登录完成后，复制地址栏中的完整 URL。">
        <UiInput
          v-model="callback"
          placeholder="http://localhost:1455/auth/callback?…"
          autocomplete="off"
          :disabled="busy"
        />
      </UiField>
    </form>
    <template #footer>
      <UiButton :disabled="busy" @click="emit('close')">取消</UiButton>
      <UiButton variant="primary" type="submit" form="oauth-login-form" :loading="busy">
        完成登录
      </UiButton>
    </template>
  </UiDialog>
</template>
