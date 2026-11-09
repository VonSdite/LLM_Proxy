<script setup lang="ts">
import UiButton from './UiButton.vue'
defineProps<{ selected: boolean; disabled?: boolean; title?: string }>()
const emit = defineEmits<{ select: [] }>()
</script>
<template>
  <div class="ui-selectable-row" :data-selected="selected || undefined">
    <UiButton
      class="ui-selectable-row-trigger"
      variant="ghost"
      :aria-pressed="selected"
      :disabled="disabled"
      :title="title"
      @click="emit('select')"
    >
      <slot />
    </UiButton>
    <div v-if="$slots.actions" class="ui-selectable-row-actions"><slot name="actions" /></div>
  </div>
</template>
<style scoped>
.ui-selectable-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  min-width: 0;
  padding-right: var(--space-1);
  border-radius: var(--radius-control);
  transition: background var(--motion);
}

.ui-selectable-row:hover {
  background: var(--hover);
}

.ui-selectable-row[data-selected] {
  background: var(--selected);
}

.ui-selectable-row-trigger {
  min-width: 0;
  justify-content: flex-start;
  border-color: transparent;
}

.ui-selectable-row-trigger:hover:not(:disabled) {
  background: transparent;
}

.ui-selectable-row[data-selected] .ui-selectable-row-trigger {
  color: var(--link);
}

.ui-selectable-row-actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}
</style>
