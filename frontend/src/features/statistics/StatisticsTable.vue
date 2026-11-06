<script setup lang="ts">
import { computed } from 'vue'
import type { StatisticsView, UsageRow } from '@/api/statistics'
import { UiTable, UiBadge, type TableSort } from '@/components/ui'
import { number } from '@/features/shared/format'
import { cacheRate, columnsByView, duration, rowKey, tokenKeys, totals } from './statistics'
const props = defineProps<{
  rows: UsageRow[]
  view: StatisticsView
  loading: boolean
  sort: TableSort
}>()
const emit = defineEmits<{ 'update:sort': [value: TableSort] }>()
const footer = computed(() => totals(props.rows, props.view))
</script>
<template>
  <UiTable
    :rows="rows"
    :columns="columnsByView[view]"
    :row-key="(row) => rowKey(row, view)"
    :loading="loading"
    :sort="sort"
    manual-sorting
    :footer="footer"
    :storage-key="`statistics-${view}`"
    empty="该时间范围内没有请求"
    @update:sort="emit('update:sort', $event)"
  >
    <template #cell-ip_address="{ row }">
      <span class="mono">{{ row.ip_address }}</span>
    </template>
    <template #cell-model_flow="{ row }">
      <div class="statistics-flow mono">
        <span title="请求模型">{{ row.request_model || '—' }}</span>
        <span v-if="row.target_model_id" class="muted" title="目标模型">
          → {{ row.target_model_id }}
        </span>
        <span v-if="row.response_model" class="muted" title="响应模型">
          → {{ row.response_model }}
        </span>
      </div>
    </template>
    <template v-for="key in tokenKeys" :key="key" #[`cell-${key}`]="{ row }">
      {{ number(row[key]) }}
    </template>
    <template #cell-cache_hit_rate="{ row }">
      <span
        :title="
          row.cache_usage_status === 'partial'
            ? '部分请求提供了缓存明细，命中率按已知输入 Token 加权'
            : row.cache_usage_status === 'unknown'
              ? '上游未提供缓存 Token 明细'
              : '缓存读取 Token / 已知输入 Token'
        "
      >
        {{ cacheRate(row.cache_hit_rate, row.cache_usage_status) }}
      </span>
    </template>
    <template #cell-request_count="{ row }">{{ number(row.request_count) }}</template>
    <template #cell-key_name="{ row }">
      <div class="statistics-key">
        <span>{{ row.key_name }}</span>
        <UiBadge v-if="row.status === 'deleted'">已删除</UiBadge>
        <UiBadge v-else-if="row.status === 'disabled'">已禁用</UiBadge>
      </div>
    </template>
    <template #cell-key_preview="{ row }">
      <span class="mono">{{ row.key_preview }}</span>
    </template>
    <template #cell-duration="{ row }">{{ duration(row) }}</template>
  </UiTable>
</template>
<style scoped>
.statistics-flow,
.statistics-key {
  display: grid;
  gap: var(--space-1);
}
</style>
