<script setup lang="ts" generic="T extends object">
import { computed, ref, shallowRef, watch } from 'vue'
import {
  getCoreRowModel,
  getSortedRowModel,
  useVueTable,
  type ColumnSizingState,
  type SortingState,
} from '@tanstack/vue-table'
import type { TableColumn, TableSort } from './types'
import UiCheckbox from './UiCheckbox.vue'
import UiEmptyState from './UiEmptyState.vue'
import UiIcon from './UiIcon.vue'
import UiDragHandle from './UiDragHandle.vue'
import UiHelpPopover from './UiHelpPopover.vue'
import UiButton from './UiButton.vue'
import { useRowDrag } from './drag'
const props = withDefaults(
  defineProps<{
    rows: T[]
    columns: TableColumn[]
    rowKey: (row: T) => string
    selected?: string[]
    selectable?: boolean
    sort?: TableSort
    manualSorting?: boolean
    loading?: boolean
    empty?: string
    storageKey?: string
    reorderable?: boolean
    reorderDisabled?: boolean
    reorderGroup?: (row: T) => string
    footer?: Record<string, string | number | undefined>
    addLabel?: string
    addDisabled?: boolean
  }>(),
  { selected: () => [] },
)
const emit = defineEmits<{
  'update:selected': [value: string[]]
  'update:sort': [value: TableSort]
  reorder: [value: { id: string; targetId: string; after: boolean }]
  add: []
}>()
const { setRoot, dragging, startDrag, endDrag, drop, over, leave, startPointer, keydown } =
  useRowDrag(
    () => !!props.reorderable && !props.reorderDisabled,
    () =>
      table
        .getRowModel()
        .rows.map((row) => ({ id: row.id, group: props.reorderGroup?.(row.original) })),
    (move) => emit('reorder', move),
  )
