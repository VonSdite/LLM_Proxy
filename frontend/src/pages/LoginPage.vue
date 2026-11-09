<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { sessionApi } from '@/api/session'
import { ApiError, errorMessage } from '@/api/client'
import { useSession } from '@/stores/session'
import { useTheme } from '@/stores/theme'
import { useLifetime } from '@/features/shared/useLifetime'
import { UiAlert, UiButton, UiField, UiIconButton, UiInput, UiSecretInput } from '@/components/ui'
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
      <header class="login-heading">
        <h1>登录</h1>
      </header>
      <div class="login-body">
        <UiAlert v-if="error">{{ error }}</UiAlert>
        <UiField label="用户名" :error="submitted && !username.trim() ? '请输入用户名' : undefined">
          <UiInput
            v-model="username"
            autocomplete="username"
            placeholder="请输入用户名"
            autofocus
          />
        </UiField>
        <UiField label="密码" :error="submitted && !password ? '请输入密码' : undefined">
          <UiSecretInput v-model="password" placeholder="请输入密码" password-autofill />
        </UiField>
        <UiButton type="submit" variant="primary" :loading="busy">登录</UiButton>
      </div>
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
  --control: 40px;

  width: min(420px, 100%);
  overflow: hidden;
  box-shadow: var(--shadow-soft);
}

.login-heading {
  padding: var(--space-5) 28px;
  background: var(--surface-tint);
  border-bottom: 1px solid var(--divider);
}

.login-body {
  display: grid;
  gap: var(--space-5);
  padding: var(--space-6) 28px 28px;
}

@media (pointer: coarse) {
  .login-form {
    --control: 44px;
  }
}

@media (width <= 480px) {
  .login-page {
    padding: var(--space-5);
  }

  .login-heading {
    padding: var(--space-5) var(--space-6);
  }

  .login-body {
    padding: var(--space-6);
  }
}
</style>
