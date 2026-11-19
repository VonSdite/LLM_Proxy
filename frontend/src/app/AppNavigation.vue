<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { usePageRegistry } from '@/stores/pages'
import { useSession } from '@/stores/session'
import { useTheme } from '@/stores/theme'
import { sessionApi } from '@/api/session'
import { errorMessage } from '@/api/client'
import { UiButton, UiDropdownMenu, UiIcon, UiTooltip, notify } from '@/components/ui'
import { adminPageRouteName } from './page-contract'
import type { NavigationPage } from './page-registry'

defineProps<{ collapsed?: boolean }>()
const pages = usePageRegistry()
const session = useSession()
const theme = useTheme()
const router = useRouter()
const route = useRoute()
const initial = computed(() =>
  Array.from(session.context?.username.trim() || 'A')[0]!.toUpperCase(),
)
const navigation = computed(() =>
  pages.navigationPages.filter(
    (page) => !page.visible || (!!session.context && page.visible(session.context)),
  ),
)
function isActive(page: NavigationPage) {
  if (page.kind === 'extension') {
    return route.name === adminPageRouteName(page) && route.params.pageId === page.id
  }
  return route.name === adminPageRouteName(page)
}
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
  <div class="app-navigation" :class="{ 'app-navigation--collapsed': collapsed }">
    <nav class="app-nav" aria-label="主导航">
      <RouterLink
        v-for="item in navigation"
        :key="item.id"
        :to="item.path"
        class="app-nav-link"
        :class="{ 'is-active': isActive(item) }"
        :aria-label="item.navigation.label"
        :aria-current="isActive(item) ? 'page' : undefined"
        :title="collapsed ? item.navigation.label : undefined"
      >
        <UiIcon :name="item.navigation.icon" :size="19" />
        <span v-if="!collapsed">{{ item.navigation.label }}</span>
      </RouterLink>
    </nav>
    <footer class="app-nav-footer">
      <UiDropdownMenu
        v-if="session.context?.auth_enabled"
        label="账户菜单"
        :items="[{ value: 'logout', label: '退出登录', icon: 'logout', danger: true }]"
        side="top"
        @select="logout"
      >
        <UiButton
          variant="ghost"
          class="app-account"
          :aria-label="`${session.context.username} 的账户菜单`"
        >
          <span class="app-avatar" aria-hidden="true">{{ initial }}</span>
          <span v-if="!collapsed" class="app-username truncate">
            {{ session.context.username }}
          </span>
          <UiIcon v-if="!collapsed" name="sort" :size="14" class="app-account-caret" />
        </UiButton>
      </UiDropdownMenu>
      <UiTooltip :text="theme.theme === 'dark' ? '切换为浅色模式' : '切换为深色模式'">
        <UiButton
          variant="ghost"
          :icon="theme.theme === 'dark' ? 'sun' : 'moon'"
          class="app-theme-toggle"
          aria-label="切换主题"
          @click="theme.toggle"
        />
      </UiTooltip>
    </footer>
  </div>
</template>
<style scoped>
.app-navigation {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: var(--space-4);
  min-height: 0;
  min-width: 0;
}

.app-nav {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-1);
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.app-nav-link {
  display: flex;
  flex: none;
  align-items: center;
  gap: var(--space-3);
  min-height: 44px;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-control);
  color: var(--muted);
  font-size: var(--text-base);
  white-space: nowrap;
}

.app-nav-link:hover {
  color: var(--text);
  background: var(--hover);
  text-decoration: none;
}

.app-nav-link.is-active {
  color: var(--link);
  background: var(--selected);
  font-weight: var(--weight-emphasis);
}

.app-nav-footer {
  display: flex;
  flex: none;
  align-items: center;
  gap: var(--space-1);
  padding-top: var(--space-3);
  border-top: 1px solid var(--divider);
}

.app-account {
  flex: 1;
  justify-content: flex-start;
  min-width: 0;
  min-height: 44px;
  padding: var(--space-1) var(--space-2);
}

.app-account[data-state='open'] {
  background: var(--hover);
}

.app-avatar {
  display: grid;
  flex: none;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-pill);
  background: var(--hover);
  color: var(--muted);
  font-size: var(--text-sm);
  font-weight: var(--weight-emphasis);
}

.app-username {
  flex: 1;
  min-width: 0;
  font-size: var(--text-base);
  text-align: left;
}

.app-account-caret {
  color: var(--muted);
}

.app-theme-toggle {
  flex: none;
  width: var(--control);
  padding: 0;
  color: var(--muted);
}

.app-navigation--collapsed .app-nav-footer {
  flex-direction: column;
}

.app-navigation--collapsed .app-theme-toggle {
  order: -1;
}

.app-navigation--collapsed .app-nav-link,
.app-navigation--collapsed .app-account {
  justify-content: center;
  padding-inline: 0;
}

.app-navigation--collapsed .app-account {
  width: 100%;
}
</style>