const sizes = ref<ColumnSizingState>({})
if (props.storageKey) {
  try {
    sizes.value = JSON.parse(
      localStorage.getItem(`table-widths:${props.storageKey}`) || '{}',
    ) as ColumnSizingState
  } catch {
    /* 使用默认列宽。 */
  }
}
watch(
  sizes,
  (value) => {
    if (props.storageKey) {
      try {
        localStorage.setItem(`table-widths:${props.storageKey}`, JSON.stringify(value))
      } catch {
        /* 会话内列宽仍有效。 */
      }
    }
  },
  { deep: true },
)
const sorting = computed<SortingState>(() =>
  props.sort ? [{ id: props.sort.key, desc: props.sort.direction === 'desc' }] : [],
)
const selection = computed(() => Object.fromEntries(props.selected.map((id) => [id, true])))
const columns = computed(() =>
  props.columns.map((column) => ({
    id: column.key,
    accessorFn: (row: T) => Reflect.get(row, column.key) as unknown,
    header: column.label,
    enableSorting: column.sortable === true,
    size: column.width || 180,
    minSize: column.minWidth || 80,
    maxSize: column.maxWidth,
  })),
)
// TanStack 按数组引用缓存行与单元格；草稿原位编辑时刷新缓存并保留原始行对象。
const data = shallowRef<T[]>([])
watch(
  () => props.rows,
  (rows) => {
    data.value = [...rows]
  },
  { immediate: true, deep: true },
)
const table = useVueTable({
  data,
  get columns() {
    return columns.value
  },
  getRowId: (row) => props.rowKey(row),
  getCoreRowModel: getCoreRowModel(),
  getSortedRowModel: getSortedRowModel(),
  get manualSorting() {
    return props.manualSorting
  },
  columnResizeMode: 'onChange',
  state: {
    get sorting() {
      return sorting.value
    },
    get rowSelection() {
      return selection.value
    },
    get columnSizing() {
      return sizes.value
    },
  },
  onSortingChange: (update) => {
    const next = typeof update === 'function' ? update(sorting.value) : update
    const item = next[0]
    if (item) emit('update:sort', { key: item.id, direction: item.desc ? 'desc' : 'asc' })
  },
  onRowSelectionChange: (update) => {
    const next = typeof update === 'function' ? update(selection.value) : update
    emit(
      'update:selected',
      Object.keys(next).filter((id) => next[id]),
    )
  },
  onColumnSizingChange: (update) => {
    sizes.value = typeof update === 'function' ? update(sizes.value) : update
  },
  enableSortingRemoval: false,
})
const flexibleWeight = computed(() =>
  props.columns.reduce(
    (total, column) =>
      total + (column.fixed ? 0 : (column.grow ?? table.getColumn(column.key)?.getSize() ?? 0)),
    0,
  ),
)
function columnWidth(id: string, size: number) {
  const column = props.columns.find((column) => column.key === id)
  if (column?.fixed) return `${size}px`
  if (!flexibleWeight.value) return `${size}px`
  const extra = `max(0px, 100cqw - var(--table-utility-width) - ${table.getTotalSize()}px)`
  const preferred = `calc(${size}px + ${extra} * ${(column?.grow ?? size) / flexibleWeight.value})`
  if (column?.maxWidth) return `min(${column.maxWidth}px, ${preferred})`
  // 有上限的列停止扩展后，额外宽度分配给未设上限的弹性列。
  const capped = props.columns.filter((item) => !item.fixed && item.maxWidth)
  if (!capped.length) return preferred
  const remainingWeight = props.columns.reduce(
    (total, item) =>
      total +
      (item.fixed || item.maxWidth ? 0 : (item.grow ?? table.getColumn(item.key)?.getSize() ?? 0)),
    0,
  )
  if (!remainingWeight) return preferred
  const surplus = capped
    .map((item) => {
      const base = table.getColumn(item.key)?.getSize() || 0
      return `max(0px, ${extra} * ${(item.grow ?? base) / flexibleWeight.value} - ${item.maxWidth! - base}px)`
    })
    .join(' + ')
  return `calc(${preferred} + (${surplus}) * ${(column?.grow ?? size) / remainingWeight})`
}
</script>
<template>
  <div
    :ref="setRoot"
    class="ui-table-wrap ui-sort-root surface"
    :class="{ 'ui-table-wrap--reorderable': reorderable }"
    :aria-busy="loading || undefined"
    @dragover="over"
    @dragleave="leave"
    @drop="drop"
  >
    <div class="ui-table-scroll">
      <table
        class="ui-table"
        :style="{
          '--table-utility-width': `calc(${selectable ? 'var(--table-check-width)' : '0px'} + ${reorderable ? 'var(--table-order-width)' : '0px'})`,
          minWidth: `calc(${table.getTotalSize()}px + var(--table-utility-width))`,
        }"
      >
        <thead>
          <tr>
            <th v-if="reorderable" class="ui-table-order" aria-label="顺序" />
            <th v-if="selectable" class="ui-table-check">
              <UiCheckbox
                class="ui-table-checkbox"
                :model-value="
                  table.getIsAllRowsSelected() && rows.length > 0
                    ? true
                    : table.getIsSomeRowsSelected()
                      ? 'indeterminate'
                      : false
                "
                label="选择当前列表"
                :disabled="!rows.length"
                @update:model-value="table.toggleAllRowsSelected($event)"
              />
            </th>
            <th
              v-for="header in table.getFlatHeaders()"
              :key="header.id"
              :style="{ width: columnWidth(header.id, header.getSize()) }"
              :class="{
                'ui-table-right':
                  props.columns.find((column) => column.key === header.id)?.align === 'right',
              }"
              :aria-sort="
                header.column.getIsSorted() === 'asc'
                  ? 'ascending'
                  : header.column.getIsSorted() === 'desc'
                    ? 'descending'
                    : undefined
              "
            >
              <div class="ui-table-header-content">
                <slot :name="`header-${header.id}`">
                  <button
                    v-if="header.column.getCanSort()"
                    class="ui-table-heading"
                    @click="header.column.toggleSorting()"
                  >
                    {{ header.column.columnDef.header }}
                    <UiIcon
                      :name="
                        header.column.getIsSorted() === 'asc'
                          ? 'up'
                          : header.column.getIsSorted() === 'desc'
                            ? 'arrowDown'
                            : 'sort'
                      "
                      :size="13"
                    />
                  </button>
                  <span v-else>{{ header.column.columnDef.header }}</span>
                </slot>
                <UiHelpPopover
                  v-if="props.columns.find((column) => column.key === header.id)?.help"
                  :label="`${header.column.columnDef.header}说明`"
                  :text="props.columns.find((column) => column.key === header.id)?.help"
                />
              </div>
              <div
                class="ui-table-resize"
                role="separator"
                tabindex="0"
                aria-orientation="vertical"
                :aria-label="`调整${header.column.columnDef.header}列宽`"
                :aria-valuenow="Math.round(header.getSize())"
                @mousedown="header.getResizeHandler()($event)"
                @touchstart="header.getResizeHandler()($event)"
                @dblclick="header.column.resetSize()"
                @keydown.left.prevent="
                  table.setColumnSizing((old) => ({
                    ...old,
                    [header.id]: Math.max(
                      header.column.columnDef.minSize || 80,
                      header.getSize() - 16,
                    ),
                  }))
                "
                @keydown.right.prevent="
                  table.setColumnSizing((old) => ({ ...old, [header.id]: header.getSize() + 16 }))
                "
              />
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in table.getRowModel().rows"
            :key="row.id"
            class="ui-sort-item"
            :data-sort-id="row.id"
            :data-selected="row.getIsSelected() || undefined"
            :data-dragging="dragging === row.id || undefined"
          >
            <td v-if="reorderable" class="ui-table-order">
              <div class="ui-table-order-actions">
                <UiDragHandle
                  :disabled="reorderDisabled"
                  :label="`拖动 ${row.id} 排序`"
                  @dragstart="startDrag($event, row.id)"
                  @dragend="endDrag"
                  @pointerdown="startPointer($event, row.id)"
                  @keydown="keydown($event, row.id)"
                />
              </div>
            </td>
            <td v-if="selectable" class="ui-table-check">
              <UiCheckbox
                class="ui-table-checkbox"
                :model-value="row.getIsSelected()"
                :label="`选择 ${row.id}`"
                @update:model-value="row.toggleSelected($event)"
              />
            </td>
            <td
              v-for="cell in row.getVisibleCells()"
              :key="cell.id"
              :class="{
                'ui-table-right':
                  columns.find((column) => column.id === cell.column.id) &&
                  props.columns.find((column) => column.key === cell.column.id)?.align === 'right',
              }"
            >
              <slot :name="`cell-${cell.column.id}`" :row="row.original" :value="cell.getValue()">
                {{ cell.getValue() ?? '—' }}
              </slot>
            </td>
          </tr>
          <tr v-if="addLabel" class="ui-table-add-row">
            <td :colspan="props.columns.length + Number(!!selectable) + Number(!!reorderable)">
              <UiButton
                variant="ghost"
                icon="plus"
                class="ui-table-add"
                :disabled="addDisabled"
                @click="emit('add')"
              >
                {{ addLabel }}
              </UiButton>
            </td>
          </tr>
        </tbody>
        <tfoot v-if="footer && rows.length">
          <tr>
            <td v-if="reorderable" />
            <td v-if="selectable" />
            <td
              v-for="column in props.columns"
              :key="column.key"
              :class="{ 'ui-table-right': column.align === 'right' }"
            >
              <slot :name="`footer-${column.key}`" :value="footer[column.key]">
                {{ footer[column.key] }}
              </slot>
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
    <UiEmptyState
      v-if="!rows.length && (!addLabel || loading)"
      :loading="loading"
      :title="empty || '暂无数据'"
    />
    <div v-if="dragging" class="ui-sort-tail" aria-hidden="true" />
  </div>
