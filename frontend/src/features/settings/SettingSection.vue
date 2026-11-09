<script setup lang="ts">
import { UiAlert, UiButton } from '@/components/ui'
defineProps<{
  title: string
  saving?: boolean
  dirty?: boolean
  disabled?: boolean
  error?: string
}>()
const emit = defineEmits<{ save: [] }>()
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
      <UiButton
        type="submit"
        size="sm"
        :variant="dirty ? 'primary' : 'secondary'"
        :disabled="!dirty || disabled"
        :loading="saving"
      >
        保存
      </UiButton>
    </header>
    <fieldset :disabled="saving"><slot /></fieldset>
    <UiAlert v-if="error" class="setting-error">{{ error }}</UiAlert>
  </form>
</template>
<style scoped>
.setting-section {
  padding: var(--space-6);
  min-width: 0;
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

@media (width <= 600px) {
  .setting-section {
    padding: var(--space-4);
  }
}
</style>
