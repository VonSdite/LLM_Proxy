<script setup lang="ts">
import { computed, ref } from 'vue'
import { apiKeysApi, type ApiKey } from '@/api/api-keys'
import { usePagedList } from '@/features/shared/usePagedList'
import { useAction } from '@/features/shared/useAction'
import { useToggle } from '@/features/shared/useToggle'
import { copyText, dateTime, number, tokenK } from '@/features/shared/format'
import ApiKeyDialog from '@/features/api-keys/ApiKeyDialog.vue'
import {
  UiAlert,
  UiButton,
  UiConfirmPopover,
  UiIconButton,
  UiListToolbar,
  UiPageHeader,
  UiPagination,
  UiSearchInput,
  UiSwitch,
  UiTable,
  notify,
  type TableColumn,
} from '@/components/ui'
const list = usePagedList(apiKeysApi.list, { key: 'created_at', direction: 'desc' })
const action = useAction(list.refresh)
const toggles = useToggle()
const clipboard = useAction()
const editing = ref<ApiKey>()
const formOpen = ref(false)
const revealed = ref(new Set<number>())
const keyColumnWidth = computed(() =>
  Math.max(440, ...list.rows.value.map((row) => (row.api_key?.length || 0) * 8 + 120)),
)
const columns = computed<TableColumn[]>(() => [
  { key: 'name', label: '名称', sortable: true, width: 150 },
  {
    key: 'api_key',
    label: 'API Key',
    width: keyColumnWidth.value,
    minWidth: keyColumnWidth.value,
    help: '供客户端访问本代理服务的凭据，使用 Authorization: Bearer 密钥 或 X-API-Key 请求头。不是 Provider 的上游密钥。',
  },
  { fixed: true, key: 'enabled', label: '启用', width: 80 },
  { key: 'allowed_models_count', label: '模型权限', sortable: true, width: 110 },
  {
    key: 'token_limit_k',
    label: '配额 / 剩余 (k)',
    sortable: true,
    width: 184,
    align: 'right',
    help: '左侧是累计总配额，右侧是扣除已用 Token 后的剩余量。1k = 1000 Tokens，不按月重置；不限额表示未设置总量上限。',
  },
  { key: 'total_tokens', label: '总用量 (k)', sortable: true, width: 120, align: 'right' },
  { key: 'total_request_count', label: '请求', sortable: true, width: 95, align: 'right' },
  { key: 'created_at', label: '创建时间', sortable: true, width: 170 },
  { key: 'last_used_at', label: '最近使用', sortable: true, width: 170 },
  { fixed: true, key: 'actions', label: '操作', width: 120, minWidth: 120 },
])
function edit(key?: ApiKey) {
  if (toggles.busy.value || action.busy.value) return
  editing.value = key
  formOpen.value = true
}
function reveal(id: number) {
  if (revealed.value.has(id)) revealed.value.delete(id)
  else revealed.value.add(id)
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
    <UiPageHeader title="API Key" />
    <UiListToolbar>
      <template #actions>
        <UiButton
          variant="primary"
          icon="plus"
          :disabled="action.busy.value || toggles.busy.value"
          @click="edit()"
        >
          创建 API Key
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
    <UiAlert v-if="list.error.value || action.error.value || clipboard.error.value">
      {{ action.error.value || clipboard.error.value || list.error.value }}
    </UiAlert>
    <UiTable
      v-model:sort="list.sort.value"
      :rows="list.rows.value"
      :columns="columns"
      :row-key="(row) => String(row.id)"
      manual-sorting
      :loading="list.loading.value"
      storage-key="api-keys"
      empty="还没有 API Key"
    >
      <template #cell-api_key="{ row }">
        <div class="key-cell">
          <code class="key-text">
            {{ revealed.has(row.id) ? row.api_key : `${row.key_prefix}••••${row.key_suffix}` }}
          </code>
          <UiIconButton
            :icon="revealed.has(row.id) ? 'eyeOff' : 'eye'"
            :label="revealed.has(row.id) ? '隐藏 Key' : '显示 Key'"
            @click="reveal(row.id)"
          />
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
        <UiButton
          variant="ghost"
          size="sm"
          :disabled="action.busy.value || toggles.busy.value"
          @click="edit(row)"
        >
          {{ row.model_permissions === '*' ? '全部模型' : `${row.allowed_models_count} 个模型` }}
        </UiButton>
      </template>
      <template #cell-token_limit_k="{ row }">
        <span v-if="row.token_limit_k === null" class="muted">不限额</span>
        <template v-else>
          {{ number(row.token_limit_k) }}
          <span class="muted">/ {{ tokenK(row.token_limit_remaining) }}</span>
        </template>
      </template>
      <template #cell-total_tokens="{ row }">{{ tokenK(row.total_tokens) }}</template>
      <template #cell-total_request_count="{ row }">{{ number(row.total_request_count) }}</template>
      <template #cell-created_at="{ row }">
        <small class="muted">{{ dateTime(row.created_at) }}</small>
      </template>
      <template #cell-last_used_at="{ row }">
        <small class="muted">{{ dateTime(row.last_used_at) }}</small>
      </template>
      <template #cell-actions="{ row }">
        <div class="table-actions">
          <UiIconButton
            icon="edit"
            :label="`编辑 ${row.name}`"
            :disabled="action.busy.value || toggles.busy.value"
            @click="edit(row)"
          />
          <UiConfirmPopover
            :message="`删除 ${row.name}？使用此 Key 的请求将无法通过认证。`"
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
  </div>
</template>
<style scoped>
.key-cell {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-wrap: nowrap;
}

.key-text {
  flex: 0 0 auto;
  margin-right: auto;
  white-space: nowrap;
  overflow-wrap: normal;
}
</style>
