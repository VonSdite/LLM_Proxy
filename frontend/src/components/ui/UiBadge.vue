<script setup lang="ts">
withDefaults(defineProps<{ variant?: 'neutral' | 'success' | 'danger' | 'warning' | 'info' }>(), {
  variant: 'neutral',
})
</script>
<template>
  <span class="ui-badge" :class="`ui-badge--${variant}`"><slot /></span>
</template>
<style scoped>
.ui-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  border-radius: var(--space-1);
  padding: 2px 6px;
  font-size: var(--text-xs);
  line-height: 1.5;
  white-space: nowrap;
}

.ui-badge--neutral {
  color: var(--muted);
  background: var(--hover);
}

.ui-badge--success {
  color: var(--success);
  background: var(--success-surface);
}

.ui-badge--danger {
  color: var(--danger);
  background: var(--danger-surface);
}

.ui-badge--warning {
  color: var(--warning);
  background: var(--warning-surface);
}

.ui-badge--info {
  color: var(--link);
  background: var(--selected);
}
</style>
