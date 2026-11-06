<script setup lang="ts">
import { UiButton } from '@/components/ui'
defineProps<{ title: string; saving?: boolean }>()
const emit = defineEmits<{ save: [] }>()
</script>
<template>
  <form class="setting-section surface" @submit.prevent="emit('save')">
    <header class="section-heading">
      <h2>{{ title }}</h2>
      <UiButton type="submit" size="sm" :loading="saving" icon="check">保存</UiButton>
    </header>
    <slot />
  </form>
</template>
<style scoped>
.setting-section {
  padding: var(--space-6);
  min-width: 0;
}

@media (width <= 600px) {
  .setting-section {
    padding: var(--space-4);
  }
}
</style>
