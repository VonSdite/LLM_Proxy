<script setup lang="ts">
import UiIconButton from './UiIconButton.vue'
defineProps<{ label: string; disabled?: boolean }>()
</script>
<template>
  <UiIconButton
    class="ui-drag-handle"
    icon="grip"
    size="sm"
    :label="label"
    :aria-disabled="disabled || undefined"
    :draggable="!disabled"
    aria-description="可拖动排序，也可用上下方向键移动，Home 置顶，End 置底。"
    aria-keyshortcuts="ArrowUp ArrowDown Home End"
  />
</template>
<style scoped>
.ui-drag-handle {
  cursor: grab;
  touch-action: none;
  color: var(--muted);
}

.ui-drag-handle:active:not([aria-disabled]) {
  cursor: grabbing;
}

.ui-drag-handle[aria-disabled] {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
