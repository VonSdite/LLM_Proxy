<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { statisticsApi, type StatisticsView } from '@/api/statistics'
import {
  UiAlert,
  UiButton,
  UiField,
  UiHelpPopover,
  UiIconButton,
  UiInput,
  UiListToolbar,
  UiMultiSelect,
  UiPageHeader,
  UiPagination,
  UiSegmented,
  UiTabs,
  type TableSort,
  notify,
} from '@/components/ui'
import { useSession } from '@/stores/session'
import { useRequest } from '@/features/shared/useRequest'
import { useAction } from '@/features/shared/useAction'
import JsonImportDialog from '@/features/shared/JsonImportDialog.vue'
import StatisticsTable from '@/features/statistics/StatisticsTable.vue'
import StatisticsOverview from '@/features/statistics/StatisticsOverview.vue'
import { dateErrors, datePreset } from '@/features/statistics/statistics'

const session = useSession()
const section = ref('overview')
const dimension = ref<StatisticsView>('stats')
const view = computed<StatisticsView>(() =>
  section.value === 'logs' ? 'logs' : section.value === 'overview' ? 'models' : dimension.value,
)
const draft = reactive(datePreset(1))
const applied = ref(datePreset(1))
const presets = [
  { value: '1', label: '今天' },
  { value: '7', label: '近 7 天' },
  { value: '30', label: '近 30 天' },
]
const selectedPreset = computed(
  () =>
    presets.find((item) => {
      const range = datePreset(Number(item.value))
      return draft.start_date === range.start_date && draft.end_date === range.end_date
    })?.value || '',
)
const errors = ref<{ start?: string; end?: string }>({})
const page = ref(1)
const pageSize = ref(50)
const sorts = reactive<Record<StatisticsView, TableSort>>({
  stats: { key: 'total_tokens', direction: 'desc' },
  model_usage: { key: 'total_tokens', direction: 'desc' },
  response_model_usage: { key: 'total_tokens', direction: 'desc' },
  user_usage: { key: 'total_tokens', direction: 'desc' },
  api_key_usage: { key: 'total_tokens', direction: 'desc' },
  ip_usage: { key: 'total_tokens', direction: 'desc' },
  models: { key: 'request_count', direction: 'desc' },
  logs: { key: 'start_time', direction: 'desc' },
})
const sort = computed({
  get: () => sorts[view.value],
  set: (value) => {
    sorts[view.value] = value
  },
})
const dimensions = computed(() => [
  { value: 'stats', label: '来源 × 模型' },
  { value: 'model_usage', label: '请求模型' },
  { value: 'response_model_usage', label: '响应模型' },
  { value: 'ip_usage', label: 'IP' },
  { value: 'user_usage', label: '用户' },
  ...(session.context?.features.api_keys ? [{ value: 'api_key_usage', label: 'API Key' }] : []),
])
watch(
  () => session.context?.features.api_keys,
  (enabled) => {
    if (!enabled && dimension.value === 'api_key_usage') dimension.value = 'stats'
  },
)
watch(
  [view, sort, pageSize],
  () => {
    page.value = 1
  },
  { deep: true },
)
const query = computed(() => ({
  ...applied.value,
  sort_key: sort.value.key,
  sort_direction: sort.value.direction,
  ...(view.value === 'logs' ? { page: page.value, page_size: pageSize.value } : {}),
}))
const overview = useRequest((signal) => statisticsApi.overview(applied.value, signal))
const request = useRequest(async (signal) => {
  const current = view.value
  return { ...(await statisticsApi.list(current, query.value, signal)), view: current }
})
const candidates = useRequest(statisticsApi.candidates)
const action = useAction()
const lifetime = new AbortController()
onBeforeUnmount(() => lifetime.abort())
const importing = ref(false)
const usesOverview = computed(() =>
  ['models', 'model_usage', 'response_model_usage', 'ip_usage'].includes(view.value),
)
watch(
  [applied, view],
  ([filters], [previousFilters]) => {
    if (usesOverview.value || filters !== previousFilters) void overview.refresh()
  },
  { immediate: true },
)
watch(
  [view, query],
  () => {
    if (!usesOverview.value) void request.refresh()
  },
  { immediate: true },
)
watch(request.data, (result) => {
  if (
    view.value === 'logs' &&
    result?.view === 'logs' &&
    page.value > Math.max(1, Math.ceil(result.total / pageSize.value))
  )
    page.value = Math.max(1, Math.ceil(result.total / pageSize.value))
})
const rows = computed(() =>
  view.value === 'response_model_usage'
    ? overview.data.value?.response_models || []
    : view.value === 'ip_usage'
      ? overview.data.value?.ips || []
      : view.value === 'model_usage'
        ? overview.data.value?.model_flows || []
        : request.data.value?.view === view.value
          ? request.data.value.rows
          : [],
)
const loading = computed(() =>
  usesOverview.value ? overview.loading.value : request.loading.value,
)
void candidates.refresh()
function apply() {
  errors.value = dateErrors(draft)
  if (errors.value.start || errors.value.end) return
  if (JSON.stringify(draft) === JSON.stringify(applied.value)) return
  page.value = 1
  applied.value = {
    ...draft,
    username: [...draft.username],
    request_model: [...draft.request_model],
    response_model: [...draft.response_model],
    ip_address: [...draft.ip_address],
  }
}
function preset(days: number) {
  const range = datePreset(days)
  draft.start_date = range.start_date
  draft.end_date = range.end_date
  apply()
}
function model(value: string) {
  if (!value) return
  Object.assign(draft, applied.value, {
    username: [...applied.value.username],
    request_model: [value],
    response_model: [...applied.value.response_model],
    ip_address: [...applied.value.ip_address],
  })
  apply()
  section.value = 'overview'
}
function refresh() {
  void overview.refresh()
  if (!usesOverview.value) void request.refresh()
}
async function importData(value: unknown, signal: AbortSignal) {
  const result = await statisticsApi.importData(value, signal)
  return `请求明细新增 ${result.request_logs_inserted_count} 条、重复 ${result.request_logs_skipped_count} 条；日聚合新增 ${result.daily_request_stats_inserted_count} 条、合并 ${result.daily_request_stats_merged_count} 条`
}
function imported(message: string) {
  notify(message)
  refresh()
  void candidates.refresh()
}
</script>
<template>
  <div class="page">
    <UiPageHeader title="统计与日志">
      <template #navigation>
        <UiTabs
          v-model="section"
          :items="[
            { value: 'overview', label: '概览' },
            { value: 'usage', label: '用量' },
            { value: 'logs', label: '请求明细' },
          ]"
          label="统计视图"
        />
      </template>
      <UiIconButton icon="refresh" label="刷新统计" :loading="loading" @click="refresh" />
    </UiPageHeader>
    <UiListToolbar>
      <template #actions>
        <div class="statistics-presets">
          <UiSegmented
            :model-value="selectedPreset"
            :items="presets"
            label="日期快捷范围"
            @update:model-value="preset(Number($event))"
          />
        </div>
        <UiButton icon="upload" @click="importing = true">导入</UiButton>
        <UiButton
          icon="download"
          :disabled="action.busy.value"
          @click="action.run(() => statisticsApi.exportData(query, lifetime.signal))"
        >
          导出数据
        </UiButton>
        <UiButton
          icon="download"
          :disabled="action.busy.value"
          @click="action.run(() => statisticsApi.exportExcel(view, query, lifetime.signal))"
        >
          导出 Excel
        </UiButton>
        <UiHelpPopover label="统计导入导出说明">
          <ul>
            <li>
              导出数据包含当前已查询范围的请求明细与日聚合，可再次导入；Excel
              对应当前视图，包含全部分页。
            </li>
            <li>
              收起下拉框后筛选自动生效，导出使用已应用条件。相同请求明细导入时跳过，日聚合按数值累加，重复导入会再次累加。
            </li>
          </ul>
        </UiHelpPopover>
      </template>
    </UiListToolbar>
    <div class="statistics-filters">
      <UiField label="开始日期" :error="errors.start">
        <UiInput v-model="draft.start_date" type="date" required @change="apply" />
      </UiField>
      <UiField
        label="结束日期"
        :error="errors.end"
        help="包含开始和结束日期当天，最多一年。日历选定日期或输入完成后自动查询；输入过程中不查询。"
      >
        <UiInput
          v-model="draft.end_date"
          type="date"
          :min="draft.start_date"
          required
          @change="apply"
        />
      </UiField>
      <UiField label="用户">
        <UiMultiSelect
          v-model="draft.username"
          :options="(candidates.data.value?.users || []).map((value) => ({ value, label: value }))"
          placeholder="全部用户"
          @commit="apply"
        />
      </UiField>
      <UiField label="IP">
        <UiMultiSelect
          v-model="draft.ip_address"
          :options="
            (candidates.data.value?.ip_addresses || []).map((value) => ({ value, label: value }))
          "
          placeholder="全部 IP"
          @commit="apply"
        />
      </UiField>
      <UiField
        label="请求模型"
        help="客户端请求的原始模型 ID，包含模型映射名称。选择一个模型可研究它的成功率、缓存和性能，条件同时应用于用量与请求明细。"
      >
        <UiMultiSelect
          v-model="draft.request_model"
          :options="(candidates.data.value?.models || []).map((value) => ({ value, label: value }))"
          placeholder="全部模型"
          @commit="apply"
        />
      </UiField>
      <UiField
        label="响应模型"
        help="按请求最终返回的上游模型 ID 筛选。同一请求中的全部重试仍计入成功率。支持多选，与其他筛选条件同时生效。"
      >
        <UiMultiSelect
          v-model="draft.response_model"
          :options="
            (candidates.data.value?.response_models || []).map((value) => ({ value, label: value }))
          "
          placeholder="全部响应模型"
          @commit="apply"
        />
      </UiField>
    </div>
    <UiAlert v-if="request.error.value || overview.error.value || candidates.error.value">
      {{ request.error.value || overview.error.value || candidates.error.value }}
    </UiAlert>
    <StatisticsOverview
      v-if="section === 'overview'"
      :data="overview.data.value"
      :loading="overview.loading.value"
    />
    <template v-else>
      <div v-if="section === 'usage'" class="statistics-dimensions">
        <UiSegmented
          :model-value="dimension"
          :items="dimensions"
          label="汇总维度"
          @update:model-value="dimension = $event as StatisticsView"
        />
        <UiHelpPopover label="汇总维度说明">
          <ul>
            <li>
              用户合并同名用户的使用量；未登记的来源以 IP 区分。IP 按访问地址汇总，API Key
              按密钥汇总。
            </li>
            <li>
              来源 ×
              模型按用户、IP、请求模型、目标模型和响应模型组合分组，可查看具体来源使用了哪些模型。
            </li>
            <li>
              请求模型按请求模型 → 映射目标模型 →
              响应模型的完整流向汇总；同一流向合并不同来源，目标或响应模型不同则分别统计。
            </li>
            <li>
              响应模型按上游实际报告的模型 ID 汇总，合并不同请求模型、映射目标和来源；未提供模型 ID
              的记录归入未知。
            </li>
            <li>请求模型、用户、IP 和交叉维度保留日聚合的历史用量；API Key 依据保留的请求明细。</li>
          </ul>
        </UiHelpPopover>
      </div>
      <StatisticsTable
        :key="view"
        v-model:sort="sort"
        :rows="rows"
        :view="view"
        :loading="loading"
        @model="model"
      />
      <UiPagination
        v-if="view === 'logs'"
        v-model:page="page"
        v-model:page-size="pageSize"
        :sizes="[50, 100, 200]"
        :total="request.data.value?.total || 0"
        :disabled="loading"
      />
    </template>
    <JsonImportDialog
      v-if="importing"
      v-model:open="importing"
      title="导入统计数据"
      :help="[
        '使用导出数据生成的 JSON，可包含请求明细与日聚合。相同请求明细会跳过，相同维度的日聚合按数值累加。',
        '重复导入同一份日聚合会再次累加。导入范围由文件内容决定，不受当前页面筛选条件限制。Excel 用于查看，不能在此导入。',
      ]"
      :submit="importData"
      @imported="imported"
    />
  </div>
</template>
<style scoped>
.statistics-filters {
  display: grid;
  grid-template-columns: 155px 155px minmax(120px, 0.8fr) minmax(140px, 1fr) repeat(
      2,
      minmax(160px, 1.2fr)
    );
  gap: var(--space-3);
  align-items: start;
}

.statistics-presets {
  display: flex;
  gap: var(--space-1);
  margin-right: var(--space-3);
}

.statistics-dimensions {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--space-3);
}

@media (width <= 1500px) {
  .statistics-filters {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .statistics-filters > :nth-child(5) {
    grid-column: 1 / span 2;
  }

  .statistics-filters > :nth-child(6) {
    grid-column: span 2;
  }
}

@media (width <= 900px) {
  .statistics-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .statistics-filters > :nth-child(5),
  .statistics-filters > :nth-child(6) {
    grid-column: auto;
  }
}

@media (width <= 480px) {
  .statistics-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .statistics-filters > :nth-child(n + 3) {
    grid-column: 1 / -1;
  }

  .statistics-presets {
    flex-basis: 100%;
    margin-right: 0;
  }

  .statistics-dimensions {
    align-items: flex-start;
  }
}
</style>
