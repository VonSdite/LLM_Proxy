<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { OAuthFile } from '@/api/oauth'
import { dateTime, number, tokenK } from '@/features/shared/format'
import {
  availableCards,
  cost,
  fileCategory,
  fileLabels,
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
  { label: '当前', value: props.file.current_usage },
  ...(props.file.previous_usage ? [{ label: '上期', value: props.file.previous_usage }] : []),
])
function time(value: number | string | null) {
  const at = timestamp(value)
  return at === null ? '—' : dateTime(new Date(at).toISOString())
}
</script>
<template>
  <article class="oauth-file surface" :class="{ 'oauth-file-disabled': file.enabled === false }">
    <header class="oauth-file-header">
      <UiCheckbox
        :model-value="selected"
        :label="`选择 ${file.name}`"
        @update:model-value="emit('select', $event)"
      />
      <div class="oauth-file-title">
        <strong :title="file.name">{{ file.name }}</strong>
        <small class="muted">{{ file.email || '无邮箱' }}</small>
      </div>
      <UiSwitch
        :model-value="file.enabled !== false"
        :label="`启用 ${file.name}`"
        :disabled="disabled"
        @update:model-value="emit('enabled', $event)"
      />
      <UiConfirmPopover :message="`删除认证文件 ${file.name}？`" @confirm="emit('remove')">
        <UiIconButton icon="delete" :label="`删除 ${file.name}`" :disabled="disabled" />
      </UiConfirmPopover>
    </header>
    <div class="toolbar">
      <div class="actions">
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
        <UiBadge>{{ file.plan_type || '未知套餐' }}</UiBadge>
        <UiHelpPopover label="认证文件详情">
          <p>路径：{{ file.path }}</p>
          <p>Token 到期：{{ dateTime(file.expired) }}</p>
          <p v-if="file.usage_status_message && file.usage_status_message !== 'success'">
            {{ file.usage_status_message }}
          </p>
          <p v-if="file.availability_retry_at">
            预计恢复：{{ dateTime(file.availability_retry_at) }}
          </p>
        </UiHelpPopover>
      </div>
      <UiIconButton
        icon="refresh"
        :label="`刷新 ${file.name} 额度`"
        :loading="loading"
        :disabled="disabled"
        @click="emit('refresh')"
      />
    </div>
    <UiAlert v-if="error || file.quota_error">{{ error || file.quota_error }}</UiAlert>
    <div v-if="windows.length" class="quota-windows">
      <UiMeter
        v-for="window in windows"
        :key="window.label"
        :label="window.label"
        :value="window.remaining_percent"
      >
        {{ time(window.reset_at) }} 重置
      </UiMeter>
    </div>
    <p v-else class="muted quota-empty">尚无额度快照</p>
    <div class="oauth-usage">
      <div v-for="row in usage" :key="row.label" class="oauth-usage-row">
        <span class="muted">{{ row.label }}</span>
        <span>{{ number(row.value?.request_count) }} 次</span>
        <span>{{ tokenK(row.value?.total_tokens) }}k Tokens</span>
        <span>{{ cost(row.value?.estimated_cost_usd) }}</span>
        <UiHelpPopover :label="`${row.label}费用说明`">
          {{ windowName(row.value?.limit_window_seconds) }}预估
          {{ cost(row.value?.estimated_full_cost_usd) }}。累计金额为本地请求的 API
          等价费用，预估按本地额度消耗折算。
        </UiHelpPopover>
      </div>
    </div>
    <footer class="oauth-file-footer">
      <small class="muted">
        {{ file.quota_refreshed_at ? `更新于 ${dateTime(file.quota_refreshed_at)}` : '未刷新' }}
      </small>
      <div v-if="cards.length" class="reset-cards">
        <UiSelect
          v-model="selectedCard"
          :options="
            cards.map((card, index) => ({
              value: card.id,
              label: `${card.title || card.label || card.name || `重置卡 ${index + 1}`} · ${time(card.expires_at)}`,
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
      <small v-else class="muted">
        {{ file.reset_cards_refreshed_at ? '无可用重置卡' : '重置卡未查询' }}
      </small>
    </footer>
  </article>
</template>
<style scoped>
.oauth-file {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-4);
  align-content: start;
}

.oauth-file-disabled {
  background: color-mix(in srgb, var(--surface) 70%, var(--background));
}

.oauth-file-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.oauth-file-title {
  display: grid;
  min-width: 0;
  gap: var(--space-1);
  margin-right: auto;
}

.oauth-file-title strong {
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
  font-weight: 600;
}

.oauth-file-title small {
  overflow-wrap: anywhere;
}

.quota-windows {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

.quota-empty {
  font-size: var(--text-xs);
  padding-block: var(--space-3);
}

.oauth-usage {
  display: grid;
  gap: var(--space-1);
}

.oauth-usage-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}

.oauth-file-footer {
  display: grid;
  gap: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px solid var(--border);
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

@media (width <= 450px) {
  .quota-windows {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
