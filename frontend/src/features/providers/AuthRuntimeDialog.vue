<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { authGroupsApi, type AuthRuntimeEntry } from '@/api/providers'
import { useRequest } from '@/features/shared/useRequest'
import { useAction } from '@/features/shared/useAction'
import { dateTime, number, tokenK } from '@/features/shared/format'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmPopover,
  UiDialog,
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
const detail = ref<AuthRuntimeEntry>()
onMounted(request.refresh)
const statuses: Record<string, string> = {
  available: '可用',
  disabled: '已禁用',
  cooldown: '冷却中',
  quota_exceeded: '额度耗尽',
  saturated: '并发已满',
}
const columns: TableColumn[] = [
  { key: 'id', label: 'Entry ID', width: 160 },
  { key: 'status', label: '状态', width: 100 },
  { key: 'inflight', label: '并发 / 上限', width: 100 },
  { key: 'cooldown_until', label: '冷却截止', width: 180 },
  { key: 'requests', label: '请求 · 分钟 / 天', width: 150 },
  { key: 'tokens', label: 'Token (k) · 分钟 / 天', width: 180 },
  { key: 'actions', label: '操作', width: 180 },
]
</script>
<template>
  <UiDialog
    open
    :title="`${name} · 运行状态`"
    size="xl"
    :busy="action.busy.value"
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
        <UiButton
          size="sm"
          icon="refresh"
          :loading="request.loading.value"
          @click="request.refresh"
        >
          刷新
        </UiButton>
      </div>
      <UiAlert v-if="request.error.value || action.error.value">
        {{ action.error.value || request.error.value }}
      </UiAlert>
      <UiTable
        :rows="request.data.value?.entries || []"
        :columns="columns"
        :row-key="(row) => row.id"
        :loading="request.loading.value"
        storage-key="auth-runtime"
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
          {{ number(row.minute_request_count) }} / {{ number(row.day_request_count) }}
        </template>
        <template #cell-tokens="{ row }">
          {{ tokenK(row.minute_total_tokens) }} / {{ tokenK(row.day_total_tokens) }}
        </template>
        <template #cell-actions="{ row }">
          <div class="actions">
            <UiSwitch
              :model-value="!row.disabled"
              :label="`启用 ${row.id}`"
              :disabled="!row.enabled || action.busy.value"
              @update:model-value="
                action.run((signal) =>
                  authGroupsApi.entryAction(name, row.id, $event ? 'enable' : 'disable', signal),
                )
              "
            />
            <UiButton size="sm" variant="ghost" @click="detail = row">详情</UiButton>
            <UiConfirmPopover
              message="重置此条目的冷却、禁用状态和当前分钟用量？"
              confirm-label="重置"
              @confirm="
                action.run(
                  (signal) => authGroupsApi.entryAction(name, row.id, 'reset', signal),
                  '已重置运行状态',
                )
              "
            >
              <UiButton size="sm" :disabled="action.busy.value">重置</UiButton>
            </UiConfirmPopover>
          </div>
        </template>
      </UiTable>
    </div>
  </UiDialog>
  <UiDialog v-if="detail" open :title="`${detail.id} · 运行详情`" @update:open="detail = undefined">
    <dl class="runtime-details">
      <template
        v-for="item in [
          { label: '状态', value: statuses[detail.status] || detail.status },
          { label: 'HTTP 状态码', value: detail.last_status_code },
          { label: '错误类型', value: detail.last_error_type },
          { label: '错误信息', value: detail.last_error_message },
          { label: '禁用原因', value: detail.disabled_reason },
          { label: '更新时间', value: dateTime(detail.updated_at) },
          { label: '冷却截止', value: dateTime(detail.cooldown_until) },
          { label: '最大并发', value: detail.max_concurrency },
          { label: '请求 / 分钟', value: detail.request_quota_per_minute },
          { label: '请求 / 天', value: detail.request_quota_per_day },
          { label: 'Token / 分钟', value: detail.token_quota_per_minute },
          { label: 'Token / 天', value: detail.token_quota_per_day },
        ]"
        :key="item.label"
      >
        <dt class="muted">{{ item.label }}</dt>
        <dd>{{ item.value ?? '—' }}</dd>
      </template>
    </dl>
  </UiDialog>
</template>
<style scoped>
.runtime-details {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-sm);
}

.runtime-details dd {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
