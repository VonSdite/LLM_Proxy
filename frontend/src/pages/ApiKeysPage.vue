<script setup lang="ts">
import { ref } from 'vue'
import { apiKeysApi, type ApiKey } from '@/api/api-keys'
import { usePagedList } from '@/features/shared/usePagedList'
import { useAction } from '@/features/shared/useAction'
import { copyText, dateTime, number, tokenK } from '@/features/shared/format'
import ApiKeyDialog from '@/features/api-keys/ApiKeyDialog.vue'
import {
  UiAlert,
  UiButton,
  UiConfirmPopover,
  UiIconButton,
  UiInput,
  UiPagination,
  UiSwitch,
  UiTable,
  notify,
  type TableColumn,
} from '@/components/ui'
const list = usePagedList(apiKeysApi.list, { key: 'created_at', direction: 'desc' })
const action = useAction(list.refresh)
const clipboard = useAction()
const editing = ref<ApiKey>()
const formOpen = ref(false)
const revealed = ref(new Set<number>())
const columns: TableColumn[] = [
  { key: 'name', label: '名称', sortable: true, width: 150 },
  { key: 'api_key', label: 'API Key', width: 240 },
  { key: 'enabled', label: '启用', width: 80 },
  { key: 'allowed_models_count', label: '模型权限', sortable: true, width: 110 },
  { key: 'token_limit_k', label: '配额 / 剩余 (k)', sortable: true, width: 160, align: 'right' },
  { key: 'prompt_tokens', label: '输入 (k)', sortable: true, width: 110, align: 'right' },
  { key: 'completion_tokens', label: '输出 (k)', sortable: true, width: 110, align: 'right' },
  { key: 'total_tokens', label: '总用量 (k)', sortable: true, width: 120, align: 'right' },
  { key: 'total_request_count', label: '请求', sortable: true, width: 95, align: 'right' },
  { key: 'created_at', label: '创建时间', sortable: true, width: 170 },
  { key: 'last_used_at', label: '最近使用', sortable: true, width: 170 },
  { key: 'actions', label: '操作', width: 90 },
]
function edit(key?: ApiKey) {
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
</script>
<template>
  <div class="page">
    <header class="page-heading">
      <h1>API Key</h1>
      <UiButton variant="primary" icon="plus" @click="edit()">创建 API Key</UiButton>
    </header>
    <div class="toolbar">
      <div class="list-search">
        <UiInput
          v-model="list.keyword.value"
          placeholder="搜索名称或 Key"
          aria-label="搜索 API Key"
          autocomplete="off"
          data-1p-ignore
          data-lpignore="true"
        />
      </div>
      <UiIconButton
        icon="refresh"
        label="刷新 API Key"
        :loading="list.loading.value"
        @click="list.refresh"
      />
    </div>
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
          :model-value="Boolean(row.enabled)"
          :label="`启用 ${row.name}`"
          :disabled="action.busy.value"
          @update:model-value="action.run((signal) => apiKeysApi.toggle(row.id, signal))"
        />
      </template>
      <template #cell-allowed_models_count="{ row }">
        <UiButton variant="ghost" size="sm" @click="edit(row)">
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
      <template #cell-prompt_tokens="{ row }">{{ tokenK(row.prompt_tokens) }}</template>
      <template #cell-completion_tokens="{ row }">{{ tokenK(row.completion_tokens) }}</template>
      <template #cell-total_tokens="{ row }">{{ tokenK(row.total_tokens) }}</template>
      <template #cell-total_request_count="{ row }">{{ number(row.total_request_count) }}</template>
      <template #cell-created_at="{ row }">
        <small class="muted">{{ dateTime(row.created_at) }}</small>
      </template>
      <template #cell-last_used_at="{ row }">
        <small class="muted">{{ dateTime(row.last_used_at) }}</small>
      </template>
      <template #cell-actions="{ row }">
        <div class="actions">
          <UiIconButton icon="edit" :label="`编辑 ${row.name}`" @click="edit(row)" />
          <UiConfirmPopover
            :message="`删除 ${row.name}？使用此 Key 的请求将无法通过认证。`"
            @confirm="action.run((signal) => apiKeysApi.remove(row.id, signal), '已删除 API Key')"
          >
            <UiIconButton icon="delete" :label="`删除 ${row.name}`" :disabled="action.busy.value" />
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
}

.key-text {
  min-width: 0;
  margin-right: auto;
  overflow-wrap: anywhere;
}
</style>
