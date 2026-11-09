<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import type { OAuthFile, QuotaWindow } from '@/api/oauth'
import { dateTime, number, tokenK } from '@/features/shared/format'
import {
  availableCards,
  cost,
  fileCategory,
  fileLabels,
  quotaDue,
  quotaWindows,
  timestamp,
  windowName,
} from './quota'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiCheckbox,
  UiConfirmPopover,
  UiHelpPopover,
  UiIconButton,
  UiMeter,
  UiSelect,
  UiSwitch,
} from '@/components/ui'
const props = defineProps<{
  file: OAuthFile
  selected: boolean
  disabled: boolean
  loading: boolean
  enabled?: boolean
  toggling?: boolean
  toggleDisabled?: boolean
  now: number
  error?: string
}>()
const emit = defineEmits<{
  select: [value: boolean]
  enabled: [value: boolean]
  remove: []
  refresh: []
  consume: [id: string]
}>()
const category = computed(() => fileCategory(props.file, props.now))
const windows = computed(() => quotaWindows(props.file.quota))
const cards = computed(() => availableCards(props.file.reset_cards || [], props.now))
const issue = computed(() => props.error || props.file.quota_error)
const issueTitle = computed(() =>
  props.file.availability_status === 'auth_failed'
    ? '认证失败，请重新登录'
    : props.file.quota_error
      ? '额度暂不可用'
      : '操作未完成',
)
const historicalQuota = computed(
  () => windows.value.length > 0 && (!!props.file.quota_error || quotaDue(props.file, props.now)),
)
const updatedAt = computed(() => props.file.quota_refreshed_at || props.file.quota?.refreshed_at)
const issueOpen = ref(false)
const issueId = useId()
const selectedCard = ref('')
watch(
  cards,
  (value) => {
    if (!value.some((card) => card.id === selectedCard.value))
      selectedCard.value = value[0]?.id || ''
  },
  { immediate: true },
)
const usage = computed(() => [
  { label: '本期', value: props.file.current_usage },
  ...(props.file.previous_usage ? [{ label: '上期', value: props.file.previous_usage }] : []),
])
function time(value: number | string | null) {
  const at = timestamp(value)
  return at === null ? '—' : dateTime(new Date(at).toISOString())
}
function shortTime(value: number | string | null | undefined) {
  const at = timestamp(value)
  if (at === null) return '—'
  const date = new Date(at)
  return date.toLocaleString('zh-CN', {
    year: date.getFullYear() === new Date(props.now).getFullYear() ? undefined : 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}
function quotaLabel(window: QuotaWindow) {
  return window.limit_window_seconds
    ? windowName(window.limit_window_seconds)
    : window.label.replace(/^codex\s*/i, '') || window.label
}
function resetLabel(window: QuotaWindow) {
  const at = timestamp(window.reset_at)
  if (at === null) return '重置时间未知'
  return at <= props.now ? '等待刷新' : `${shortTime(at)} 重置`
}
</script>
<template>
  <article
    class="oauth-file surface"
    :class="{ 'oauth-file-disabled': file.enabled === false, 'oauth-file-selected': selected }"
    :aria-label="file.name"
  >
    <header class="oauth-file-header">
      <UiCheckbox
        :model-value="selected"
        :label="`选择 ${file.name}`"
        @update:model-value="emit('select', $event)"
      />
      <div class="oauth-file-title">
        <strong :title="file.email || file.name">{{ file.email || file.name }}</strong>
        <span v-if="file.email" class="oauth-file-name muted" :title="file.name">
          {{ file.name }}
        </span>
      </div>
      <UiSwitch
        :model-value="enabled ?? file.enabled !== false"
        :loading="toggling"
        :label="`启用 ${file.name}`"
        :disabled="toggleDisabled ?? disabled"
        @update:model-value="emit('enabled', $event)"
      />
    </header>
    <div class="oauth-file-meta">
      <div class="oauth-file-status">
        <UiBadge
          :variant="
            category === 'available'
              ? 'success'
              : category === 'auth_failed'
                ? 'danger'
                : category === 'quota_exhausted'
                  ? 'warning'
                  : 'neutral'
          "
        >
          {{ fileLabels[category] }}
        </UiBadge>
        <UiBadge v-if="file.plan_type" class="oauth-file-plan">{{ file.plan_type }}</UiBadge>
        <UiHelpPopover label="认证文件详情">
          <p>文件：{{ file.name }}</p>
          <p>路径：{{ file.path }}</p>
          <p>Token 到期：{{ dateTime(file.expired) }}</p>
          <p v-if="file.usage_status_message && file.usage_status_message !== 'success'">
            {{ file.usage_status_message }}
          </p>
          <p v-if="file.availability_retry_at">
            预计恢复：{{ dateTime(file.availability_retry_at) }}
          </p>
          <p v-if="historicalQuota">
            上次额度来自已保存的快照。额度查询失败或窗口到期时，数值不代表账号目前仍可使用的额度。
          </p>
        </UiHelpPopover>
      </div>
      <div class="oauth-file-actions">
        <UiIconButton
          icon="refresh"
          :label="`刷新 ${file.name} 额度`"
          :loading="loading"
          :disabled="disabled"
          @click="emit('refresh')"
        />
        <UiConfirmPopover :message="`删除认证文件 ${file.name}？`" @confirm="emit('remove')">
          <UiIconButton icon="delete" :label="`删除 ${file.name}`" :disabled="disabled" />
        </UiConfirmPopover>
      </div>
    </div>
    <UiAlert v-if="issue">
      <div class="oauth-issue-heading">
        <span>{{ issueTitle }}</span>
        <UiButton
          size="sm"
          variant="ghost"
          :aria-expanded="issueOpen"
          :aria-controls="issueId"
          @click="issueOpen = !issueOpen"
        >
          {{ issueOpen ? '收起' : '查看原因' }}
        </UiButton>
      </div>
      <p v-if="issueOpen" :id="issueId" class="oauth-issue-details">{{ issue }}</p>
    </UiAlert>
    <div class="quota-section">
      <div class="quota-heading">
        <span>{{ historicalQuota ? '上次额度' : '剩余额度' }}</span>
        <span v-if="updatedAt" class="muted" :title="`更新于 ${dateTime(updatedAt)}`">
          {{ shortTime(updatedAt) }} 更新
        </span>
      </div>
      <div v-if="windows.length" class="quota-windows">
        <UiMeter
          v-for="window in windows"
          :key="window.label"
          :label="quotaLabel(window)"
          :value="window.remaining_percent"
          :muted="historicalQuota"
        >
          <span :title="`重置时间：${time(window.reset_at)}`">{{ resetLabel(window) }}</span>
        </UiMeter>
      </div>
      <p v-else class="muted quota-empty">暂无额度数据</p>
    </div>
    <div class="oauth-usage">
      <div v-for="row in usage" :key="row.label" class="oauth-usage-row">
        <div class="oauth-usage-period">
          <span class="muted">{{ row.label }}</span>
          <UiHelpPopover :label="`${row.label}费用说明`">
            <p>
              {{ windowName(row.value?.limit_window_seconds) }}整窗预估
              {{ cost(row.value?.estimated_full_cost_usd) }}。
            </p>
            <p>
              本期 / 上期对应本地记录的额度窗口。次数、Token
              和累计金额只统计经过本代理的调用，金额按模型 API 价格折算，不是账号账单或实际扣费。
            </p>
            <p>
              整窗预估按本地调用金额与观测到的额度消耗折算；其他客户端的使用、缺失用量或价格信息会影响估算，—
              表示暂无可靠结果。
            </p>
          </UiHelpPopover>
        </div>
        <dl class="oauth-usage-values">
          <div>
            <dt>请求</dt>
            <dd>
              {{ number(row.value?.request_count) }}
              <span class="muted">次</span>
            </dd>
          </div>
          <div>
            <dt>Token</dt>
            <dd>
              {{ tokenK(row.value?.total_tokens) }}
              <span v-if="row.value" class="muted">k</span>
            </dd>
          </div>
          <div>
            <dt>预估费用</dt>
            <dd>{{ cost(row.value?.estimated_cost_usd) }}</dd>
          </div>
        </dl>
      </div>
    </div>
    <footer v-if="cards.length" class="oauth-file-footer">
      <div class="oauth-reset-heading">
        <span>重置卡</span>
        <UiHelpPopover label="额度重置卡说明">
          <p>上游账号提供的额度重置权益。下拉框显示名称和过期时间，已过期的卡自动隐藏。</p>
          <p>点击使用并确认后会真实消费所选卡；成功后重新查询额度和卡列表，不只是重置本页显示。</p>
        </UiHelpPopover>
      </div>
      <div class="reset-cards">
        <UiSelect
          v-model="selectedCard"
          :options="
            cards.map((card, index) => ({
              value: card.id,
              label: `${card.title || card.label || card.name || `重置卡 ${index + 1}`}${timestamp(card.expires_at) === null ? '' : ` · ${shortTime(card.expires_at)} 到期`}`,
            }))
          "
          placeholder="选择重置卡"
          aria-label="选择额度重置卡"
          :disabled="disabled || file.enabled === false || category === 'auth_failed'"
        />
        <UiConfirmPopover
          message="使用所选重置卡恢复此账号额度？"
          confirm-label="使用"
          @confirm="emit('consume', selectedCard)"
        >
          <UiButton
            size="sm"
            :disabled="
              !selectedCard || disabled || file.enabled === false || category === 'auth_failed'
            "
          >
            使用
          </UiButton>
        </UiConfirmPopover>
      </div>
    </footer>
  </article>
</template>
<style scoped>
.oauth-file {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-5);
  align-content: start;
  min-width: 0;
}

.oauth-file-disabled {
  background: color-mix(in srgb, var(--surface) 70%, var(--background));
}

.oauth-file-selected {
  border-color: var(--link);
}

.oauth-file-header {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-3);
}

