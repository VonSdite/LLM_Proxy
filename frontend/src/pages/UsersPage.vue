<script setup lang="ts">
import { computed, ref } from 'vue'
import { usersApi, type User } from '@/api/users'
import { useSession } from '@/stores/session'
import { usePagedList } from '@/features/shared/usePagedList'
import { useAction } from '@/features/shared/useAction'
import { useToggle } from '@/features/shared/useToggle'
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
  UiListToolbar,
  UiPageHeader,
  UiPagination,
  UiSearchInput,
  UiSelectionBar,
  UiSwitch,
  UiTable,
  UiTooltip,
  notify,
  type TableColumn,
} from '@/components/ui'
const session = useSession()
const whitelist = computed(() => session.context?.features.whitelist || false)
const list = usePagedList(usersApi.list, { key: 'total_tokens', direction: 'desc' })
const action = useAction(list.refresh)
const exporting = useAction()
const toggles = useToggle()
const editing = ref<User>()
const formOpen = ref(false)
const importing = ref(false)
const permissionIds = ref<number[]>()
const selectedIds = computed(() => list.selected.value.map(Number))
const columns: TableColumn[] = [
  { key: 'username', label: '用户', sortable: true, width: 160 },
  { key: 'ip_address', label: 'IP 地址', sortable: true, width: 175 },
  { fixed: true, key: 'whitelist_access_enabled', label: '白名单', width: 95 },
  { key: 'allowed_models_count', label: '模型权限', sortable: true, width: 110 },
  { key: 'prompt_tokens', label: '输入 (k)', sortable: true, width: 110, align: 'right' },
  { key: 'completion_tokens', label: '输出 (k)', sortable: true, width: 110, align: 'right' },
  {
    key: 'total_tokens',
    label: '总用量 (k)',
    sortable: true,
    width: 148,
    align: 'right',
    help: '该用户累计记录的输入与输出 Token，1k = 1000 Tokens。输入、输出列使用相同单位；查看特定日期范围请到统计与日志。',
  },
  { key: 'total_request_count', label: '请求', sortable: true, width: 95, align: 'right' },
  { key: 'created_at', label: '创建时间', sortable: true, width: 170 },
  { fixed: true, key: 'actions', label: '操作', width: 120, minWidth: 120 },
]
function edit(user?: User) {
  if (toggles.busy.value || action.busy.value) return
  editing.value = user
  formOpen.value = true
}
async function saved() {
  notify('已保存')
  await list.refresh()
}
function toggleWhitelist(value: boolean) {
  return toggles.run(
    'whitelist',
    value,
    (signal) => usersApi.whitelist(value, signal),
    (result) => {
      if (session.context) session.context.features.whitelist = result.enabled
    },
  )
}
function setEnabled(user: User, value: boolean) {
  return toggles.run(
    String(user.id),
    value,
    (signal) => usersApi.update(user.id, { whitelist_access_enabled: value }, signal),
    () =>
      list.patch((data) => ({
        ...data,
        items: data.items.map((row) =>
          row.id === user.id ? { ...row, whitelist_access_enabled: value } : row,
        ),
      })),
  )
}
async function removeSelected() {
  if (toggles.busy.value) return
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
function exportFiltered() {
  if (
    !list.total.value ||
    list.filterPending.value ||
    list.error.value ||
    action.busy.value ||
    toggles.busy.value
  )
    return
  const keyword = list.keyword.value.trim()
  void exporting.run((signal) => usersApi.exportFiltered(keyword, signal))
}
function exportSelected() {
  if (
    !selectedIds.value.length ||
    list.filterPending.value ||
    action.busy.value ||
    toggles.busy.value
  )
    return
  const ids = [...selectedIds.value]
  void exporting.run((signal) => usersApi.export(ids, signal))
}
</script>
<template>
  <div class="page">
    <UiPageHeader title="用户" />
    <UiListToolbar>
      <template #actions>
        <UiButton
          variant="primary"
          icon="plus"
          :disabled="action.busy.value || toggles.busy.value"
          @click="edit()"
        >
          添加用户
        </UiButton>
        <UiButton
          icon="import"
          :disabled="action.busy.value || toggles.busy.value"
          @click="importing = true"
        >
          导入
        </UiButton>
        <UiTooltip :text="`导出当前搜索条件下的 ${list.total.value} 个用户，包含所有分页`">
          <UiButton
            icon="export"
            :loading="exporting.busy.value"
            :disabled="
              !list.total.value ||
              list.filterPending.value ||
              !!list.error.value ||
              action.busy.value ||
              toggles.busy.value
            "
            @click="exportFiltered"
          >
            导出
          </UiButton>
        </UiTooltip>
      </template>
      <template #search>
        <UiSearchInput
          v-model="list.keyword.value"
          placeholder="搜索用户名或 IP"
          aria-label="搜索用户"
        />
      </template>
      <template #filters>
        <UiSwitch
          :model-value="toggles.value('whitelist', whitelist)"
          :loading="toggles.pending('whitelist')"
          :disabled="action.busy.value"
          @update:model-value="toggleWhitelist"
        >
          白名单
        </UiSwitch>
        <UiHelpPopover label="白名单说明">
          <p>
            启用后，调用模型的 IP
            必须已登记且开启“允许白名单访问”，并受用户的模型权限限制；模型目录也按权限过滤。
          </p>
          <p>
            新建用户默认允许白名单访问并授权全部模型，可在列表中禁用或编辑权限。关闭总开关后不再按用户
            IP 与模型权限拦截；API Key 校验仍由其独立开关控制。
          </p>
        </UiHelpPopover>
      </template>
      <UiIconButton
        icon="refresh"
        label="刷新用户"
        :loading="list.loading.value"
        @click="list.refresh"
      />
    </UiListToolbar>
    <UiSelectionBar
      v-if="selectedIds.length"
      :count="selectedIds.length"
      :disabled="action.busy.value || toggles.busy.value"
      @clear="list.selected.value = []"
    >
      <UiButton
        :disabled="!whitelist || action.busy.value || toggles.busy.value"
        @click="permissionIds = [...selectedIds]"
      >
        模型权限
      </UiButton>
      <UiButton
        :disabled="
          action.busy.value ||
          toggles.busy.value ||
          exporting.busy.value ||
          list.filterPending.value
        "
        @click="exportSelected"
      >
        导出所选
      </UiButton>
      <UiConfirmPopover
        :message="`删除所选 ${selectedIds.length} 个用户？`"
        @confirm="removeSelected"
      >
        <UiButton variant="danger-outline" :disabled="action.busy.value || toggles.busy.value">
          删除
        </UiButton>
      </UiConfirmPopover>
    </UiSelectionBar>
    <UiAlert v-if="list.error.value || action.error.value || exporting.error.value">
      {{ exporting.error.value || action.error.value || list.error.value }}
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
          :model-value="toggles.value(String(row.id), Boolean(row.whitelist_access_enabled))"
          :loading="toggles.pending(String(row.id))"
          :disabled="!whitelist || action.busy.value || toggles.pending('whitelist')"
          :label="`${row.username} 白名单访问`"
          @update:model-value="setEnabled(row, $event)"
        />
      </template>
      <template #cell-allowed_models_count="{ row }">
        <UiButton
          variant="ghost"
          size="sm"
          :disabled="!whitelist || action.busy.value || toggles.busy.value"
          @click="edit(row)"
        >
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
        <div class="table-actions">
          <UiIconButton
            icon="edit"
            :label="`编辑 ${row.username}`"
            :disabled="action.busy.value || toggles.busy.value"
            @click="edit(row)"
          />
          <UiConfirmPopover
            :message="`删除用户 ${row.username}？`"
            @confirm="action.run((signal) => usersApi.remove(row.id, signal), '已删除用户')"
          >
            <UiIconButton
              icon="delete"
              :label="`删除 ${row.username}`"
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
      :help="[
        '选择本系统导出的 JSON 文件，或粘贴相同结构的内容。以 IP 地址识别用户：新 IP 创建记录，相同 IP 更新原记录的名称、白名单状态和模型权限。',
        '导入后立即生效，不会删除文件中未包含的用户，也不包含历史用量。',
      ]"
      :submit="importUsers"
      @imported="imported"
    />
  </div>
</template>