</template>
<style scoped>
.ui-table-wrap {
  --table-check-width: 44px;
  --table-order-width: calc(var(--control-sm) + var(--space-2));

  min-width: 0;
  container-type: inline-size;
}

.ui-table-wrap--reorderable {
  --table-check-width: var(--control-sm);
}

.ui-table-scroll {
  overflow-x: auto;
  border-radius: inherit;
}

.ui-table-checkbox {
  display: flex;
  justify-content: center;
}

.ui-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: var(--text-base);
}

.ui-table th {
  position: relative;
  height: 44px;
  padding: var(--space-2) var(--space-3);
  background: var(--surface-soft);
  color: var(--muted);
  font-weight: var(--weight-label);
  font-size: var(--text-sm);
  text-align: left;
}

.ui-table td {
  height: 56px;
  padding: 10px var(--space-3);
  border-top: 1px solid var(--divider);
  overflow-wrap: anywhere;
  vertical-align: middle;
}

.ui-table tfoot {
  background: var(--surface-soft);
  font-weight: var(--weight-emphasis);
}

.ui-table .ui-table-add-row td {
  height: auto;
  padding: 0;
}

.ui-table-add {
  position: sticky;
  left: 0;
  width: min(100%, 100cqw);
  min-height: 44px;
  border-radius: 0;
  color: var(--link);
}

.ui-table tr[data-selected] {
  --tag-background: var(--tag-selected-background);

  background: var(--selected);
}

.ui-table tbody tr:not([data-selected]):hover {
  background: color-mix(in srgb, var(--hover) 65%, transparent);
}

.ui-table .ui-table-check {
  width: var(--table-check-width);
  padding-inline: 0;
  text-align: center;
}

.ui-table .ui-table-order {
  width: var(--table-order-width);
  padding-inline: var(--space-2) 0;
}

.ui-table-order-actions {
  display: flex;
  align-items: center;
  justify-content: center;
}

.ui-table .ui-table-right {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.ui-table-heading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  border: 0;
  padding: 0;
  background: transparent;
  color: inherit;
  font-size: inherit;
  font-weight: inherit;
}

.ui-table-header-content {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.ui-table-right .ui-table-header-content {
  justify-content: flex-end;
}

.ui-table-resize {
  position: absolute;
  top: var(--space-2);
  bottom: var(--space-2);
  right: 0;
  width: 5px;
  cursor: col-resize;
  touch-action: none;
}

.ui-table-resize:hover,
.ui-table-resize:focus-visible {
  background: var(--link);
}

@media (pointer: coarse) {
  .ui-table-wrap {
    --table-check-width: calc(var(--control) + 24px);
  }

  .ui-table-heading {
    min-height: var(--control);
  }
}
</style>
