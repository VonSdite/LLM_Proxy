<script setup lang="ts">
import { UiAlert, UiButton } from '@/components/ui'
defineProps<{
  title: string
  saving?: boolean
  error?: string
}>()
const emit = defineEmits<{ save: []; retry: [] }>()
</script>
<template>
  <form
    class="setting-section surface"
    :aria-label="title"
    :aria-busy="saving || undefined"
    @submit.prevent="emit('save')"
  >
    <header class="section-heading">
      <h2>{{ title }}</h2>
    </header>
    <fieldset><slot /></fieldset>
    <UiAlert v-if="error" class="setting-error">
      <span>未保存：{{ error }}</span>
      <UiButton size="sm" :loading="saving" @click="emit('retry')">重试</UiButton>
    </UiAlert>
  </form>
</template>
<style scoped>
.setting-section {
  padding: var(--space-5);
  min-width: 0;
}

.section-heading {
  min-height: var(--control);
}

fieldset {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.setting-error {
  margin-top: var(--space-4);
}

h2 {
  margin: 0;
  font-size: var(--text-section);
  font-weight: 600;
}

@media (width <= 600px) {
  .setting-section {
    padding: var(--space-4);
  }
}
</style>
