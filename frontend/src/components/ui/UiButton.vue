<script setup lang="ts">
import UiIcon, { type IconName } from './UiIcon.vue'
withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
    size?: 'sm' | 'md'
    icon?: IconName
    loading?: boolean
    disabled?: boolean
    type?: 'button' | 'submit' | 'reset'
  }>(),
  { variant: 'secondary', size: 'md', type: 'button' },
)
</script>
<template>
  <button
    :type="type"
    class="ui-button"
    :class="[`ui-button--${variant}`, `ui-button--${size}`]"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
  >
    <UiIcon v-if="loading || icon" :name="loading ? 'loading' : icon!" />
    <slot />
  </button>
</template>
<style scoped>
.ui-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  min-height: var(--control);
  padding: 0 var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--surface);
  color: var(--text);
  font-size: var(--text-sm);
  font-weight: 550;
  white-space: nowrap;
  transition:
    background var(--motion),
    border-color var(--motion);
}

.ui-button:hover:not(:disabled) {
  background: var(--hover);
}

.ui-button--sm {
  min-height: var(--control-sm);
}

.ui-button--primary {
  background: var(--primary);
  color: var(--on-primary);
  border-color: transparent;
}

.ui-button--primary:hover:not(:disabled) {
  background: color-mix(in srgb, var(--primary) 90%, var(--text));
}

.ui-button--ghost {
  border-color: transparent;
  background: transparent;
}

.ui-button--danger {
  background: var(--danger-surface);
  border-color: transparent;
  color: var(--danger);
}

.ui-button--danger:hover:not(:disabled) {
  background: color-mix(in srgb, var(--danger-surface) 85%, var(--danger));
}

.ui-button:disabled {
  opacity: 0.5;
}
</style>
