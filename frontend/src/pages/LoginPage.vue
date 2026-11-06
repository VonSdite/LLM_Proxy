<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { sessionApi } from '@/api/session'
import { ApiError, errorMessage } from '@/api/client'
import { useSession } from '@/stores/session'
import { useTheme } from '@/stores/theme'
import { useLifetime } from '@/features/shared/useLifetime'
import {
  UiAlert,
  UiButton,
  UiField,
  UiIcon,
  UiIconButton,
  UiInput,
  UiSecretInput,
} from '@/components/ui'
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
const submitted = ref(false)
const session = useSession()
const theme = useTheme()
const router = useRouter()
const signal = useLifetime()
async function login() {
  submitted.value = true
  if (!username.value.trim() || !password.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await sessionApi.login(username.value, password.value, signal)
    if (signal.aborted) return
    await session.refresh()
    if (!signal.aborted) await router.replace('/')
  } catch (reason) {
    if (signal.aborted) return
    error.value =
      reason instanceof ApiError && reason.status === 401
        ? '账号或密码不正确'
        : errorMessage(reason)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="login-page">
    <UiIconButton
      class="login-theme"
      :icon="theme.theme === 'dark' ? 'sun' : 'moon'"
      label="切换主题"
      @click="theme.toggle"
    />
    <form class="login-form surface" @submit.prevent="login">
      <div class="login-brand">
        <span class="login-mark"><UiIcon name="network" :size="24" /></span>
        <h1>LLM Proxy</h1>
        <p class="muted">登录管理控制台</p>
      </div>
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <UiField label="账号" :error="submitted && !username.trim() ? '请输入账号' : undefined">
        <UiInput v-model="username" autocomplete="username" autofocus />
      </UiField>
      <UiField label="密码" :error="submitted && !password ? '请输入密码' : undefined">
        <UiSecretInput v-model="password" password-autofill />
      </UiField>
      <UiButton type="submit" variant="primary" :loading="busy">登录</UiButton>
      <small class="login-version muted">v{{ session.context?.version }}</small>
    </form>
  </div>
</template>
<style scoped>
.login-page {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: var(--space-6);
}

.login-theme {
  position: absolute;
  right: var(--space-6);
  top: var(--space-6);
}

.login-form {
  display: grid;
  gap: var(--space-6);
  width: min(380px, 100%);
  padding: var(--space-8);
}

.login-brand {
  display: grid;
  justify-items: center;
  gap: var(--space-2);
}

.login-brand p {
  font-size: var(--text-sm);
}

.login-mark {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: var(--radius-group);
  background: var(--selected);
  color: var(--link);
  margin-bottom: var(--space-2);
}

.login-version {
  text-align: center;
}
</style>
