<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { oauthApi } from '@/api/oauth'
import { errorMessage, isAbort } from '@/api/client'
import { copyText } from '@/features/shared/format'
import { useDraftChanges } from '@/features/shared/useDraftChanges'
import {
  UiAlert,
  UiButton,
  UiDialog,
  UiField,
  UiIconButton,
  UiInput,
  UiPopover,
  notify,
} from '@/components/ui'
const emit = defineEmits<{ close: []; completed: [name: string]; working: [busy: boolean] }>()
const authorizationUrl = ref('')
const callback = ref('')
const busy = ref(false)
const error = ref('')
const fieldError = ref('')
const copyOpen = ref(false)
const authorizationInput = ref<InstanceType<typeof UiInput>>()
function selectAuthorization(event: Event) {
  const input = event.target as HTMLInputElement
  input.focus()
  input.select()
  input.scrollLeft = 0
  copyOpen.value = true
}
const lifetime = new AbortController()
watch(busy, (value) => emit('working', value), { flush: 'sync' })
onBeforeUnmount(() => {
  lifetime.abort()
  emit('working', false)
})
async function generate() {
  if (busy.value) return
  busy.value = true
  copyOpen.value = false
  error.value = ''
  try {
    const result = await oauthApi.start(lifetime.signal)
    if (!lifetime.signal.aborted) {
      authorizationUrl.value = result.authorization_url
      await nextTick()
      if (!lifetime.signal.aborted) authorizationInput.value?.focus()
    }
  } catch (reason) {
    if (!isAbort(reason)) error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
async function copy() {
  try {
    await copyText(authorizationUrl.value)
    if (lifetime.signal.aborted) return
    copyOpen.value = false
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
const dirty = useDraftChanges(() => callback.value)
</script>
<template>
  <UiDialog
    open
    title="添加 Codex 账号"
    :busy="busy"
    :dirty="dirty"
    :save="submit"
    save-label="完成登录"
    @update:open="emit('close')"
  >
    <form id="oauth-login-form" class="stack" @submit.prevent="submit">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <div class="actions">
        <UiButton icon="refresh" :loading="busy" @click="generate">
          {{ authorizationUrl ? '重新生成链接' : '生成授权链接' }}
        </UiButton>
      </div>
      <UiField v-if="authorizationUrl" label="授权链接">
        <div class="authorization-link">
          <div class="authorization-input">
            <UiInput
              ref="authorizationInput"
              :model-value="authorizationUrl"
              readonly
              aria-label="授权链接"
              aria-haspopup="dialog"
              :aria-expanded="copyOpen"
              @focus="selectAuthorization"
              @pointerdown.prevent="selectAuthorization"
              @click="selectAuthorization"
              @keydown.down.prevent="copyOpen = true"
            />
            <UiPopover v-model:open="copyOpen" title="复制授权链接" compact="icon" side="top">
              <template #anchor>
                <span class="authorization-copy-anchor" aria-hidden="true" />
              </template>
              <UiIconButton
                icon="copy"
                label="复制授权链接"
                size="xs"
                :disabled="busy"
                @mousedown.prevent
                @click="copy"
              />
            </UiPopover>
          </div>
          <a :href="authorizationUrl" target="_blank" rel="noopener noreferrer">打开链接</a>
        </div>
      </UiField>
      <UiField
        label="回调 URL"
        :error="fieldError"
        hint="登录完成后，复制地址栏中的完整 URL。"
        help="先在授权链接中完成登录，再粘贴跳转后的完整 localhost 回调地址，保留 code、state 等查询参数。即使浏览器提示本地页面无法访问，也可复制该地址回来完成登录；重新生成链接后请使用新链接对应的回调。"
      >
        <UiInput
          v-model="callback"
          placeholder="http://localhost:1455/auth/callback?…"
          autocomplete="off"
          :disabled="busy"
        />
      </UiField>
    </form>
    <template #footer="{ close }">
      <UiButton :disabled="busy" @click="close()">取消</UiButton>
      <UiButton variant="primary" type="submit" form="oauth-login-form" :loading="busy">
        完成登录
      </UiButton>
    </template>
  </UiDialog>
</template>

<style scoped>
.authorization-link {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
}

.authorization-link a {
  white-space: nowrap;
}

.authorization-input {
  position: relative;
  min-width: 0;
}

.authorization-copy-anchor {
  position: absolute;
  top: 0;
  right: var(--space-2);
  width: var(--control-xs);
  height: 100%;
  pointer-events: none;
}
</style>
