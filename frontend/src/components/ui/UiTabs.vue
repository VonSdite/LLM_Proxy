<script setup lang="ts">
import { TabsList, TabsRoot, TabsTrigger } from 'reka-ui'
import type { SelectOption } from './types'
withDefaults(
  defineProps<{ modelValue: string; items: SelectOption[]; label?: string; segmented?: boolean }>(),
  { label: '页面分区' },
)
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
</script>
<template>
  <TabsRoot
    class="ui-tabs-root"
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', String($event))"
  >
    <TabsList class="ui-tabs" :class="{ 'ui-tabs--segmented': segmented }" :aria-label="label">
      <TabsTrigger
        v-for="item in items"
        :key="item.value"
        class="ui-tab"
        :value="item.value"
        :disabled="item.disabled"
      >
        {{ item.label }}
      </TabsTrigger>
    </TabsList>
    <slot />
  </TabsRoot>
</template>
<style scoped>
.ui-tabs-root {
  min-width: 0;
  max-width: 100%;
}

.ui-tabs {
  display: flex;
  gap: var(--space-1);
  max-width: 100%;
  overflow-x: auto;
  border-bottom: 1px solid var(--border);
}

.ui-tab {
  position: relative;
  padding: var(--space-2) var(--space-4);
  min-height: var(--control);
  border: 0;
  background: transparent;
  border-radius: var(--radius-control) var(--radius-control) 0 0;
  color: var(--muted);
  font-size: var(--text-sm);
  white-space: nowrap;
}

.ui-tab:hover {
  background: var(--hover);
}

.ui-tab[data-state='active'] {
  color: var(--link);
}

.ui-tab[data-state='active']::after {
  content: '';
  position: absolute;
  left: var(--space-3);
  right: var(--space-3);
  bottom: 0;
  height: 2px;
  background: var(--link);
}

.ui-tabs--segmented {
  width: fit-content;
  background: var(--input);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  padding: 3px;
}

.ui-tabs--segmented .ui-tab {
  padding: var(--space-1) var(--space-3);
  min-height: 28px;
  border-radius: 6px;
}

.ui-tabs--segmented .ui-tab[data-state='active'] {
  background: var(--overlay);
  color: var(--text);
}

.ui-tabs--segmented .ui-tab::after {
  display: none;
}

@media (pointer: coarse) {
  .ui-tabs--segmented .ui-tab {
    min-height: var(--control);
  }
}
</style>
