<script setup lang="ts">
import { ref } from 'vue'
import {
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuRoot,
  DropdownMenuTrigger,
} from 'reka-ui'
import UiIcon, { type IconName } from './UiIcon.vue'

withDefaults(
  defineProps<{
    label: string
    items: { value: string; label: string; icon?: IconName; danger?: boolean; disabled?: boolean }[]
    side?: 'top' | 'right' | 'bottom' | 'left'
    align?: 'start' | 'center' | 'end'
  }>(),
  { side: 'bottom', align: 'start' },
)
const emit = defineEmits<{ select: [value: string] }>()
const trigger = ref<InstanceType<typeof DropdownMenuTrigger>>()
function select(value: string) {
  // 后续弹窗以仍在页面上的菜单入口作为返回焦点。
  const element = trigger.value?.$el
  if (element instanceof HTMLElement) element.focus({ preventScroll: true })
  emit('select', value)
}
</script>
<template>
  <DropdownMenuRoot :modal="false">
    <DropdownMenuTrigger ref="trigger" as-child><slot /></DropdownMenuTrigger>
    <DropdownMenuPortal>
      <DropdownMenuContent
        class="ui-dropdown-menu"
        :aria-labelledby="undefined"
        :aria-label="label"
        :side="side"
        :align="align"
        :side-offset="8"
        :collision-padding="12"
        loop
      >
        <DropdownMenuItem
          v-for="item in items"
          :key="item.value"
          class="ui-dropdown-menu-item"
          :class="{ 'ui-dropdown-menu-item--danger': item.danger }"
          :disabled="item.disabled"
          @select="select(item.value)"
        >
          <UiIcon v-if="item.icon" :name="item.icon" />
          {{ item.label }}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenuPortal>
  </DropdownMenuRoot>
</template>
<style scoped>
:global(.ui-dropdown-menu) {
  z-index: var(--z-popup);
  min-width: 180px;
  max-width: min(280px, calc(100vw - 24px));
  padding: var(--space-1);
  border-radius: var(--radius-group);
  background: var(--overlay);
  color: var(--text);
  box-shadow: var(--shadow-overlay);
  outline: none;
}

:global(.ui-dropdown-menu-item) {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-control);
  font-size: var(--text-base);
  cursor: pointer;
  outline: none;
  user-select: none;
}

:global(.ui-dropdown-menu-item[data-highlighted]) {
  background: var(--hover);
}

:global(.ui-dropdown-menu-item--danger) {
  color: var(--danger);
}

:global(.ui-dropdown-menu-item--danger[data-highlighted]) {
  background: var(--danger-surface);
}

:global(.ui-dropdown-menu-item[data-disabled]) {
  opacity: 0.5;
  pointer-events: none;
}
</style>
