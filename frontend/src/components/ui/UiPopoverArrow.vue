<script setup lang="ts">
import { PopoverArrow, TooltipArrow } from 'reka-ui'
defineProps<{ bordered?: boolean; tooltip?: boolean }>()
</script>
<template>
  <component
    :is="tooltip ? TooltipArrow : PopoverArrow"
    class="ui-popover-arrow"
    :width="14"
    :height="7"
    aria-hidden="true"
  >
    <!-- 底部填色延伸进浮层，遮住边框接缝；描边仅沿两条斜边，不封闭底边。 -->
    <path d="M0 -2V0L4.58579 4.58579C5.36683 5.36683 6.63316 5.36684 7.41421 4.58579L12 0V-2Z" />
    <path
      v-if="bordered"
      class="ui-popover-arrow-outline"
      d="M0 0L4.58579 4.58579C5.36683 5.36683 6.63316 5.36684 7.41421 4.58579L12 0"
      vector-effect="non-scaling-stroke"
    />
  </component>
</template>
<style scoped>
:global(.ui-popover-arrow) {
  overflow: visible;
  fill: var(--overlay);
  pointer-events: none;
}

:global(.ui-popover-arrow-outline) {
  fill: none;
  stroke: var(--border);
  stroke-width: 1;
}
</style>
