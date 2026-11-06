<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from 'reka-ui'
import UiIconButton from './UiIconButton.vue'
const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    description?: string
    size?: 'sm' | 'md' | 'lg' | 'xl'
    busy?: boolean
    beforeClose?: () => boolean | Promise<boolean>
  }>(),
  { size: 'md' },
)
const emit = defineEmits<{ 'update:open': [value: boolean]; closed: [] }>()
const closing = ref(false)
let returnFocus: HTMLElement | null = null
watch(
  () => props.open,
  (open) => {
    if (open)
      returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  },
  { immediate: true },
)
function restoreFocus(event: Event) {
  event.preventDefault()
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true })
}
async function requestClose(value = false) {
  if (value || props.busy || closing.value) return
  closing.value = true
  try {
    if (props.beforeClose && !(await props.beforeClose())) return
    emit('update:open', false)
    emit('closed')
  } finally {
    closing.value = false
  }
}
defineExpose({ requestClose })
</script>
<template>
  <DialogRoot :open="open" @update:open="requestClose">
    <DialogPortal>
      <DialogOverlay class="ui-dialog-overlay" />
      <DialogContent
        class="ui-dialog"
        :class="`ui-dialog--${size}`"
        @escape-key-down="busy && $event.preventDefault()"
        @interact-outside="busy && $event.preventDefault()"
        @close-auto-focus="restoreFocus"
      >
        <header class="ui-dialog-header">
          <div>
            <DialogTitle class="ui-dialog-title">{{ title }}</DialogTitle>
            <DialogDescription v-if="description" class="ui-dialog-description">
              {{ description }}
            </DialogDescription>
            <DialogDescription v-else class="sr-only">{{ title }}</DialogDescription>
          </div>
          <UiIconButton icon="close" label="关闭" :disabled="busy" @click="requestClose()" />
        </header>
        <div class="ui-dialog-body"><slot /></div>
        <footer v-if="$slots.footer" class="ui-dialog-footer">
          <slot name="footer" />
        </footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
<style scoped>
.ui-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog);
  background: var(--backdrop);
}

.ui-dialog {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: var(--z-dialog);
  display: flex;
  flex-direction: column;
  width: min(640px, calc(100vw - 32px));
  max-height: calc(100dvh - 48px);
  border: 1px solid var(--border);
  border-radius: var(--radius-dialog);
  background: var(--surface);
  box-shadow: var(--shadow-dialog);
  outline: none;
}

.ui-dialog--sm {
  width: min(440px, calc(100vw - 32px));
}

.ui-dialog--lg {
  width: min(900px, calc(100vw - 32px));
}

.ui-dialog--xl {
  width: min(1200px, calc(100vw - 32px));
}

.ui-dialog-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-6);
}

.ui-dialog-title {
  font-size: var(--text-section);
  font-weight: 600;
  padding-top: var(--space-1);
}

.ui-dialog-description {
  color: var(--muted);
  font-size: var(--text-sm);
  margin-top: var(--space-1);
}

.ui-dialog-body {
  padding: var(--space-2) var(--space-6) var(--space-6);
  overflow: auto;
  min-height: 0;
  overscroll-behavior: contain;
}

.ui-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-4) var(--space-6);
  border-top: 1px solid var(--border);
}

@media (width <= 600px) {
  .ui-dialog-header,
  .ui-dialog-body,
  .ui-dialog-footer {
    padding-left: var(--space-4);
    padding-right: var(--space-4);
  }
}
</style>
