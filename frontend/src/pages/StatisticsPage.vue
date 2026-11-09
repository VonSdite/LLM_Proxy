<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { statisticsApi, type StatisticsView } from '@/api/statistics'
import {
  UiAlert,
  UiButton,
  UiField,
  UiHelpPopover,
  UiInput,
  UiMultiSelect,
  UiPagination,
  UiTabs,
  type TableSort,
  notify,
} from '@/components/ui'
import { useSession } from '@/stores/session'
import { useRequest } from '@/features/shared/useRequest'
import { useAction } from '@/features/shared/useAction'
import JsonImportDialog from '@/features/shared/JsonImportDialog.vue'
import StatisticsTable from '@/features/statistics/StatisticsTable.vue'
import { dateErrors, datePreset } from '@/features/statistics/statistics'

const session = useSession()
const view = ref<StatisticsView>('stats')
const draft = reactive(datePreset(1))
const applied = ref(datePreset(1))
const errors = ref<{ start?: string; end?: string }>({})
const page = ref(1)
const pageSize = ref(50)
const sorts = reactive<Record<StatisticsView, TableSort>>({
  stats: { key: 'total_tokens', direction: 'desc' },
  user_usage: { key: 'total_tokens', direction: 'desc' },
  api_key_usage: { key: 'total_tokens', direction: 'desc' },
  logs: { key: 'start_time', direction: 'desc' },
})
const sort = computed({
  get: () => sorts[view.value],
  set: (value) => {
    sorts[view.value] = value
  },
})
const tabs = computed(() => [
  { value: 'stats', label: '调用汇总' },
  { value: 'user_usage', label: '用户用量' },
  ...(session.context?.features.api_keys ? [{ value: 'api_key_usage', label: 'Key 用量' }] : []),
  { value: 'logs', label: '请求明细' },
])
watch(
  () => session.context?.features.api_keys,
  (enabled) => {
    if (!enabled && view.value === 'api_key_usage') view.value = 'stats'
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
const request = useRequest((signal) => statisticsApi.list(view.value, query.value, signal))
watch(view, () => {
  request.data.value = undefined
})
const candidates = useRequest(statisticsApi.candidates)
const action = useAction()
const lifetime = new AbortController()
onBeforeUnmount(() => lifetime.abort())
const importing = ref(false)
watch(query, () => void request.refresh(), { immediate: true })
watch(request.data, (result) => {
  if (
    view.value === 'logs' &&
    result &&
    page.value > Math.max(1, Math.ceil(result.total / pageSize.value))
  )
    page.value = Math.max(1, Math.ceil(result.total / pageSize.value))
})
void candidates.refresh()
function apply() {
  errors.value = dateErrors(draft)
  if (errors.value.start || errors.value.end) return
  page.value = 1
  applied.value = {
    ...draft,
    username: [...draft.username],
    request_model: [...draft.request_model],
  }
}
function preset(days: number) {
  const range = datePreset(days)
  draft.start_date = range.start_date
  draft.end_date = range.end_date
  apply()
}
async function importData(value: unknown, signal: AbortSignal) {
  const result = await statisticsApi.importData(value, signal)
  return `请求明细新增 ${result.request_logs_inserted_count} 条、重复 ${result.request_logs_skipped_count} 条；日聚合新增 ${result.daily_request_stats_inserted_count} 条、合并 ${result.daily_request_stats_merged_count} 条`
}
function imported(message: string) {
  notify(message)
  void request.refresh()
  void candidates.refresh()
}
</script>
<template>
  <div class="page">
    <header class="toolbar">
      <h1>统计与日志</h1>
      <div class="actions">
        <UiButton icon="upload" @click="importing = true">导入数据</UiButton>
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
          <p>
            导出数据生成可再次导入的 JSON，请求明细与日聚合按已查询的筛选条件导出。导出 Excel
            对应当前统计页签，导出范围不局限于当前分页。
          </p>
          <p>
            更改日期或筛选后先点查询，再导出。导入会跳过相同请求明细；相同维度的日聚合按数值累加，重复导入会再次累加，请查看完成消息中的新增、重复与合并数量。
          </p>
        </UiHelpPopover>
      </div>
    </header>
    <form class="statistics-filters surface" @submit.prevent="apply">
      <UiField label="开始日期" :error="errors.start">
        <UiInput v-model="draft.start_date" type="date" required />
      </UiField>
      <UiField
        label="结束日期"
        :error="errors.end"
        help="范围包含开始日期和结束日期当天，最多一年。默认查询今天；调整日期与多选项后点击查询，列表和导出才使用新的条件。"
      >
        <UiInput v-model="draft.end_date" type="date" :min="draft.start_date" required />
      </UiField>
      <UiField label="用户">
        <UiMultiSelect
          v-model="draft.username"
          :options="(candidates.data.value?.users || []).map((value) => ({ value, label: value }))"
          placeholder="全部用户"
        />
      </UiField>
      <UiField
        label="请求模型"
        help="按客户端发来的原始模型 ID 筛选，不是映射目标或上游返回的模型名。可多选，留空表示全部模型。"
      >
        <UiMultiSelect
          v-model="draft.request_model"
          :options="(candidates.data.value?.models || []).map((value) => ({ value, label: value }))"
          placeholder="全部模型"
        />
      </UiField>
      <UiButton type="submit" variant="primary" :loading="request.loading.value">查询</UiButton>
      <div class="actions statistics-presets">
        <UiButton
          v-for="item in [
            { days: 1, label: '今天' },
            { days: 7, label: '近 7 天' },
            { days: 30, label: '近 30 天' },
          ]"
          :key="item.days"
          size="sm"
          variant="ghost"
          :aria-pressed="
            draft.start_date === datePreset(item.days).start_date &&
            draft.end_date === datePreset(item.days).end_date
          "
          @click="preset(item.days)"
        >
          {{ item.label }}
        </UiButton>
      </div>
    </form>
    <UiAlert v-if="request.error.value || candidates.error.value || action.error.value">
      {{ request.error.value || action.error.value || candidates.error.value }}
    </UiAlert>
    <UiTabs
      :model-value="view"
      :items="tabs"
      label="统计视图"
      @update:model-value="view = $event as StatisticsView"
    />
    <StatisticsTable
      :key="view"
      v-model:sort="sort"
      :rows="request.data.value?.rows || []"
      :view="view"
      :loading="request.loading.value"
    />
    <UiPagination
      v-if="view === 'logs'"
      v-model:page="page"
      v-model:page-size="pageSize"
      :sizes="[50, 100, 200]"
      :total="request.data.value?.total || 0"
      :disabled="request.loading.value"
    />
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
  grid-template-columns: 160px 160px minmax(150px, 1fr) minmax(180px, 1.4fr) auto;
  gap: var(--space-3);
  align-items: start;
  padding: var(--space-4);
}

.statistics-filters > button {
  margin-top: 28px;
}

.statistics-presets {
  grid-column: 1 / -1;
}

@media (width <= 1200px) {
  .statistics-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .statistics-filters > button {
    margin-top: 0;
  }

  .statistics-presets {
    grid-column: auto;
  }
}

@media (width <= 480px) {
  .statistics-filters {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
