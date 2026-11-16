<script setup lang="ts">
import { computed, ref } from 'vue'
import type { StatisticsOverview } from '@/api/statistics'
import { UiHelpPopover, UiSegmented, UiTrendChart } from '@/components/ui'
import {
  cacheHelp,
  compact,
  latencyHelp,
  metric,
  percent,
  successHelp,
  tpsHelp,
} from './statistics'
const props = defineProps<{ data?: StatisticsOverview; loading: boolean }>()
const trend = ref('requests')
const trendOptions = [
  { value: 'requests', label: '请求' },
  { value: 'tokens', label: 'Token' },
  { value: 'success', label: '上游成功率' },
  { value: 'cache', label: '缓存命中率' },
]
const trendLabel = computed(() => trendOptions.find((item) => item.value === trend.value)!.label)
const totals = computed(() => props.data?.totals)
const cards = computed(() => [
  {
    label: '请求数',
    value: metric(totals.value?.request_count, 0),
    detail: `已记录 ${metric(totals.value?.recorded_count, 0)} 条明细`,
    help: [
      '请求总量来自日聚合，包含历史用量。结果与性能来自保留的请求明细；历史未采集的状态不推测为成功。',
    ],
  },
  {
    label: '总 Token',
    value: totals.value ? compact(totals.value.total_tokens) : '—',
    detail: `输入 ${compact(totals.value?.prompt_tokens || 0)} · 输出 ${compact(totals.value?.completion_tokens || 0)}`,
    help: [
      '上游报告的输入和输出 Token 总量。缓存读取属于输入的一部分，不重复相加。未报告用量的请求不估算 Token。',
    ],
  },
  {
    label: '上游成功率',
    value: percent(totals.value?.upstream_success_rate),
    detail: `成功 ${metric(totals.value?.upstream_success_count, 0)} · 失败 ${metric(totals.value?.upstream_error_count, 0)} · 异常 ${metric(totals.value?.upstream_exception_count, 0)}`,
    help: successHelp,
  },
  {
    label: '缓存命中率',
    value: percent(totals.value?.cache_hit_rate),
    detail: `请求命中 ${percent(totals.value?.cache_request_hit_rate)}`,
    help: cacheHelp,
  },
  {
    label: '平均首字延迟',
    value:
      totals.value?.first_token_latency_ms == null
        ? '—'
        : `${metric(totals.value.first_token_latency_ms / 1000, 2)} s`,
    detail: `${metric(totals.value?.latency_sample_count, 0)} 个可测样本`,
    help: latencyHelp,
  },
  {
    label: '平均 TPS',
    value: totals.value?.tps == null ? '—' : `${metric(totals.value.tps)} tok/s`,
    detail: `${metric(totals.value?.tps_sample_count, 0)} 个可测样本`,
    help: tpsHelp,
  },
])
const points = computed(() =>
  (props.data?.series || []).map((row) => ({
    label: row.date,
    value:
      trend.value === 'success'
        ? (row.upstream_success_rate ?? null)
        : trend.value === 'cache'
          ? (row.cache_hit_rate ?? null)
          : (trend.value === 'requests' ? row.request_count : row.total_tokens) || 0,
  })),
)
</script>
<template>
  <div class="statistics-overview" :aria-busy="loading">
    <section class="statistics-metrics surface" aria-label="统计概览">
      <div v-for="card in cards" :key="card.label" class="statistics-metric">
        <div class="statistics-metric-label">
          {{ card.label }}
          <UiHelpPopover :label="`${card.label}说明`">
            <ul>
              <li v-for="line in card.help" :key="line">{{ line }}</li>
            </ul>
          </UiHelpPopover>
        </div>
        <strong>{{ card.value }}</strong>
        <span class="muted">{{ card.detail }}</span>
      </div>
    </section>
    <section class="surface statistics-chart" aria-label="使用趋势">
      <header>
        <h2>使用趋势</h2>
        <UiSegmented v-model="trend" :items="trendOptions" label="趋势指标" />
      </header>
      <UiTrendChart
        :label="trendLabel"
        :points="points"
        :format="trend === 'success' || trend === 'cache' ? percent : compact"
      />
    </section>
  </div>
</template>
<style scoped>
.statistics-overview {
  display: grid;
  gap: var(--space-5);
  min-width: 0;
}

.statistics-metrics {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  padding: var(--space-5) 0;
}

.statistics-metric {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: 0 var(--space-5);
  min-width: 0;
  border-right: 1px solid var(--border);
}

.statistics-metric:last-child {
  border-right: 0;
}

.statistics-metric-label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--muted);
  white-space: nowrap;
}

.statistics-metric strong {
  font-size: 28px;
  font-weight: 600;
  line-height: 1.3;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}

.statistics-metric > span {
  font-size: var(--text-sm);
  white-space: nowrap;
}

.statistics-chart {
  padding: var(--space-5);
  min-width: 0;
}

.statistics-chart header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

h2 {
  margin: 0;
  font-size: var(--text-section);
  font-weight: 600;
}

@media (width <= 1600px) {
  .statistics-metrics {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    row-gap: var(--space-5);
  }

  .statistics-metric:nth-child(3) {
    border-right: 0;
  }
}

@media (width <= 600px) {
  .statistics-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .statistics-metric {
    padding: 0 var(--space-3);
  }

  .statistics-metric:nth-child(3) {
    border-right: 1px solid var(--border);
  }

  .statistics-metric:nth-child(even) {
    border-right: 0;
  }

  .statistics-metric strong {
    font-size: 25px;
  }

  .statistics-chart {
    padding: var(--space-3);
  }
}
</style>
