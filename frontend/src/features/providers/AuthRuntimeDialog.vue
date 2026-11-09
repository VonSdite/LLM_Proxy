<script setup lang="ts">
import { onMounted } from 'vue'
import { authGroupsApi, type AuthRuntimeEntry } from '@/api/providers'
import { useRequest } from '@/features/shared/useRequest'
import { useAction } from '@/features/shared/useAction'
import { useToggle } from '@/features/shared/useToggle'
import { dateTime, number, tokenK } from '@/features/shared/format'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmPopover,
  UiDialog,
  UiIconButton,
  UiSwitch,
  UiTable,
  type TableColumn,
} from '@/components/ui'
const props = defineProps<{ name: string }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const request = useRequest((signal) => authGroupsApi.runtime(props.name, signal))
const action = useAction(async () => {
  await request.refresh()
  emit('changed')
})
const toggles = useToggle()
function disabledReason(entry: AuthRuntimeEntry) {
  const reason = entry.disabled_reason
  if (!reason || reason === 'manual_disabled' || reason === `http_${entry.last_status_code}`)
    return ''
  return reason.replace(/^http_(\d+)$/, 'HTTP $1')
}
function setEnabled(id: string, value: boolean) {
  return toggles.run(
    id,
    value,
    async (signal) => {
      await authGroupsApi.entryAction(props.name, id, value ? 'enable' : 'disable', signal)
      signal.throwIfAborted()
      return authGroupsApi.runtime(props.name, signal)
    },
    (result) => {
      request.patch(() => result)
      emit('changed')
    },
    props.name,
  )
}
onMounted(request.refresh)
const statuses: Record<string, string> = {
  available: '可用',
  disabled: '已禁用',
  cooldown: '冷却中',
  quota_exceeded: '额度耗尽',
  saturated: '并发已满',
}
const columns: TableColumn[] = [
  { key: 'id', label: 'Entry ID', width: 130 },
  {
    key: 'status',
    label: '状态',
    width: 90,
    help: [
      '可用：参与调度。冷却、额度耗尽或并发已满时暂时跳过。',
      '401 / 403：条目被禁用，处理凭据后可重新启用。',
      '配置中已禁用：需先编辑认证组并启用该条目。',
    ],
  },
  { key: 'inflight', label: '并发 / 上限', width: 100 },
  { key: 'cooldown_until', label: '冷却截止', width: 130 },
  {
    key: 'requests',
    label: '请求用量 / 上限',
    width: 155,
    help: [
      '统计范围：当前自然分钟 / 自然日已发出的上游请求，包含失败尝试。',
      '时间边界：按服务本地时间计算。',
      '每行：已用请求数 / 配置上限；∞ 表示未设上限。',
    ],
  },
  {
    key: 'tokens',
    label: 'Token (k) · 分钟 / 天',
    width: 170,
    help: [
      '统计范围：当前自然分钟 / 自然日累计的输入与输出 Token。',
      '来源与单位：按上游返回的用量统计，1k = 1000 Tokens。',
      '用途：仅供观察用量，不参与限速。',
    ],
  },
  {
    key: 'result',
    label: '最近结果',
    width: 250,
    minWidth: 200,
    grow: 2,
    help: [
      '内容：最近记录的 HTTP 状态码、错误类型、错误信息及更新时间。',
      '状态区别：这些是历史请求信息，当前是否可用以状态列为准。',
      '禁用原因：显示上游自动禁用等原因；手动禁用只在状态和开关中体现。',
    ],
  },
  {
    key: 'actions',
    label: '操作',
    width: 120,
    minWidth: 120,
    fixed: true,
    help: [
      '开关：控制运行时禁用标记，不改写认证组的配置启用值。',
      '重置：清除运行故障状态及当前分钟、当天用量。',
      '上游配额：重置不会重新发放上游配额。',
    ],
  },
]
</script>
<template>
  <UiDialog
    open
    :title="`${name} · 运行状态`"
    size="xl"
    placement="top"
    :busy="action.busy.value || toggles.busy.value"
    @update:open="emit('close')"
  >
    <div class="stack">
      <div class="toolbar">
        <div class="actions">
          <UiBadge v-if="request.data.value" variant="success">
            可用 {{ request.data.value.summary.available_count }}
          </UiBadge>
          <UiBadge v-if="request.data.value" variant="warning">
            冷却 {{ request.data.value.summary.cooldown_count }}
          </UiBadge>
          <UiBadge v-if="request.data.value">
            禁用 {{ request.data.value.summary.disabled_count }}
          </UiBadge>
        </div>
        <UiIconButton
          icon="refresh"
          label="刷新运行状态"
          :loading="request.loading.value"
          :disabled="action.busy.value || toggles.busy.value"
          @click="request.refresh"
        />
      </div>
      <UiAlert v-if="request.error.value || action.error.value">
        {{ action.error.value || request.error.value }}
      </UiAlert>
      <UiTable
        :rows="request.data.value?.entries || []"
        :columns="columns"
        :row-key="(row) => row.id"
        :loading="request.loading.value"
        storage-key="auth-runtime-results"
      >
        <template #cell-status="{ row }">
          <UiBadge
            :variant="
              row.status === 'available'
                ? 'success'
                : row.status === 'disabled'
                  ? 'danger'
                  : 'warning'
            "
          >
            {{ statuses[row.status] || row.status }}
          </UiBadge>
        </template>
        <template #cell-inflight="{ row }">
          {{ row.inflight }} / {{ row.max_concurrency ?? '∞' }}
        </template>
        <template #cell-cooldown_until="{ row }">
          <small>{{ dateTime(row.cooldown_until) }}</small>
        </template>
        <template #cell-requests="{ row }">
          <div class="runtime-usage">
            <span class="muted">分钟</span>
            <span>
              {{ number(row.minute_request_count) }} /
              {{
                row.request_quota_per_minute == null ? '∞' : number(row.request_quota_per_minute)
              }}
            </span>
            <span class="muted">当天</span>
            <span>
              {{ number(row.day_request_count) }} /
              {{ row.request_quota_per_day == null ? '∞' : number(row.request_quota_per_day) }}
            </span>
          </div>
        </template>
        <template #cell-tokens="{ row }">
          {{ tokenK(row.minute_total_tokens) }} / {{ tokenK(row.day_total_tokens) }}
        </template>
        <template #cell-result="{ row }">
          <div class="runtime-result">
            <div
              v-if="row.last_status_code != null || row.last_error_type"
              class="runtime-result-heading"
            >
              <UiBadge
                v-if="row.last_status_code != null"
                :variant="row.last_status_code >= 400 ? 'danger' : 'neutral'"
              >
                HTTP {{ row.last_status_code }}
              </UiBadge>
              <span v-if="row.last_error_type">{{ row.last_error_type }}</span>
            </div>
            <div v-if="row.last_error_message" class="runtime-error">
              {{ row.last_error_message }}
            </div>
            <div v-if="disabledReason(row)">禁用原因：{{ disabledReason(row) }}</div>
            <small v-if="row.updated_at" class="muted">更新 {{ dateTime(row.updated_at) }}</small>
            <span
              v-else-if="
                !row.last_status_code &&
                !row.last_error_type &&
                !row.last_error_message &&
                !disabledReason(row)
              "
              class="muted"
            >
              —
            </span>
          </div>
        </template>
        <template #cell-actions="{ row }">
          <div class="actions">
            <UiSwitch
              :model-value="toggles.value(row.id, !row.disabled)"
              :loading="toggles.pending(row.id)"
              :label="`启用 ${row.id}`"
              :disabled="
                !row.enabled ||
                action.busy.value ||
                (toggles.busy.value && !toggles.pending(row.id))
              "
              @update:model-value="setEnabled(row.id, $event)"
            />
            <UiConfirmPopover
              message="重置此条目？"
              :description="['清除冷却与运行时禁用状态', '清零当前分钟和当天用量']"
              confirm-label="重置"
              @confirm="
                action.run(
                  (signal) => authGroupsApi.entryAction(name, row.id, 'reset', signal),
                  '已重置运行状态',
                )
              "
            >
              <UiButton size="sm" :disabled="action.busy.value || toggles.busy.value">
                重置
              </UiButton>
            </UiConfirmPopover>
          </div>
        </template>
      </UiTable>
    </div>
  </UiDialog>
</template>
<style scoped>
.runtime-usage {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--space-1) var(--space-2);
}

.runtime-result {
  display: grid;
  gap: var(--space-1);
}

.runtime-result-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1);
}

.runtime-error {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
