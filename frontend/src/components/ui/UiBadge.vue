<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: 'neutral' | 'success' | 'danger' | 'warning' | 'info'
    size?: 'sm' | 'md'
  }>(),
  {
    variant: 'neutral',
    size: 'sm',
  },
)
</script>
<template>
  <span class="ui-badge" :class="[`ui-badge--${variant}`, `ui-badge--${size}`]"><slot /></span>
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

.ui-badge--md {
  max-width: 100%;
  padding: 3px var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-control);
  font-size: var(--text-base);
}

.ui-badge--md.ui-badge--neutral {
  background: var(--tag-background);
  color: var(--tag-text);
}
</style>
