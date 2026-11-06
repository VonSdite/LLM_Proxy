<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useSession } from '@/stores/session'
import { useTheme } from '@/stores/theme'
import { sessionApi } from '@/api/session'
import { errorMessage } from '@/api/client'
import { UiButton, UiIcon, UiIconButton, notify } from '@/components/ui'
const session = useSession()
const theme = useTheme()
const router = useRouter()
const route = useRoute()
const navigation = computed(() =>
  [
    { path: '/', label: 'Provider', show: true },
    { path: '/oauth', label: 'OAuth', show: session.context?.features.oauth },
    { path: '/model-mappings', label: '模型映射', show: session.context?.features.model_mapping },
    { path: '/api-keys', label: 'API Key', show: session.context?.features.api_keys },
    { path: '/agents', label: 'Agent 配置', show: true },
    { path: '/users', label: '用户', show: true },
    { path: '/statistics', label: '统计', show: true },
    { path: '/settings', label: '设置', show: true },
  ].filter((item) => item.show),
)
async function logout() {
  try {
    await sessionApi.logout()
    session.clear()
    await router.replace('/login')
  } catch (reason) {
    notify(errorMessage(reason), 'danger')
  }
}
</script>
<template>
  <div class="app-shell">
    <a href="#main-content" class="skip-link">跳转到内容</a>
    <header class="app-header">
      <RouterLink to="/" class="app-brand">
        <UiIcon name="network" :size="22" />
        <span>LLM Proxy</span>
        <small>v{{ session.context?.version }}</small>
      </RouterLink>
      <nav class="app-nav" aria-label="主导航">
        <template v-for="item in navigation" :key="item.path">
          <RouterLink
            :to="item.path"
            class="app-nav-link"
            :class="{
              'is-active':
                route.path === item.path || (item.path === '/' && route.path === '/providers'),
            }"
          >
            {{ item.label }}
          </RouterLink>
        </template>
      </nav>
      <div class="app-user">
        <UiIconButton
          :icon="theme.theme === 'dark' ? 'sun' : 'moon'"
          label="切换主题"
          @click="theme.toggle"
        />
        <template v-if="session.context?.auth_enabled">
          <span class="app-username">{{ session.context.username }}</span>
          <UiButton variant="ghost" icon="logout" size="sm" @click="logout">退出</UiButton>
        </template>
      </div>
    </header>
    <main id="main-content" class="app-content" tabindex="-1"><slot /></main>
  </div>
</template>
<style scoped>
.app-shell {
  min-height: 100dvh;
}

.app-header {
  position: sticky;
  top: 0;
  z-index: var(--z-header);
  display: flex;
  align-items: center;
  gap: var(--space-6);
  min-height: 64px;
  padding: var(--space-3) var(--space-8);
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}

.app-brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--text);
  font-weight: 650;
  white-space: nowrap;
  letter-spacing: -0.02em;
}

.app-brand:hover {
  text-decoration: none;
}

.app-brand > svg {
  color: var(--link);
}

.app-brand small {
  color: var(--muted);
  font-size: 10px;
  font-weight: 400;
}

.app-nav {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  overflow: auto;
  flex: 1;
}

.app-nav-link {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-control);
  color: var(--muted);
  font-size: var(--text-sm);
  white-space: nowrap;
}

.app-nav-link:hover {
  color: var(--text);
  background: var(--hover);
  text-decoration: none;
}

.app-nav-link.is-active {
  color: var(--text);
  background: var(--hover);
  font-weight: 550;
}

.app-user {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.app-username {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: var(--text-xs);
  color: var(--muted);
}

.app-content {
  max-width: 1760px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-8) var(--space-8);
  outline: none;
}

.skip-link {
  position: fixed;
  top: -100px;
  left: var(--space-4);
  z-index: var(--z-toast);
  padding: var(--space-3);
  background: var(--surface);
}

.skip-link:focus {
  top: var(--space-2);
}

@media (pointer: coarse) {
  .app-nav-link {
    display: inline-flex;
    align-items: center;
    min-height: var(--control);
  }
}

@media (width <= 1200px) {
  .app-header {
    flex-wrap: wrap;
    gap: var(--space-2);
    padding-inline: var(--space-6);
  }

  .app-nav {
    order: 3;
    flex-basis: 100%;
  }

  .app-user {
    margin-left: auto;
  }
}

@media (width <= 600px) {
  .app-header {
    padding-inline: var(--space-4);
  }

  .app-content {
    padding: var(--space-4);
  }

  .app-brand small {
    display: none;
  }
}
</style>
