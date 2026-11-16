<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import UiIconButton from './UiIconButton.vue'
import UiTooltip from './UiTooltip.vue'
defineProps<{ count: number; disabled?: boolean; embedded?: boolean }>()
const emit = defineEmits<{ clear: [] }>()
const root = ref<HTMLElement>()
const previousFocus = document.activeElement
onBeforeUnmount(() => {
  if (
    root.value?.contains(document.activeElement) &&
    previousFocus instanceof HTMLElement &&
    previousFocus.isConnected
  ) {
    previousFocus.focus()
  }
})
</script>
<template>
  <div
    ref="root"
    class="ui-selection-bar"
    :class="{ 'ui-selection-bar-embedded': embedded }"
    role="region"
    aria-label="批量操作"
  >
    <div class="ui-selection-bar-summary">
      <span class="number" aria-live="polite">已选 {{ count }} 项</span>
      <UiTooltip text="清空选择">
        <UiIconButton
          class="ui-selection-bar-clear"
          icon="close"
          size="xs"
          label="清空选择"
          title=""
          :disabled="disabled"
          @click="emit('clear')"
        />
      </UiTooltip>
    </div>
    <div class="ui-selection-bar-actions"><slot /></div>
  </div>
</template>
<style scoped>
.ui-selection-bar {
  container-type: inline-size;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-group);
  background: var(--surface-soft);
  font-size: var(--text-base);
}

.ui-selection-bar-summary,
.ui-selection-bar-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.ui-selection-bar-summary {
  gap: var(--space-1);
  flex-wrap: nowrap;
  white-space: nowrap;
  color: var(--link);
  font-weight: var(--weight-emphasis);
}

.ui-selection-bar-clear {
  color: var(--muted);
}

.ui-selection-bar-embedded {
  flex: none;
  flex-wrap: nowrap;
  min-height: var(--control);
  padding: 0;
  border: 0;
  border-radius: 0;
  container-type: normal;
  background: transparent;
}

.ui-selection-bar-embedded .ui-selection-bar-actions {
  flex-wrap: nowrap;
  width: auto;
}

@container (height >= 80px) {
  .ui-selection-bar-embedded {
    flex-wrap: wrap;
    max-width: 100%;
  }

  .ui-selection-bar-embedded .ui-selection-bar-actions {
    flex-wrap: wrap;
  }
}

@container (max-width: 560px) {
  .ui-selection-bar-actions {
    order: 1;
    width: 100%;
  }
}
</style>
