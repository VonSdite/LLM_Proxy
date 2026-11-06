<script setup lang="ts">
import { ToastClose, ToastDescription, ToastProvider, ToastRoot, ToastViewport } from 'reka-ui'
import { dismissToast, toasts } from './toast'
import UiIcon from './UiIcon.vue'
</script>
<template>
  <ToastProvider :duration="4500">
    <ToastRoot
      v-for="toast in toasts"
      :key="toast.id"
      class="ui-toast"
      :class="`ui-toast--${toast.variant}`"
      @update:open="!$event && dismissToast(toast.id)"
    >
      <UiIcon :name="toast.variant === 'success' ? 'check' : 'alert'" />
      <ToastDescription>
        {{ toast.message }}
      </ToastDescription>
      <ToastClose class="ui-toast-close" aria-label="关闭通知">
        <UiIcon name="close" :size="14" />
      </ToastClose>
    </ToastRoot>
    <ToastViewport class="ui-toast-viewport" />
  </ToastProvider>
</template>
<style scoped>
:global(.ui-toast-viewport) {
  position: fixed;
  right: var(--space-6);
  bottom: var(--space-6);
  z-index: var(--z-toast);
  display: grid;
  gap: var(--space-2);
  width: min(380px, calc(100% - 48px));
  padding: 0;
  margin: 0;
  list-style: none;
  outline: none;
}

:global(.ui-toast) {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-group);
  background: var(--overlay);
  box-shadow: var(--shadow-overlay);
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}

:global(.ui-toast--success > svg) {
  color: var(--success);
}

:global(.ui-toast--danger > svg) {
  color: var(--danger);
}

:global(.ui-toast--info > svg) {
  color: var(--link);
}

:global(.ui-toast--warning > svg) {
  color: var(--warning);
}

:global(.ui-toast-close) {
  display: flex;
  align-items: center;
  margin-left: auto;
  padding: var(--space-1);
  border: 0;
  background: transparent;
  color: var(--muted);
}

@media (pointer: coarse) {
  :global(.ui-toast-close) {
    min-width: var(--control);
    min-height: var(--control);
    justify-content: center;
  }
}
</style>