.oauth-file-title {
  display: grid;
  min-width: 0;
  gap: var(--space-1);
}

.oauth-file-title strong {
  font-size: var(--text-base);
}

.oauth-file-title strong,
.oauth-file-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.oauth-file-name {
  font-size: var(--text-sm);
}

.oauth-file-meta,
.quota-heading,
.oauth-issue-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.oauth-file-status,
.oauth-file-actions,
.oauth-usage-period,
.oauth-reset-heading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.oauth-file-status {
  flex-wrap: wrap;
}

.oauth-file-plan {
  text-transform: capitalize;
}

.oauth-file-actions {
  flex-shrink: 0;
  gap: var(--space-1);
}

.oauth-issue-details {
  margin-top: var(--space-2);
  white-space: pre-wrap;
}

.quota-section {
  display: grid;
  gap: var(--space-3);
}

.quota-heading {
  flex-wrap: wrap;
  font-size: var(--text-sm);
}

.quota-heading > .muted {
  font-size: var(--text-xs);
}

.quota-windows {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr));
  gap: var(--space-4);
}

.quota-empty {
  font-size: var(--text-sm);
  padding-block: var(--space-3);
}

.oauth-usage {
  display: grid;
  gap: var(--space-3);
  margin-top: var(--space-1);
}

.oauth-usage-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}

.oauth-usage-period {
  gap: var(--space-1);
}

.oauth-usage-values {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  margin: 0;
}

.oauth-usage-values dt {
  color: var(--muted);
  margin-bottom: var(--space-1);
}

.oauth-usage-values dd {
  margin: 0;
  font-size: var(--text-base);
  font-weight: 500;
}

.oauth-file-footer {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--divider);
  font-size: var(--text-sm);
}

.reset-cards {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.reset-cards > :first-child {
  flex: 1;
  min-width: 0;
}

@media (width <= 600px) {
  .oauth-file {
    padding: var(--space-4);
  }

  .oauth-usage-row {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-2);
  }
}
</style>
