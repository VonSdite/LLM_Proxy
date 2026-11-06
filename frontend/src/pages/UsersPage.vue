<script setup lang="ts">
import { computed, ref } from 'vue'
import { usersApi, type User } from '@/api/users'
import { useSession } from '@/stores/session'
import { usePagedList } from '@/features/shared/usePagedList'
import { useAction } from '@/features/shared/useAction'
import { dateTime, number, tokenK } from '@/features/shared/format'
import JsonImportDialog from '@/features/shared/JsonImportDialog.vue'
import UserDialog from '@/features/users/UserDialog.vue'
import BatchPermissionsDialog from '@/features/users/BatchPermissionsDialog.vue'
import {
  UiAlert,
  UiButton,
  UiConfirmPopover,
  UiHelpPopover,
  UiIconButton,
  UiInput,
  UiPagination,
  UiSwitch,
  UiTable,
  notify,
  type TableColumn,
} from '@/components/ui'
const session = useSession()
const whitelist = computed(() => session.context?.features.whitelist || false)
const list = usePagedList(usersApi.list, { key: 'total_tokens', direction: 'desc' })
const action = useAction(list.refresh)
const editing = ref<User>()
const formOpen = ref(false)
const importing = ref(false)
const permissionIds = ref<number[]>()
const selectedIds = computed(() => list.selected.value.map(Number))
const columns: TableColumn[] = [
  { key: 'username', label: '用户', sortable: true, width: 160 },
  { key: 'ip_address', label: 'IP 地址', sortable: true, width: 175 },
  { key: 'whitelist_access_enabled', label: '白名单', width: 95 },
  { key: 'allowed_models_count', label: '模型权限', sortable: true, width: 110 },
  { key: 'prompt_tokens', label: '输入 (k)', sortable: true, width: 110, align: 'right' },
  { key: 'completion_tokens', label: '输出 (k)', sortable: true, width: 110, align: 'right' },
  { key: 'total_tokens', label: '总用量 (k)', sortable: true, width: 120, align: 'right' },
  { key: 'total_request_count', label: '请求', sortable: true, width: 95, align: 'right' },
  { key: 'created_at', label: '创建时间', sortable: true, width: 170 },
  { key: 'actions', label: '操作', width: 90 },
]
function edit(user?: User) {
  editing.value = user
  formOpen.value = true
}
async function saved() {
  notify('已保存')
  await list.refresh()
}
async function toggleWhitelist(value: boolean) {
  await action.run(
    async (signal) => {
      await usersApi.whitelist(value, signal)
      if (signal.aborted) return
      await session.refresh()
    },
    value ? '白名单已启用' : '白名单已关闭',
  )
}
async function removeSelected() {
  const ids = [...selectedIds.value]
  if (
    await action.run(
      (signal) => usersApi.batch(ids, 'delete', undefined, signal),
      `已删除 ${ids.length} 个用户`,
    )
  )
    list.selected.value = []
}
async function importUsers(value: unknown, signal: AbortSignal) {
  const result = await usersApi.import(value, signal)
  return `已导入 ${result.count} 个用户：新增 ${result.created_count}，更新 ${result.updated_count}`
}
function imported(message: string) {
  notify(message)
  void list.refresh()
}
</script>
<template>
  <div class="page">
    <header class="page-heading">
      <h1>用户</h1>
      <div class="actions">
        <UiSwitch
          :model-value="whitelist"
          :disabled="action.busy.value"
          @update:model-value="toggleWhitelist"
        >
          白名单
        </UiSwitch>
        <UiHelpPopover label="白名单说明">
          启用后，仅允许白名单中的 IP 调用模型，并按用户模型权限限制访问。
        </UiHelpPopover>
      </div>
      <UiButton variant="primary" icon="plus" @click="edit()">添加用户</UiButton>
    </header>
    <div class="toolbar">
      <div class="list-search">
        <UiInput v-model="list.keyword.value" placeholder="搜索用户名或 IP" aria-label="搜索用户" />
      </div>
      <div class="actions">
        <template v-if="selectedIds.length">
          <span class="muted">已选 {{ selectedIds.length }}</span>
          <UiButton
            :disabled="!whitelist || action.busy.value"
            @click="permissionIds = [...selectedIds]"
          >
            模型权限
          </UiButton>
          <UiConfirmPopover
            :message="`删除所选 ${selectedIds.length} 个用户？`"
            @confirm="removeSelected"
          >
            <UiButton variant="danger" :disabled="action.busy.value">删除</UiButton>
          </UiConfirmPopover>
        </template>
        <UiButton icon="upload" @click="importing = true">导入</UiButton>
        <UiButton
          icon="download"
          :disabled="action.busy.value"
          @click="action.run((signal) => usersApi.export(selectedIds, signal))"
        >
          {{ selectedIds.length ? '导出所选' : '导出' }}
        </UiButton>
        <UiIconButton
          icon="refresh"
          label="刷新用户"
          :loading="list.loading.value"
          @click="list.refresh"
        />
      </div>
    </div>
    <UiAlert v-if="list.error.value || action.error.value">
      {{ action.error.value || list.error.value }}
    </UiAlert>
    <UiTable
      v-model:selected="list.selected.value"
      v-model:sort="list.sort.value"
      :rows="list.rows.value"
      :columns="columns"
      :row-key="(row) => String(row.id)"
      selectable
      manual-sorting
      :loading="list.loading.value"
      storage-key="users"
      empty="还没有用户"
    >
      <template #cell-ip_address="{ row }">
        <span class="mono">{{ row.ip_address }}</span>
      </template>
      <template #cell-whitelist_access_enabled="{ row }">
        <UiSwitch
          :model-value="Boolean(row.whitelist_access_enabled)"
          :disabled="!whitelist || action.busy.value"
          :label="`${row.username} 白名单访问`"
          @update:model-value="action.run((signal) => usersApi.toggle(row.id, signal))"
        />
      </template>
      <template #cell-allowed_models_count="{ row }">
        <UiButton variant="ghost" size="sm" :disabled="!whitelist" @click="edit(row)">
          {{ row.model_permissions === '*' ? '全部模型' : `${row.allowed_models_count} 个模型` }}
        </UiButton>
      </template>
      <template #cell-prompt_tokens="{ row }">{{ tokenK(row.prompt_tokens) }}</template>
      <template #cell-completion_tokens="{ row }">{{ tokenK(row.completion_tokens) }}</template>
      <template #cell-total_tokens="{ row }">{{ tokenK(row.total_tokens) }}</template>
      <template #cell-total_request_count="{ row }">{{ number(row.total_request_count) }}</template>
      <template #cell-created_at="{ row }">
        <small class="muted">{{ dateTime(row.created_at) }}</small>
      </template>
      <template #cell-actions="{ row }">
        <div class="actions">
          <UiIconButton icon="edit" :label="`编辑 ${row.username}`" @click="edit(row)" />
          <UiConfirmPopover
            :message="`删除用户 ${row.username}？`"
            @confirm="action.run((signal) => usersApi.remove(row.id, signal), '已删除用户')"
          >
            <UiIconButton
              icon="delete"
              :label="`删除 ${row.username}`"
              :disabled="action.busy.value"
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
    <UserDialog
      v-if="formOpen"
      :user="editing"
      :models="list.models.value"
      :whitelist="whitelist"
      @close="formOpen = false"
      @saved="saved"
    />
    <BatchPermissionsDialog
      v-if="permissionIds"
      :ids="permissionIds"
      :models="list.models.value"
      @close="permissionIds = undefined"
      @saved="saved"
    />
    <JsonImportDialog
      v-if="importing"
      v-model:open="importing"
      title="导入用户"
      :submit="importUsers"
      @imported="imported"
    />
  </div>
</template>
