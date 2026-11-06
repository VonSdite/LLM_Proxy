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
  UiInput,
  UiPagination,
  UiSegmented,
} from '@/components/ui'
const workspace = useOAuthFiles()
const keyword = ref('')
const filter = ref<FileFilter>('all')
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
  Object.entries(fileLabels).map(([value, label]) => ({
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
const selectedNames = computed(() =>
  filtered.value.filter((file) => selected.value.has(file.name)).map((file) => file.name),
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
</script>
<template>
  <div class="page">
    <header class="page-heading">
      <h1>Codex OAuth</h1>
      <UiButton icon="settings" @click="catalogOpen = true">模型目录</UiButton>
      <UiButton
        variant="primary"
        icon="plus"
        :disabled="workspace.blocked.value"
        @click="loginOpen = true"
      >
        添加账号
      </UiButton>
    </header>
    <div class="toolbar">
      <div class="actions">
        <h2>认证文件</h2>
        <UiHelpPopover label="认证文件与额度说明">
          <p>
            状态来自认证文件、最近请求结果与额度快照。每分钟同步本地状态，仅对额度窗口已到期的账号发起查询。
          </p>
          <p>手动刷新依次查询额度和重置卡。批量操作只处理当前筛选范围内所选账号。</p>
        </UiHelpPopover>
      </div>
      <div class="list-search">
        <UiInput v-model="keyword" placeholder="搜索文件、邮箱或套餐" aria-label="搜索认证文件" />
      </div>
    </div>
    <UiSegmented v-model="filter" :items="filters" label="认证文件状态" />
    <div class="toolbar">
      <UiCheckbox
        :model-value="allSelected ? true : selectedNames.length ? 'indeterminate' : false"
        :disabled="!filtered.length"
        @update:model-value="selectAll"
      >
        当前筛选 · 已选 {{ selectedNames.length }}
      </UiCheckbox>
      <div class="actions">
        <UiButton
          size="sm"
          :disabled="workspace.blocked.value || !selectedNames.length"
          @click="workspace.refresh(selectedNames)"
        >
          刷新额度
        </UiButton>
        <UiButton
          size="sm"
          :disabled="workspace.blocked.value || !selectedNames.length"
          @click="workspace.mutate(selectedNames, 'enable')"
        >
          启用
        </UiButton>
        <UiButton
          size="sm"
          :disabled="workspace.blocked.value || !selectedNames.length"
          @click="workspace.mutate(selectedNames, 'disable')"
        >
          禁用
        </UiButton>
        <UiConfirmPopover
          :message="`删除当前筛选中所选的 ${selectedNames.length} 个认证文件？`"
          @confirm="workspace.mutate(selectedNames, 'delete')"
        >
          <UiButton
            size="sm"
            variant="danger"
            :disabled="workspace.blocked.value || !selectedNames.length"
          >
            删除
          </UiButton>
        </UiConfirmPopover>
        <UiButton
          size="sm"
          icon="upload"
          :disabled="workspace.blocked.value"
          @click="fileInput?.click()"
        >
          导入
        </UiButton>
        <UiButton
          size="sm"
          icon="download"
          :disabled="workspace.blocked.value || !selectedNames.length"
          @click="workspace.exportFiles(selectedNames)"
        >
          导出
        </UiButton>
        <UiIconButton
          icon="refresh"
          label="同步认证文件列表"
          :loading="workspace.busy.value === 'loading'"
          :disabled="workspace.blocked.value"
          @click="workspace.reload"
        />
      </div>
      <input
        ref="fileInput"
        type="file"
        accept=".json,.zip"
        multiple
        class="sr-only"
        aria-label="导入认证文件"
        @change="importFiles"
      />
    </div>
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
        @select="select(file.name, $event)"
        @enabled="workspace.mutate([file.name], $event ? 'enable' : 'disable')"
        @remove="workspace.mutate([file.name], 'delete')"
        @refresh="workspace.refresh([file.name])"
        @consume="workspace.consume(file.name, $event)"
      />
    </div>
    <UiEmptyState
      v-if="!visible.length"
      :loading="workspace.busy.value === 'loading'"
      :title="workspace.files.value.length ? '没有匹配的认证文件' : '添加账号或导入认证文件'"
    />
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
.oauth-files {
  display: grid;
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
