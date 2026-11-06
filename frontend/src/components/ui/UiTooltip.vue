<script setup lang="ts">
import {
  TooltipContent,
  TooltipPortal,
  TooltipProvider,
  TooltipRoot,
  TooltipTrigger,
} from 'reka-ui'
defineProps<{ text: string }>()
</script>
<template>
  <TooltipProvider :delay-duration="120" :skip-delay-duration="160">
    <TooltipRoot>
      <TooltipTrigger as-child><slot /></TooltipTrigger>
      <TooltipPortal>
        <TooltipContent :side-offset="8" class="ui-tooltip">
          {{ text }}
        </TooltipContent>
      </TooltipPortal>
    </TooltipRoot>
  </TooltipProvider>
</template>
<style scoped>
:global(.ui-tooltip) {
  z-index: var(--z-popup);
  max-width: 280px;
  border-radius: var(--radius-control);
  background: var(--overlay);
  color: var(--text);
  box-shadow: var(--shadow-overlay);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
}
</style>
