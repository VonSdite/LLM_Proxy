<script setup lang="ts">
import { onBeforeUnmount, ref, useId } from 'vue'
import { PopoverAnchor, PopoverContent, PopoverPortal, PopoverRoot } from 'reka-ui'
import UiIcon from './UiIcon.vue'
withDefaults(defineProps<{ label?: string }>(), { label: '查看说明' })
const open = ref(false)
const id = useId()
let timer: ReturnType<typeof setTimeout> | undefined
function show() {
  clearTimeout(timer)
  timer = setTimeout(() => {
    open.value = true
  }, 120)
}
function hide() {
  clearTimeout(timer)
  timer = setTimeout(() => {
    open.value = false
  }, 160)
}
function hold() {
  clearTimeout(timer)
}
onBeforeUnmount(() => clearTimeout(timer))
</script>
<template>
  <PopoverRoot v-model:open="open">
    <PopoverAnchor as-child>
      <button
        type="button"
        class="ui-help"
        :aria-label="label"
        :aria-describedby="open ? id : undefined"
        :aria-expanded="open"
        @pointerenter="show"
        @pointerleave="hide"
        @focus="show"
        @blur="hide"
        @click="open = !open"
      >
        <UiIcon name="help" :size="14" />
      </button>
    </PopoverAnchor>
    <PopoverPortal>
      <PopoverContent
        as-child
        class="ui-help-content"
        :side-offset="8"
        :collision-padding="12"
        @open-auto-focus.prevent
        @close-auto-focus.prevent
        @pointerenter="hold"
        @pointerleave="hide"
        @focusin="hold"
        @focusout="hide"
      >
        <div :id="id"><slot /></div>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
<style scoped>
.ui-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 2px;
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--muted);
}

.ui-help:hover {
  color: var(--link);
}

@media (pointer: coarse) {
  .ui-help {
    min-width: var(--control);
    min-height: var(--control);
  }
}

:global(.ui-help-content) {
  z-index: var(--z-popup);
  width: max-content;
  max-width: min(360px, calc(100vw - 24px));
  padding: var(--space-3);
  border-radius: var(--radius-control);
  background: var(--overlay);
  color: var(--text);
  box-shadow: var(--shadow-overlay);
  font-size: var(--text-xs);
  line-height: 1.65;
  overflow-wrap: anywhere;
}
</style>
