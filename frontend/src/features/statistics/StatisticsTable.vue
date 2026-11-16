<script setup lang="ts">
import { computed } from 'vue'
import type { StatisticsView, UsageRow } from '@/api/statistics'
import { UiTable, UiBadge, UiButton, UiTooltip, type TableSort } from '@/components/ui'
import { number } from '@/features/shared/format'
import {
  cacheRate,
  columnsByView,
  duration,
  metric,
  modelFlowSteps,
  percent,
  requestTime,
  requestError,
  rowKey,
  tokenKeys,
  totals,
  tpsUnavailableHelp,
} from './statistics'
const props = defineProps<{
  rows: UsageRow[]
  view: StatisticsView
  loading: boolean
  sort: TableSort
}>()
const emit = defineEmits<{ 'update:sort': [value: TableSort]; model: [value: string] }>()
const footer = computed(() => totals(props.rows, props.view))
const tableRows = computed(() =>
  props.view === 'model_usage'
    ? props.rows.map((row) => ({
        ...row,
        model_flow: [row.request_model, row.target_model_id].filter(Boolean).join(' → '),
      }))
    : props.rows,
)
</script>
<template>
  <UiTable
    :rows="tableRows"
    :columns="columnsByView[view]"
    :row-key="(row) => rowKey(row, view)"
    :loading="loading"
    :sort="sort"
    :manual-sorting="!['models', 'model_usage', 'response_model_usage', 'ip_usage'].includes(view)"
    :footer="footer"
    :storage-key="`statistics-${view}`"
    empty="该时间范围内没有请求"
    @update:sort="emit('update:sort', $event)"
  >
    <template #cell-request_model="{ row }">
      <UiButton variant="link" size="title" @click="emit('model', row.request_model!)">
        {{ row.request_model }}
      </UiButton>
    </template>
    <template #cell-start_time="{ row }">
      <time class="statistics-time" :datetime="row.start_time?.replace(' ', 'T')">
        <span>{{ requestTime(row.start_time).split(' ')[0] }}</span>
        <span>{{ requestTime(row.start_time).split(' ')[1] }}</span>
      </time>
    </template>
    <template #cell-response_model="{ row }">
      <span class="statistics-model-name" :class="{ muted: !row.response_model }">
        {{ row.response_model || '未知' }}
      </span>
    </template>
    <template #cell-source="{ row }">
      <div class="statistics-source">
        <span v-if="row.username && row.username !== '-'">{{ row.username }}</span>
        <span class="statistics-address">
          {{ row.ip_address || '—' }}
        </span>
      </div>
    </template>
    <template #cell-outcome="{ row }">
      <div class="statistics-flow">
        <UiBadge
          :variant="
            row.outcome === 'success' ? 'success' : row.outcome === 'error' ? 'danger' : 'neutral'
          "
        >
          {{
            row.outcome === 'success'
              ? '成功'
              : row.outcome === 'error'
                ? '失败'
                : row.outcome === 'cancelled'
                  ? '已取消'
                  : '未知'
          }}
        </UiBadge>
        <span
          v-if="row.outcome === 'error'"
          class="statistics-flow danger"
          :title="row.error_type || undefined"
        >
          <span v-if="row.http_status">{{ row.http_status }}</span>
          <span v-if="row.error_type">{{ requestError(row) }}</span>
        </span>
      </div>
    </template>
    <template #cell-upstream_success_rate="{ row }">
      <UiTooltip
        :text="`上游成功 ${number(row.upstream_success_count)} · 上游失败 ${number(row.upstream_error_count)} · 异常 ${number(row.upstream_exception_count)} · 中止 ${number(row.upstream_cancelled_count)} · 未知 ${number(row.upstream_unknown_count)}`"
      >
        <span
          class="statistics-rate"
          :class="{ 'statistics-rate--failed': (row.upstream_error_count || 0) > 0 }"
        >
          {{ percent(row.upstream_success_rate) }}
        </span>
      </UiTooltip>
    </template>
    <template #cell-first_token_latency_ms="{ row }">
      <span :class="{ muted: row.first_token_latency_ms == null }">
        {{
          row.first_token_latency_ms == null ? '—' : metric(row.first_token_latency_ms / 1000, 2)
        }}
      </span>
      <span v-if="row.first_token_latency_ms != null" class="muted">{{ ' ' }}s</span>
    </template>
    <template #cell-tps="{ row }">
      <UiTooltip v-if="view === 'logs' && row.tps == null" :text="tpsUnavailableHelp(row)">
        <span tabindex="0" aria-label="TPS 未计算原因" class="statistics-missing-metric muted">
          —
        </span>
      </UiTooltip>
      <span v-else class="statistics-measure">
        <span :class="{ muted: row.tps == null }">{{ metric(row.tps) }}</span>
        <span v-if="row.tps != null" class="muted">{{ ' ' }}tok/s</span>
      </span>
    </template>
    <template #cell-ip_address="{ row }">
      <span class="mono">{{ row.ip_address }}</span>
    </template>
    <template #cell-model_flow="{ row }">
      <div class="statistics-model-flow">
        <span
          v-for="(step, index) in modelFlowSteps(row)"
          :key="index"
          class="statistics-model-step"
        >
          <span v-if="index" class="muted">→</span>
          <UiTooltip :text="`${step.stage}模型：${step.model}`">
            <UiButton
              v-if="index === 0 && row.request_model"
              variant="link"
              @click="emit('model', row.request_model)"
            >
              {{ step.model }}
            </UiButton>
            <span v-else tabindex="0" class="statistics-model-name">{{ step.model }}</span>
          </UiTooltip>
        </span>
        <span v-if="!modelFlowSteps(row).length" class="muted">—</span>
      </div>
    </template>
    <template v-for="key in tokenKeys" :key="key" #[`cell-${key}`]="{ row }">
      <template v-if="view !== 'logs'">{{ number(row[key]) }}</template>
      <span
        v-else-if="key === 'cache_read_input_tokens'"
        class="statistics-measure"
        :class="{ muted: row.cache_usage_status === 'unknown' }"
      >
        {{ row.cache_usage_status === 'unknown' ? '未知' : metric(row[key], 0) }}
      </span>
      <span v-else class="statistics-measure" :class="{ muted: row.usage_status === 'unknown' }">
        {{ row.usage_status === 'unknown' ? '未知' : metric(row[key], 0) }}
      </span>
    </template>
    <template #cell-cache_hit_rate="{ row }">
      <span
        :class="{ muted: row.cache_usage_status === 'unknown' || row.cache_hit_rate == null }"
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
    <template #cell-duration="{ row }">
      <span class="statistics-measure">
        <span :class="{ muted: duration(row) === '—' }">{{ duration(row) }}</span>
        <span v-if="duration(row) !== '—'" class="muted">{{ ' ' }}s</span>
      </span>
    </template>
  </UiTable>
</template>
<style scoped>
.statistics-flow,
.statistics-key {
  display: grid;
  gap: var(--space-1);
}

.statistics-flow {
  justify-items: start;
  overflow-wrap: anywhere;
}

.statistics-model-flow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1) var(--space-2);
}

.statistics-model-step {
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-1);
  min-width: 0;
  max-width: 100%;
}

.statistics-model-name {
  min-width: 0;
  overflow-wrap: anywhere;
}

.statistics-rate {
  font-variant-numeric: tabular-nums;
}

.statistics-time,
.statistics-source {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-1) var(--space-2);
}

.statistics-time {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.statistics-address {
  white-space: nowrap;
}

.statistics-measure {
  white-space: nowrap;
}

.statistics-rate--failed {
  color: var(--warning);
}

.statistics-missing-metric {
  cursor: help;
}
</style>
