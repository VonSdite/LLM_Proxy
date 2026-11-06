<script setup lang="ts" generic="T extends object">
import { useRowDrag, type RowMove } from './drag'
import UiIcon from './UiIcon.vue'
import UiIconButton from './UiIconButton.vue'
const props = defineProps<{
  items: T[]
  rowKey: (row: T) => string
  disabled?: boolean
  label: string
}>()
const emit = defineEmits<{ reorder: [move: RowMove] }>()
const { dragOver, startDrag, endDrag, drop, over } = useRowDrag(
  () => !props.disabled,
  (move) => emit('reorder', move),
)
function move(index: number, offset: number) {
  const source = props.items[index]
  const target = props.items[index + offset]
  if (source && target)
    emit('reorder', { id: props.rowKey(source), targetId: props.rowKey(target), after: offset > 0 })
}
</script>
<template>
  <div class="ui-sortable-list" role="list" :aria-label="label">
    <div
      v-for="(item, index) in items"
      :key="rowKey(item)"
      class="ui-sortable-row surface"
      role="listitem"
      :data-drag-over="dragOver === rowKey(item) || undefined"
      @dragover="over($event, rowKey(item))"
      @drop="drop($event, rowKey(item))"
    >
      <div class="ui-sortable-controls">
        <button
          class="ui-sortable-handle"
          type="button"
          :draggable="!disabled"
          :disabled="disabled"
          :aria-label="`拖动第 ${index + 1} 项`"
          @dragstart="startDrag($event, rowKey(item))"
          @dragend="endDrag"
        >
          <UiIcon name="grip" :size="14" />
        </button>
        <UiIconButton
          icon="up"
          :label="`上移第 ${index + 1} 项`"
          :disabled="disabled || index === 0"
          @click="move(index, -1)"
        />
        <UiIconButton
          icon="arrowDown"
          :label="`下移第 ${index + 1} 项`"
          :disabled="disabled || index === items.length - 1"
          @click="move(index, 1)"
        />
      </div>
      <div class="ui-sortable-content"><slot :item="item" :index="index" /></div>
    </div>
  </div>
</template>
<style scoped>
.ui-sortable-list {
  display: grid;
  gap: var(--space-3);
}

.ui-sortable-row {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-3);
  min-width: 0;
}

.ui-sortable-row[data-drag-over] {
  box-shadow: inset 0 2px var(--link);
}

.ui-sortable-controls {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.ui-sortable-content {
  min-width: 0;
  flex: 1;
}

.ui-sortable-handle {
  border: 0;
  background: transparent;
  color: var(--muted);
  cursor: grab;
  padding: var(--space-1);
}

@media (pointer: coarse) {
  .ui-sortable-handle {
    min-width: var(--control);
    min-height: var(--control);
  }
}
</style>
