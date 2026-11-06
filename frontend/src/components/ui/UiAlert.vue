<script setup lang="ts">
import UiIcon from './UiIcon.vue'
withDefaults(defineProps<{ variant?: 'danger' | 'warning' | 'info' | 'success' }>(), {
  variant: 'danger',
})
</script>
<template>
  <div
    class="ui-alert"
    :class="`ui-alert--${variant}`"
    :role="variant === 'danger' ? 'alert' : 'status'"
  >
    <UiIcon :name="variant === 'success' ? 'check' : 'alert'" />
    <div><slot /></div>
  </div>
</template>
<style scoped>
.ui-alert {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-control);
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}

.ui-alert svg {
  margin-top: 2px;
}

.ui-alert--danger {
  color: var(--danger);
  background: var(--danger-surface);
}

.ui-alert--warning {
  color: var(--warning);
  background: var(--warning-surface);
}

.ui-alert--info {
  color: var(--link);
  background: var(--selected);
}

.ui-alert--success {
  color: var(--success);
  background: var(--success-surface);
}
</style>
