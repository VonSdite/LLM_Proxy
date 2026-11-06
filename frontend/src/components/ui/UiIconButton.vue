<script setup lang="ts">
import UiButton from './UiButton.vue'
import type { IconName } from './UiIcon.vue'
withDefaults(
  defineProps<{
    icon: IconName
    label: string
    disabled?: boolean
    loading?: boolean
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  }>(),
  { variant: 'ghost' },
)
</script>
<template>
  <UiButton
    :icon="icon"
    :aria-label="label"
    :title="label"
    :disabled="disabled"
    :loading="loading"
    :variant="variant"
    class="ui-icon-button"
  />
</template>
<style scoped>
.ui-icon-button {
  width: var(--control);
  padding: 0;
  flex: none;
}
</style>
