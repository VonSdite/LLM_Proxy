<script setup lang="ts" generic="T extends object">
import { useRowDrag, type RowMove } from './drag'
import UiDragHandle from './UiDragHandle.vue'
const props = defineProps<{
  items: T[]
  rowKey: (row: T) => string
  disabled?: boolean
  label: string
}>()
const emit = defineEmits<{ reorder: [move: RowMove] }>()
const { setRoot, dragging, startDrag, endDrag, drop, over, leave, startPointer, keydown } =
  useRowDrag(
    () => !props.disabled,
    () => props.items.map((item) => ({ id: props.rowKey(item) })),
    (move) => emit('reorder', move),
  )
</script>
<template>
  <div
    :ref="setRoot"
    class="ui-sortable-list ui-sort-root"
    role="list"
    :aria-label="label"
    @dragover="over"
    @dragleave="leave"
    @drop="drop"
  >
    <div
      v-for="(item, index) in items"
      :key="rowKey(item)"
      class="ui-sortable-row ui-sort-item surface"
      role="listitem"
      :data-sort-id="rowKey(item)"
      :data-dragging="dragging === rowKey(item) || undefined"
    >
      <UiDragHandle
        :disabled="disabled"
        :label="`拖动第 ${index + 1} 项`"
        @dragstart="startDrag($event, rowKey(item))"
        @dragend="endDrag"
        @pointerdown="startPointer($event, rowKey(item))"
        @keydown="keydown($event, rowKey(item))"
      />
      <div class="ui-sortable-content"><slot :item="item" :index="index" /></div>
    </div>
    <div v-if="dragging" class="ui-sort-tail" aria-hidden="true" />
  </div>
</template>
<style scoped>
.ui-sortable-list {
  display: grid;
  gap: var(--space-3);
}

.ui-sortable-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3);
  min-width: 0;
}

.ui-sortable-content {
  min-width: 0;
  flex: 1;
}
</style>
