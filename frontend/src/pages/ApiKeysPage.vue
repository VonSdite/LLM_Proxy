<script setup lang="ts">
import FeatureSettings from '@/features/settings/FeatureSettings.vue'
import { computed, ref } from 'vue'
import { apiKeysApi, type ApiKey } from '@/api/api-keys'
import { usePagedList } from '@/features/shared/usePagedList'
import { useAction } from '@/features/shared/useAction'
import { useToggle } from '@/features/shared/useToggle'
import type { RowMove } from '@/features/shared/order'
import { copyText, dateTime, number, tokenK } from '@/features/shared/format'
import ApiKeyDialog from '@/features/api-keys/ApiKeyDialog.vue'
import {
  UiDisabledRegion,
  UiAlert,
  UiButton,
  UiBadge,
  UiConfirmPopover,
  UiIconButton,
  UiListToolbar,
  UiPageHeader,
  UiPagination,
  UiPopover,
  UiSearchInput,
  UiSelectionBar,
  UiSwitch,
  UiTable,
  UiTooltip,
  notify,
  type TableColumn,
} from '@/components/ui'
const featureAvailable = ref(false)
const list = usePagedList(apiKeysApi.list, { key: 'sort_order', direction: 'asc' })
const action = useAction(list.refresh)
const toggles = useToggle()
const clipboard = useAction()
const reorderDisabled = computed(
  () =>
    action.busy.value ||
    toggles.busy.value ||
    list.filterPending.value ||
    !!list.keyword.value ||
    list.sort.value.key !== 'sort_order',
)
async function reorder(move: RowMove) {
  if (reorderDisabled.value) return
  await action.run((signal) =>
    apiKeysApi.order(
      { id: Number(move.id), target_id: Number(move.targetId), after: move.after },
      signal,
    ),
  )
}
const editing = ref<ApiKey>()
const formOpen = ref(false)
const revealed = ref<number>()
const selectedRows = computed(() =>
  list.rows.value.filter((row) => list.selected.value.includes(String(row.id))),
)
async function batch(operation: 'enable' | 'disable' | 'delete') {
  if (toggles.busy.value) return
  const rows = [...selectedRows.value]
  const done = await action.run(
    async (signal) => {
      for (const row of rows) {
        if (signal.aborted) return
        if (operation === 'delete') await apiKeysApi.remove(row.id, signal)
        else if (Boolean(row.enabled) !== (operation === 'enable'))
          await apiKeysApi.update(row.id, { enabled: operation === 'enable' }, signal)
        if (signal.aborted) return
        list.patch((data) => ({
          ...data,
          items:
            operation === 'delete'
              ? data.items.filter((item) => item.id !== row.id)
              : data.items.map((item) =>
                  item.id === row.id ? { ...item, enabled: operation === 'enable' } : item,
                ),
          total: operation === 'delete' ? data.total - 1 : data.total,
        }))
        list.selected.value = list.selected.value.filter((id) => id !== String(row.id))
      }
    },
    operation === 'delete'
      ? '已删除所选 API Key'
      : operation === 'enable'
        ? '已启用所选 API Key'
        : '已禁用所选 API Key',
  )
  if (done) list.selected.value = []
}
const nameWidth = computed(() =>
  Math.min(
    200,
    Math.max(
      90,
      ...list.rows.value.map((row) =>
        Array.from(row.name || '').reduce(
          (width, char) => width + (char.codePointAt(0)! > 255 ? 16 : 9),
          24,
        ),
      ),
    ),
  ),
)
const columns = computed<TableColumn[]>(() => [
  {
    key: 'name',
    label: '名称',
    sortable: true,
    width: nameWidth.value,
    minWidth: 90,
    maxWidth: nameWidth.value,
  },
  {
    key: 'api_key',
    label: 'API Key',
    width: 280,
    minWidth: 280,
    maxWidth: 280,
    help: '供客户端访问本代理服务的凭据，使用 Authorization: Bearer 密钥 或 X-API-Key 请求头。不是 Provider 的上游密钥。',
  },
  { fixed: true, key: 'enabled', label: '启用', width: 64, minWidth: 64 },
  { key: 'allowed_models_count', label: '模型权限', sortable: true, width: 120, grow: 1 },
  {
    key: 'limits',
    grow: 0.8,
    label: '请求限制',
    width: 120,
    help: [
      '并发：同一 Key 同时处理的推理请求上限，跨模型共享；达到上限返回 429。',
      '每分钟 / 每天：按服务本地时间的自然分钟、自然日计数，上游失败也计入，内部重试不重复计数；每日计数重启不清零。',
      '不限表示对应项目未设置上限。',
    ],
  },
  {
    key: 'token_limit_k',
    grow: 1.6,
    label: '用量 / 配额',
    sortable: true,
    width: 140,
    help: [
      '左侧是累计已用 Token，右侧是总配额，单位 k（1000 Tokens）；不限额表示未设总配额，配额不按月重置。悬浮可查看剩余配额。',
      '请求为累计请求次数。点击表头按总配额排序。',
    ],
  },
  { key: 'last_used_at', label: '最近使用', sortable: true, width: 120, grow: 1 },
  { fixed: true, key: 'actions', label: '操作', width: 88, minWidth: 88 },
])
function edit(key?: ApiKey) {
  if (toggles.busy.value || action.busy.value) return
  editing.value = key
  formOpen.value = true
}
async function saved() {
  notify('已保存 API Key')
  await list.refresh()
}
function setEnabled(key: ApiKey, value: boolean) {
  return toggles.run(
    String(key.id),
    value,
    (signal) => apiKeysApi.update(key.id, { enabled: value }, signal),
    () =>
      list.patch((data) => ({
        ...data,
        items: data.items.map((row) => (row.id === key.id ? { ...row, enabled: value } : row)),
      })),
  )
}
</script>
<template>
  <div class="page">
    <UiPageHeader title="API Key">
      <template #controls>
        <FeatureSettings group="api_keys" @availability="featureAvailable = $event" />
      </template>
    </UiPageHeader>
    <UiDisabledRegion :disabled="!featureAvailable">
      <UiListToolbar :selecting="list.selected.value.length > 0">
        <template #selection>
          <UiSelectionBar
            v-if="list.selected.value.length"
            embedded
            :count="list.selected.value.length"
            :disabled="action.busy.value || toggles.busy.value"
            @clear="list.selected.value = []"
          >
            <UiButton
              v-if="selectedRows.some((row) => !row.enabled)"
              :disabled="action.busy.value || toggles.busy.value"
              @click="batch('enable')"
            >
              启用
            </UiButton>
            <UiButton
              v-if="selectedRows.some((row) => row.enabled)"
              :disabled="action.busy.value || toggles.busy.value"
              @click="batch('disable')"
            >
              禁用
            </UiButton>
            <UiConfirmPopover
              :message="`删除所选 ${list.selected.value.length} 个 API Key？使用这些 Key 的请求将无法通过认证。`"
              @confirm="batch('delete')"
            >
              <UiButton
                variant="danger-outline"
                :disabled="action.busy.value || toggles.busy.value"
              >
                删除
              </UiButton>
            </UiConfirmPopover>
          </UiSelectionBar>
        </template>
        <template #actions>
          <UiButton
            variant="primary"
            icon="plus"
            :disabled="action.busy.value || toggles.busy.value"
            @click="edit()"
          >
            创建 API Key
          </UiButton>
          <UiButton
            v-if="list.sort.value.key !== 'sort_order'"
            :disabled="action.busy.value || toggles.busy.value"
            @click="list.sort.value = { key: 'sort_order', direction: 'asc' }"
          >
            自定义顺序
          </UiButton>
        </template>
        <template #search>
          <UiSearchInput
            v-model="list.keyword.value"
            placeholder="搜索名称或 Key"
            aria-label="搜索 API Key"
            autocomplete="off"
            data-1p-ignore
            data-lpignore="true"
          />
        </template>
        <UiIconButton
          icon="refresh"
          label="刷新 API Key"
          :loading="list.loading.value"
          @click="list.refresh"
        />
      </UiListToolbar>
      <UiAlert v-if="list.error.value">{{ list.error.value }}</UiAlert>
      <UiTable
        v-model:selected="list.selected.value"
        v-model:sort="list.sort.value"
        selectable
        reorderable
        :reorder-disabled="reorderDisabled"
        :rows="list.rows.value"
        :columns="columns"
        :row-key="(row) => String(row.id)"
        manual-sorting
        :loading="list.loading.value"
        storage-key="api-keys-balanced"
        empty="还没有 API Key"
        @reorder="reorder"
      >
        <template #header-actions><span class="key-actions-heading">操作</span></template>
        <template #cell-name="{ row }">
          <UiTooltip :text="row.name">
            <UiButton
              variant="link"
              size="title"
              class="key-name"
              aria-haspopup="dialog"
              :disabled="action.busy.value || toggles.busy.value"
              @click="edit(row)"
            >
              <span class="truncate">{{ row.name }}</span>
            </UiButton>
          </UiTooltip>
        </template>
        <template #cell-api_key="{ row }">
          <div class="key-cell">
            <code class="key-text">
              {{ `${row.key_prefix}••••${row.key_suffix}` }}
            </code>
            <UiPopover
              :open="revealed === row.id"
              title="API Key"
              compact
              side="top"
              @update:open="revealed = $event ? row.id : undefined"
            >
              <template #trigger>
                <UiIconButton
                  :icon="revealed === row.id ? 'eyeOff' : 'eye'"
                  :label="revealed === row.id ? '隐藏 Key' : '显示 Key'"
                />
              </template>
              <div class="key-reveal-content">
                <code class="key-full">{{ row.api_key }}</code>
                <UiIconButton
                  icon="copy"
                  label="复制完整 Key"
                  @click="clipboard.run(() => copyText(row.api_key), '已复制 API Key')"
                />
              </div>
            </UiPopover>
            <UiIconButton
              icon="copy"
              label="复制 Key"
              @click="clipboard.run(() => copyText(row.api_key), '已复制 API Key')"
            />
          </div>
        </template>
        <template #cell-enabled="{ row }">
          <UiSwitch
            :model-value="toggles.value(String(row.id), Boolean(row.enabled))"
            :loading="toggles.pending(String(row.id))"
            :label="`启用 ${row.name}`"
            :disabled="action.busy.value"
            @update:model-value="setEnabled(row, $event)"
          />
        </template>
        <template #cell-allowed_models_count="{ row }">
          <div class="key-permissions">
            <UiButton
              variant="link"
              :disabled="action.busy.value || toggles.busy.value"
              @click="edit(row)"
            >
              {{
                row.model_permissions === '*' ? '全部模型' : `${row.allowed_models_count} 个模型`
              }}
            </UiButton>
            <div
              v-if="Array.isArray(row.model_permissions) && row.model_permissions.length"
              class="key-models"
            >
              <UiBadge
                v-for="model in row.model_permissions.slice(0, 2)"
                :key="model"
                size="md"
                class="key-model"
                :title="model"
              >
                {{ model }}
              </UiBadge>
              <span v-if="row.model_permissions.length > 2" class="muted text-caption">
                +{{ row.model_permissions.length - 2 }}
              </span>
            </div>
          </div>
        </template>
        <template #cell-limits="{ row }">
          <span
            v-if="
              row.max_concurrency == null &&
              row.request_quota_per_minute == null &&
              row.request_quota_per_day == null
            "
            class="muted"
          >
            不限制
          </span>
          <div v-else class="key-limits">
            <span
              v-for="[field, label] in [
                ['max_concurrency', '并发'],
                ['request_quota_per_minute', '每分钟'],
                ['request_quota_per_day', '每天'],
              ] as const"
              :key="field"
              class="key-limit"
            >
              <span class="muted">{{ label }}</span>
              <span class="number">{{ row[field] == null ? '不限' : number(row[field]) }}</span>
            </span>
          </div>
        </template>
        <template #cell-token_limit_k="{ row }">
          <div class="key-usage">
            <span
              class="key-usage-ratio"
              :title="
                row.token_limit_k == null ? undefined : `剩余 ${tokenK(row.token_limit_remaining)}k`
              "
            >
              {{ tokenK(row.total_tokens) }}k /
              {{ row.token_limit_k == null ? '不限额' : `${number(row.token_limit_k)}k` }}
            </span>
            <span class="key-request-count">{{ number(row.total_request_count) }} 次请求</span>
          </div>
        </template>
        <template #cell-last_used_at="{ row }">
          <div class="key-details key-dates">
            <span>{{ dateTime(row.last_used_at) }}</span>
            <span class="muted">创建 {{ dateTime(row.created_at) }}</span>
          </div>
        </template>
        <template #cell-actions="{ row }">
          <div class="table-actions key-actions">
            <UiIconButton
              icon="edit"
              :label="`编辑 ${row.name}`"
              :disabled="action.busy.value || toggles.busy.value"
              @click="edit(row)"
            />
            <UiConfirmPopover
              message="是否删除？使用此 Key 的请求将无法通过认证。"
              @confirm="action.run((signal) => apiKeysApi.remove(row.id, signal), '已删除 API Key')"
            >
              <UiIconButton
                icon="delete"
                :label="`删除 ${row.name}`"
                :disabled="action.busy.value || toggles.busy.value"
              />
            </UiConfirmPopover>
          </div>
        </template>
      </UiTable>
      <UiPagination
        v-model:page="list.page.value"
        v-model:page-size="list.pageSize.value"
        :total="list.total.value"
        :disabled="list.loading.value"
      />
      <ApiKeyDialog
        v-if="formOpen"
        :api-key="editing"
        :models="list.models.value"
        @close="formOpen = false"
        @saved="saved"
      />
    </UiDisabledRegion>
  </div>
