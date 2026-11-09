<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useSession } from '@/stores/session'
import { useTheme } from '@/stores/theme'
import { UiButton, UiDialog, UiIcon, UiIconButton, UiTooltip } from '@/components/ui'
import AppNavigation from './AppNavigation.vue'

const session = useSession()
const theme = useTheme()
const route = useRoute()
const desktopCollapsed = ref(false)
const compactCollapsed = ref(true)
const menuOpen = ref(false)
const mobileQuery = window.matchMedia('(max-width: 900px)')
const compactQuery = window.matchMedia('(max-width: 1280px)')
const mobile = ref(mobileQuery.matches)
const compact = ref(compactQuery.matches)
const collapsed = computed(() => (compact.value ? compactCollapsed.value : desktopCollapsed.value))
function updateViewport() {
  // 窄桌面的手动展开仅在当前宽度区间内保留，宽屏偏好独立保存。
  if (mobile.value !== mobileQuery.matches || compact.value !== compactQuery.matches) {
    compactCollapsed.value = true
  }
  mobile.value = mobileQuery.matches
  compact.value = compactQuery.matches
  if (!mobile.value) menuOpen.value = false
}
function toggleSidebar() {
  if (compact.value) compactCollapsed.value = !compactCollapsed.value
  else desktopCollapsed.value = !desktopCollapsed.value
}
mobileQuery.addEventListener('change', updateViewport)
compactQuery.addEventListener('change', updateViewport)
onBeforeUnmount(() => {
  mobileQuery.removeEventListener('change', updateViewport)
  compactQuery.removeEventListener('change', updateViewport)
})
watch(
  () => route.fullPath,
  () => {
    menuOpen.value = false
  },
)
</script>
<template>
  <div class="app-shell" :class="{ 'app-shell--collapsed': collapsed }">
    <a href="#main-content" class="skip-link">跳转到内容</a>
    <aside v-if="!mobile" class="app-sidebar" aria-label="侧边栏">
      <div class="app-sidebar-header">
        <RouterLink v-if="!collapsed" to="/" class="app-brand" aria-label="LLM Proxy">
          <UiIcon name="network" :size="22" />
          <span class="app-brand-text">
            <strong>LLM Proxy</strong>
            <small>v{{ session.context?.version }}</small>
          </span>
        </RouterLink>
        <UiTooltip :text="collapsed ? '展开侧栏' : '收起侧栏'">
          <UiButton
            variant="ghost"
            :icon="collapsed ? 'expand' : 'collapse'"
            class="app-sidebar-toggle"
            :aria-label="collapsed ? '展开侧栏' : '收起侧栏'"
            :aria-expanded="!collapsed"
            @click="toggleSidebar"
          />
        </UiTooltip>
      </div>
      <AppNavigation :collapsed="collapsed" />
    </aside>
    <header v-else class="app-mobile-header">
      <UiIconButton
        icon="menu"
        label="打开导航"
        :aria-expanded="menuOpen"
        aria-haspopup="dialog"
        @click="menuOpen = true"
      />
      <RouterLink to="/" class="app-mobile-brand">LLM Proxy</RouterLink>
      <UiIconButton
        :icon="theme.theme === 'dark' ? 'sun' : 'moon'"
        label="切换主题"
        @click="theme.toggle"
      />
    </header>
    <main id="main-content" class="app-content" tabindex="-1"><slot /></main>
    <UiDialog v-if="mobile" v-model:open="menuOpen" title="LLM Proxy" placement="left">
      <AppNavigation />
    </UiDialog>
  </div>
</template>
<style scoped>
.app-shell {
  --app-sidebar-width: 216px;
  --app-sticky-offset: 24px;

  display: grid;
  grid-template-columns: var(--app-sidebar-width) minmax(0, 1fr);
  min-height: 100dvh;
}

.app-shell--collapsed {
  --app-sidebar-width: 72px;
}

.app-sidebar {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  align-self: start;
  gap: var(--space-5);
  height: 100dvh;
  min-height: 0;
  padding: var(--space-5) var(--space-3) var(--space-3);
  border-right: 1px solid var(--divider);
  background: var(--surface);
}

.app-sidebar-header {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  min-height: 44px;
}

.app-brand {
  display: flex;
  flex: 1;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  min-height: 44px;
  padding-left: var(--space-2);
  border-radius: var(--radius-control);
  color: var(--text);
  white-space: nowrap;
}

.app-brand:hover {
  text-decoration: none;
}

.app-brand > svg {
  color: var(--link);
}

.app-brand-text {
  display: grid;
  gap: 2px;
}

.app-brand strong {
  font-size: var(--text-section);
}

.app-brand small {
  color: var(--muted);
}

.app-sidebar-toggle {
  flex: none;
  width: var(--control);
  padding: 0;
  color: var(--muted);
}

.app-content {
  width: 100%;
  min-width: 0;
  padding: var(--space-6) var(--space-6) var(--space-8);
  outline: none;
}

.app-mobile-header {
  position: sticky;
  top: 0;
  z-index: var(--z-header);
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--divider);
  background: var(--surface);
}

.app-mobile-brand {
  margin-right: auto;
  color: var(--text);
  font-size: var(--text-section);
  font-weight: var(--weight-heading);
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

@media (width <= 900px) {
  .app-shell {
    --app-sticky-offset: calc(40px + var(--control));

    display: block;
  }

  .app-content {
    padding: var(--space-5) var(--space-4) var(--space-6);
  }
}
</style>
