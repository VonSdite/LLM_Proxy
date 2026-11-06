<script setup lang="ts">
import { RouterView } from 'vue-router'
import { useSession } from '@/stores/session'
import AppShell from './AppShell.vue'
import { UiAlert, UiButton, UiEmptyState, UiToast } from '@/components/ui'
const session = useSession()
function retry() {
  window.location.reload()
}
</script>
<template>
  <div v-if="session.error" class="app-bootstrap-error">
    <UiAlert>{{ session.error }}</UiAlert>
    <UiButton @click="retry">重试</UiButton>
  </div>
  <UiEmptyState v-else-if="!session.context" loading />
  <template v-else>
    <RouterView v-if="$route.meta.public" />
    <AppShell v-else><RouterView /></AppShell>
  </template>
  <UiToast />
</template>
<style scoped>
.app-bootstrap-error {
  display: grid;
  justify-items: start;
  gap: var(--space-4);
  max-width: 600px;
  margin: var(--space-8) auto;
  padding: var(--space-4);
}
</style>