</template>
<style scoped>
.key-actions-heading {
  width: 100%;
  text-align: center;
}

.key-actions {
  justify-content: center;
  gap: 0;
}

.key-usage {
  display: grid;
  gap: var(--space-1);
  font-variant-numeric: tabular-nums;
}

.key-usage-ratio {
  color: var(--muted);
  font-weight: 400;
  overflow-wrap: anywhere;
}

.key-details {
  display: grid;
  gap: var(--space-1);
  font-variant-numeric: tabular-nums;
}

.key-limits {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-1) var(--space-3);
}

.key-limit {
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-1);
  white-space: nowrap;
}

.key-dates {
  overflow-wrap: anywhere;
  font-size: var(--text-sm);
}

.key-name {
  max-width: 100%;
  font-weight: var(--weight-emphasis);
  text-align: left;
}

.key-request-count {
  color: var(--muted);
  font-size: var(--text-sm);
  white-space: nowrap;
}

.key-reveal-content {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.key-full {
  flex: 1;
  min-width: 0;
  color: var(--text);
  font-weight: 400;
  line-height: 1.7;
  white-space: nowrap;
  overflow-x: auto;
  user-select: text;
}

.key-cell {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-wrap: nowrap;
}

.key-text {
  flex: 0 0 auto;
  margin-right: var(--space-2);
  white-space: nowrap;
  overflow-wrap: normal;
}

.key-permissions {
  display: grid;
  justify-items: start;
  gap: var(--space-1);
}

.key-models {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  max-width: 100%;
}

.key-model {
  max-width: 100%;
  white-space: normal;
  overflow-wrap: anywhere;
}
</style>
