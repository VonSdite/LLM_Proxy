<script setup lang="ts">
import { useId } from 'vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import UiIconButton from './UiIconButton.vue'
import UiPopoverArrow from './UiPopoverArrow.vue'

defineProps<{ open: boolean; title: string }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
const titleId = useId()
function close() {
  emit('update:open', false)
}
function focusInput(event: Event) {
  const target = event.target
  if (!(target instanceof HTMLElement)) return
  const input = target.querySelector<HTMLElement>('input:not(:disabled), textarea:not(:disabled)')
  if (input) {
    event.preventDefault()
    input.focus()
  }
}
</script>
<template>
  <PopoverRoot :open="open" modal @update:open="emit('update:open', $event)">
    <PopoverTrigger as-child><slot name="trigger" /></PopoverTrigger>
    <PopoverPortal>
      <PopoverContent
        as-child
        class="ui-popover"
        align="end"
        :side-offset="6"
        :arrow-padding="12"
        :collision-padding="12"
        @open-auto-focus="focusInput"
        @escape-key-down="$event.stopPropagation()"
      >
        <div :aria-labelledby="titleId">
          <UiPopoverArrow bordered />
          <div class="ui-popover-panel">
            <header class="ui-popover-header">
              <h3 :id="titleId">{{ title }}</h3>
              <UiIconButton icon="close" :label="`关闭${title}`" @click="close" />
            </header>
            <div class="ui-popover-body"><slot /></div>
            <footer v-if="$slots.footer" class="ui-popover-footer">
              <slot name="footer" :close="close" />
            </footer>
          </div>
        </div>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
<style scoped>
:global(.ui-popover) {
  z-index: var(--z-popup);
  width: min(320px, calc(100vw - 24px));
  border: 1px solid var(--border);
  border-radius: var(--radius-group);
  background: var(--overlay);
  color: var(--text);
  box-shadow: var(--shadow-overlay);
}

:global(.ui-popover-panel) {
  max-height: calc(var(--reka-popover-content-available-height, 100dvh) - 2px);
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: var(--space-4);
  border-radius: inherit;
}

:global(.ui-popover-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-3);
}

:global(.ui-popover-header h3) {
  color: var(--heading);
  font-size: var(--text-base);
  font-weight: var(--weight-label);
}

:global(.ui-popover-body) {
  display: grid;
  gap: var(--space-3);
}

:global(.ui-popover-footer) {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-4);
}
</style>
