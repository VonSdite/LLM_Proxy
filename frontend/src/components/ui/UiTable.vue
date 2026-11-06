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
import UiIconButton from './UiIconButton.vue'
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
    footer?: Record<string, string | number | undefined>
  }>(),
  { selected: () => [] },
)
const emit = defineEmits<{
  'update:selected': [value: string[]]
  'update:sort': [value: TableSort]
  reorder: [value: { id: string; targetId: string; after: boolean }]
}>()
const { dragOver, startDrag, endDrag, drop, over } = useRowDrag(
  () => !!props.reorderable,
  (move) => emit('reorder', move),
)
function moveRow(index: number, offset: number) {
  const rows = table.getRowModel().rows
  const source = rows[index]
  const target = rows[index + offset]
  if (source && target) emit('reorder', { id: source.id, targetId: target.id, after: offset > 0 })
}
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
    minSize: 80,
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
</script>
<template>
  <div class="ui-table-wrap surface" :aria-busy="loading || undefined">
    <div class="ui-table-scroll">
      <table
        class="ui-table"
        :style="{
          minWidth: `${table.getTotalSize() + (selectable ? 44 : 0) + (reorderable ? 100 : 0)}px`,
        }"
      >
        <thead>
          <tr>
            <th v-if="reorderable" class="ui-table-order">顺序</th>
            <th v-if="selectable" class="ui-table-check">
              <UiCheckbox
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
              :style="{ width: `${header.getSize()}px` }"
              :aria-sort="
                header.column.getIsSorted() === 'asc'
                  ? 'ascending'
                  : header.column.getIsSorted() === 'desc'
                    ? 'descending'
                    : undefined
              "
            >
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
                    [header.id]: Math.max(80, header.getSize() - 16),
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
            v-for="(row, rowIndex) in table.getRowModel().rows"
            :key="row.id"
            :data-selected="row.getIsSelected() || undefined"
            :data-drag-over="dragOver === row.id || undefined"
            @dragover="over($event, row.id)"
            @drop="drop($event, row.id)"
          >
            <td v-if="reorderable" class="ui-table-order">
              <div class="ui-table-order-actions">
                <button
                  type="button"
                  class="ui-table-drag"
                  draggable="true"
                  :aria-label="`拖动 ${row.id} 排序`"
                  @dragstart="startDrag($event, row.id)"
                  @dragend="endDrag"
                >
                  <UiIcon name="grip" :size="14" />
                </button>
                <UiIconButton
                  icon="up"
                  :label="`上移 ${row.id}`"
                  :disabled="rowIndex === 0"
                  @click="moveRow(rowIndex, -1)"
                />
                <UiIconButton
                  icon="arrowDown"
                  :label="`下移 ${row.id}`"
                  :disabled="rowIndex === rows.length - 1"
                  @click="moveRow(rowIndex, 1)"
                />
              </div>
            </td>
            <td v-if="selectable" class="ui-table-check">
              <UiCheckbox
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
    <UiEmptyState v-if="!rows.length" :loading="loading" :title="empty || '暂无数据'" />
  </div>
</template>
<style scoped>
.ui-table-wrap {
  overflow: hidden;
  min-width: 0;
}

.ui-table-scroll {
  overflow-x: auto;
}

.ui-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: var(--text-sm);
}

.ui-table th {
  position: relative;
  height: 40px;
  padding: var(--space-2) var(--space-3);
  background: var(--input);
  color: var(--muted);
  font-weight: 500;
  text-align: left;
}

.ui-table td {
  height: 48px;
  padding: var(--space-2) var(--space-3);
  border-top: 1px solid var(--border);
  overflow-wrap: anywhere;
  vertical-align: middle;
}

.ui-table tfoot {
  background: var(--input);
  font-weight: 600;
}

.ui-table tr[data-selected] {
  background: var(--selected);
}

.ui-table tbody tr:hover {
  background: color-mix(in srgb, var(--hover) 65%, transparent);
}

.ui-table .ui-table-check {
  width: 44px;
  text-align: center;
}

.ui-table .ui-table-order {
  width: 100px;
}

.ui-table-order-actions {
  display: flex;
  align-items: center;
}

.ui-table-drag {
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--muted);
  cursor: grab;
}

.ui-table tr[data-drag-over] {
  box-shadow: inset 0 2px var(--link);
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
  .ui-table .ui-table-check {
    width: calc(var(--control) + 24px);
  }

  .ui-table .ui-table-order {
    width: calc(3 * var(--control) + 24px);
  }

  .ui-table-drag {
    min-width: var(--control);
    min-height: var(--control);
  }

  .ui-table-heading {
    min-height: var(--control);
  }
}
</style>
