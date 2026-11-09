<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useOAuthFiles } from '@/features/oauth/useOAuthFiles'
import { fileCategory, fileLabels, type FileFilter } from '@/features/oauth/quota'
import OAuthCatalog from '@/features/oauth/OAuthCatalog.vue'
import OAuthFileCard from '@/features/oauth/OAuthFileCard.vue'
import OAuthLoginDialog from '@/features/oauth/OAuthLoginDialog.vue'
import {
  UiAlert,
  UiButton,
  UiCheckbox,
  UiConfirmPopover,
  UiDialog,
  UiEmptyState,
  UiHelpPopover,
  UiIconButton,
  UiListToolbar,
  UiPageHeader,
  UiPagination,
  UiSearchInput,
  UiSegmented,
  UiSelectionBar,
  UiTooltip,
} from '@/components/ui'
const workspace = useOAuthFiles()
const keyword = ref('')
const filter = ref<FileFilter>('available')
const page = ref(1)
const pageSize = ref(50)
const selected = ref(new Set<string>())
const loginOpen = ref(false)
const fileInput = ref<HTMLInputElement>()
const catalogOpen = ref(false)
const counts = computed(() => {
  const counts: Record<string, number> = { all: workspace.files.value.length }
  for (const file of workspace.files.value) {
    const category = fileCategory(file, workspace.now.value)
    counts[category] = (counts[category] || 0) + 1
  }
  return counts
})
const filters = computed(() =>
  Object.entries(fileLabels)
    .filter(([value]) => value !== 'other_unavailable')
    .map(([value, label]) => ({
      value,
      label: `${label} ${counts.value[value] || 0}`,
    })),
)
const filtered = computed(() =>
  workspace.files.value.filter(
    (file) =>
      (filter.value === 'all' || fileCategory(file, workspace.now.value) === filter.value) &&
      `${file.name} ${file.email} ${file.plan_type}`
        .toLowerCase()
        .includes(keyword.value.toLowerCase()),
  ),
)
const visible = computed(() =>
  filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value),
)
const selectedFiles = computed(() => filtered.value.filter((file) => selected.value.has(file.name)))
const selectedNames = computed(() => selectedFiles.value.map((file) => file.name))
const enableNames = computed(() =>
  selectedFiles.value
    .filter((file) => file.enabled === false || file.disabled)
    .map((file) => file.name),
)
const disableNames = computed(() =>
  selectedFiles.value
    .filter((file) => file.enabled !== false && !file.disabled)
    .map((file) => file.name),
)
const allSelected = computed(
  () => filtered.value.length > 0 && selectedNames.value.length === filtered.value.length,
)
watch([keyword, filter, pageSize], () => {
  page.value = 1
})
watch(filtered, (value) => {
  page.value = Math.min(page.value, Math.max(1, Math.ceil(value.length / pageSize.value)))
})
watch(workspace.files, (value) => {
  const existing = new Set(value.map((file) => file.name))
  selected.value = new Set([...selected.value].filter((name) => existing.has(name)))
})
function select(name: string, value: boolean) {
  if (value) selected.value.add(name)
  else selected.value.delete(name)
}
function selectAll(value: boolean) {
  filtered.value.forEach((file) => select(file.name, value))
}
async function importFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = [...(input.files || [])]
  input.value = ''
  if (files.length) await workspace.importFiles(files)
}
function completed(name: string) {
  loginOpen.value = false
  void workspace.loggedIn(name)
}
function exportFiltered() {
  const names = filtered.value.map((file) => file.name)
  if (names.length && !workspace.blocked.value) void workspace.exportFiles(names)
}
</script>
<template>
  <div class="page">
    <UiPageHeader title="Codex OAuth">
      <template #help>
        <UiHelpPopover label="认证文件与额度说明">
          <p>状态结合认证文件、最近调用结果与额度快照判断，不是实时向上游探测的结果。</p>
          <ul>
            <li>可用：可参与请求调度，请求时仍可能遇到认证或额度错误。</li>
            <li>认证失败：凭据缺失、刷新失败或上游拒绝认证，需要刷新成功或重新登录。</li>
            <li>额度用完：上游限额或已保存的额度窗口耗尽，恢复后可重新参与调度。</li>
            <li>已禁用：人工停止调度，重新启用不代表认证与额度问题已解决。</li>
          </ul>
          <p>
            页面每分钟同步本地状态，只对已到期窗口查询额度。手动刷新依次查询额度和重置卡，加载列表本身不会查询重置卡。
          </p>
          <p>批量操作只处理当前筛选范围内所选账号。</p>
          <p>
            导入支持单个或多个认证 JSON、以及本系统导出的
            ZIP；同名文件会覆盖，并重置该文件的本地运行状态。
          </p>
          <p>
            “导出”包含当前状态和搜索条件下的所有分页，“导出所选”仅包含勾选的文件。文件打包为
            ZIP，包含登录凭据。删除将文件移入服务端的删除归档目录，并停止参与调度。
          </p>
          <p>
            启用、禁用和删除只处理当前筛选范围内所选账号。同步列表读取本地状态；刷新额度会请求上游并读取重置卡。
          </p>
        </UiHelpPopover>
      </template>
    </UiPageHeader>
    <UiListToolbar wide-filters>
      <template #actions>
        <UiButton
          variant="primary"
          icon="plus"
          :disabled="workspace.blocked.value"
          @click="loginOpen = true"
        >
          添加账号
        </UiButton>
        <UiCheckbox
          class="oauth-select-all"
          :model-value="allSelected ? true : selectedNames.length ? 'indeterminate' : false"
          :disabled="!filtered.length || workspace.blocked.value"
          label="选择当前筛选的账号"
          @update:model-value="selectAll"
        >
          全选
        </UiCheckbox>
        <UiButton @click="catalogOpen = true">模型目录</UiButton>
        <UiSegmented v-model="filter" :items="filters" label="认证文件状态" />
        <UiButton icon="import" :disabled="workspace.blocked.value" @click="fileInput?.click()">
          导入
        </UiButton>
        <UiTooltip :text="`导出当前状态和搜索条件下的 ${filtered.length} 个认证文件，包含所有分页`">
          <UiButton
            icon="export"
            :loading="workspace.busy.value === 'export'"
            :disabled="!filtered.length || workspace.blocked.value"
            @click="exportFiltered"
          >
            导出
          </UiButton>
        </UiTooltip>
      </template>
      <template #search>
        <UiSearchInput
          v-model="keyword"
          placeholder="搜索文件、邮箱或套餐"
          aria-label="搜索认证文件"
        />
      </template>
      <UiIconButton
        icon="refresh"
        label="同步认证文件列表"
        :loading="workspace.busy.value === 'loading'"
        :disabled="workspace.blocked.value"
        @click="workspace.reload"
      />
    </UiListToolbar>
    <UiSelectionBar
      v-if="selectedNames.length"
      :count="selectedNames.length"
      :disabled="workspace.blocked.value"
      @clear="selectAll(false)"
    >
      <UiButton :disabled="workspace.blocked.value" @click="workspace.refresh(selectedNames)">
        刷新额度
      </UiButton>
      <UiButton
        v-if="enableNames.length"
        :disabled="workspace.blocked.value"
        @click="workspace.mutate(enableNames, 'enable')"
      >
        启用
      </UiButton>
      <UiButton
        v-if="disableNames.length"
        :disabled="workspace.blocked.value"
        @click="workspace.mutate(disableNames, 'disable')"
      >
        禁用
      </UiButton>
      <UiButton :disabled="workspace.blocked.value" @click="workspace.exportFiles(selectedNames)">
        导出所选
      </UiButton>
      <UiConfirmPopover
        :message="`删除当前筛选中所选的 ${selectedNames.length} 个认证文件？`"
        @confirm="workspace.mutate(selectedNames, 'delete')"
      >
        <UiButton variant="danger-outline" :disabled="workspace.blocked.value">删除</UiButton>
      </UiConfirmPopover>
    </UiSelectionBar>
    <input
      ref="fileInput"
      type="file"
      accept=".json,.zip"
      multiple
      class="sr-only"
      aria-label="导入认证文件"
      @change="importFiles"
    />
    <UiAlert v-if="workspace.error.value">{{ workspace.error.value }}</UiAlert>
    <small v-if="['refresh', 'import'].includes(workspace.busy.value)" class="muted" role="status">
      读取额度 {{ workspace.progress.value.done }} / {{ workspace.progress.value.total }}
    </small>
    <div class="oauth-files">
      <OAuthFileCard
        v-for="file in visible"
        :key="file.name"
        :file="file"
        :selected="selected.has(file.name)"
        :disabled="workspace.blocked.value"
        :loading="workspace.loadingFile.value === file.name"
        :error="workspace.fileErrors.value[file.name]"
        :now="workspace.now.value"
        :enabled="workspace.toggles.value(file.name, file.enabled !== false)"
        :toggling="workspace.toggles.pending(file.name)"
        :toggle-disabled="workspace.toggleDisabled.value"
        @select="select(file.name, $event)"
        @enabled="workspace.setEnabled(file.name, $event)"
        @remove="workspace.mutate([file.name], 'delete')"
        @refresh="workspace.refresh([file.name])"
        @consume="workspace.consume(file.name, $event)"
      />
    </div>
    <UiEmptyState
      v-if="!visible.length"
      :loading="workspace.busy.value === 'loading'"
      :title="
        workspace.files.value.length
          ? filter === 'available' && !keyword
            ? '暂无可用账号'
            : '没有匹配的认证文件'
          : '添加账号或导入认证文件'
      "
    >
      <UiButton
        v-if="workspace.files.value.length && filter !== 'all'"
        size="sm"
        @click="filter = 'all'"
      >
        查看全部
      </UiButton>
    </UiEmptyState>
    <UiPagination v-model:page="page" v-model:page-size="pageSize" :total="filtered.length" />
    <OAuthLoginDialog
      v-if="loginOpen"
      @close="loginOpen = false"
      @working="workspace.externalBusy.value = $event"
      @completed="completed"
    />
    <UiDialog v-model:open="catalogOpen" title="Codex 模型目录" size="xl">
      <div class="oauth-catalogs">
        <OAuthCatalog />
        <OAuthCatalog image />
      </div>
    </UiDialog>
    <UiDialog
      v-if="workspace.importResult.value"
      open
      title="认证文件导入结果"
      @update:open="workspace.importResult.value = undefined"
    >
      <div class="stack">
        <p>
          成功 {{ workspace.importResult.value.imported }} · 失败
          {{ workspace.importResult.value.failed }}
        </p>
        <UiAlert v-for="item in workspace.importResult.value.failed_files" :key="item.name">
          <strong>{{ item.name }}</strong>
          <p>{{ item.reason }}</p>
        </UiAlert>
      </div>
    </UiDialog>
  </div>
</template>
<style scoped>
.oauth-select-all {
  flex-shrink: 0;
  white-space: nowrap;
}

.oauth-files {
  display: grid;
  align-items: start;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 480px), 1fr));
  gap: var(--space-4);
}

.oauth-catalogs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

@media (width <= 800px) {
  .oauth-catalogs {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
