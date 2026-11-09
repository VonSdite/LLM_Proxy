<script setup lang="ts">
import { ref } from 'vue'
import { moveItem, type RowMove } from '@/features/shared/order'
import {
  UiButton,
  UiIconButton,
  UiInput,
  UiPageHeader,
  UiListToolbar,
  UiSearchInput,
  UiSelectionBar,
  UiNumberInput,
  UiSecretInput,
  UiField,
  UiSelect,
  UiMultiSelect,
  UiCombobox,
  UiCheckbox,
  UiSwitch,
  UiTriState,
  UiTabs,
  UiSegmented,
  UiHelpPopover,
  UiPopover,
  UiTooltip,
  UiDialog,
  UiConfirmPopover,
  UiDropdownMenu,
  UiAlert,
  UiEmptyState,
  UiBadge,
  UiStatus,
  UiTable,
  UiSortableList,
  UiSelectableRow,
  UiPagination,
  UiCodeEditor,
  notify,
} from '@/components/ui'
const text = ref('provider/claude-sonnet-4-5')
const number = ref<number | null>(200000)
const checked = ref(true)
const tri = ref<boolean | null>(null)
const tab = ref('claude')
const open = ref(false)
const popoverOpen = ref(false)
function saveExample() {
  open.value = false
  notify('已保存')
}
const page = ref(1)
const selected = ref<string[]>([])
const selectedRow = ref('primary')
const ordered = ref([
  { id: 'primary', name: '主目标' },
  { id: 'fallback', name: '备用目标' },
])
function reorder(move: RowMove) {
  ordered.value = moveItem(ordered.value, move, (item) => item.id)
}
const filters = ref<string[]>([])
const code = ref('{\n  "model": "provider/claude-sonnet-4-5"\n}')
const options = [
  { value: 'claude', label: 'Claude', description: '200K 上下文 · 64K 输出' },
  { value: 'codex', label: 'Codex', description: '模型推荐参数' },
  { value: 'opencode', label: 'opencode', disabled: true },
]
const rows = [
  { id: '1', name: 'main/claude-sonnet-4-5', status: true },
  {
    id: '2',
    name: 'remote/very-long-provider-and-model-name-with-a-snapshot-20261106',
    status: false,
  },
]
</script>
<template>
  <div class="page">
    <UiPageHeader title="组件展示" />
    <UiListToolbar>
      <template #actions>
        <UiButton variant="primary" @click="notify('配置已保存')">显示通知</UiButton>
        <UiSegmented v-model="tab" :items="options" label="组件示例筛选" />
        <UiButton icon="import" @click="notify('示例导入')">导入</UiButton>
        <UiTooltip text="导出当前状态和搜索条件匹配的记录">
          <UiButton icon="export" @click="notify('示例导出')">导出</UiButton>
        </UiTooltip>
      </template>
      <template #search>
        <UiSearchInput v-model="text" aria-label="搜索组件示例" placeholder="搜索组件" />
      </template>
      <UiIconButton icon="refresh" label="刷新组件示例" @click="notify('已刷新')" />
    </UiListToolbar>
    <section class="specimen surface stack">
      <h2>操作</h2>
      <div class="actions">
        <UiButton variant="primary" icon="plus">新增</UiButton>
        <UiButton icon="refresh">刷新</UiButton>
        <UiButton variant="ghost">取消</UiButton>
        <UiButton variant="link">查看详情</UiButton>
        <UiButton variant="danger">删除</UiButton>
        <UiButton variant="danger-solid">确认删除</UiButton>
        <UiButton loading>保存中</UiButton>
        <UiButton disabled>禁用</UiButton>
        <UiTooltip text="编辑配置"><UiIconButton icon="edit" label="编辑" /></UiTooltip>
      </div>
    </section>
    <section class="specimen surface stack">
      <h2>表单</h2>
      <div class="form-grid">
        <UiField label="模型 ID" help="支持从目录选择或手动输入模型 ID。">
          <UiInput v-model="text" />
        </UiField>
        <UiField label="上下文窗口" help="手动输入在失焦且值变化时提交。">
          <UiNumberInput v-model="number" />
        </UiField>
        <UiField label="API Key"><UiSecretInput v-model="text" /></UiField>
        <UiField label="无效地址" error="请输入完整地址">
          <UiInput placeholder="https://" />
        </UiField>
        <UiField label="固定选项">
          <UiSelect v-model="tab" :options="options">
            <template #option="{ option }">
              <span>{{ option.label }}</span>
              <small v-if="option.description" class="muted">{{ option.description }}</small>
            </template>
          </UiSelect>
        </UiField>
        <UiField label="多选筛选"><UiMultiSelect v-model="filters" :options="options" /></UiField>
        <UiField label="搜索候选">
          <UiCombobox v-model="text" :options="options" allow-custom>
            <template #option="{ option }">
              <span>{{ option.label }}</span>
              <small v-if="option.description" class="muted">{{ option.description }}</small>
            </template>
          </UiCombobox>
        </UiField>
        <UiField label="三态设置"><UiTriState v-model="tri" /></UiField>
        <UiField label="禁用"><UiInput model-value="不可编辑" disabled /></UiField>
        <UiField inline label="行内开关" help="标签与开关在同一行，点击标签或开关切换。">
          <UiSwitch v-model="checked" label="行内开关" />
        </UiField>
      </div>
      <div class="actions">
        <UiCheckbox v-model="checked">选择项目</UiCheckbox>
        <UiSwitch v-model="checked" label="功能开关" />
        <UiHelpPopover>
          悬停或聚焦查看说明。
          <a href="#states">状态示例</a>
        </UiHelpPopover>
        <UiHelpPopover
          label="多项说明示例"
          :text="[
            '多条规则使用无序列表，每项说明一个重点。',
            '段落、路径与代码等复杂内容使用内容插槽。',
          ]"
        />
      </div>
    </section>
    <section id="states" class="specimen surface stack">
      <h2>状态与浮层</h2>
      <div class="actions">
        <UiBadge>默认</UiBadge>
        <UiBadge size="md">claude-sonnet-4-6</UiBadge>
        <UiStatus :value="true" />
        <UiBadge variant="warning">待确认</UiBadge>
        <UiBadge variant="danger">连接失败</UiBadge>
        <UiButton @click="open = true">打开弹窗</UiButton>
        <UiPopover v-model:open="popoverOpen" title="编辑内容">
          <template #trigger><UiButton>编辑浮窗</UiButton></template>
          <UiField label="内容"><UiInput v-model="text" /></UiField>
          <template #footer="{ close }"><UiButton @click="close()">完成</UiButton></template>
        </UiPopover>
        <UiConfirmPopover message="删除这条配置？">
          <UiButton variant="danger">删除配置</UiButton>
        </UiConfirmPopover>
        <UiDropdownMenu
          label="操作菜单"
          :items="[
            { value: 'copy', label: '复制', icon: 'copy' },
            { value: 'delete', label: '删除', icon: 'delete', danger: true },
          ]"
          @select="notify($event === 'copy' ? '已复制' : '已删除')"
        >
          <UiButton icon="more">更多操作</UiButton>
        </UiDropdownMenu>
      </div>
      <UiAlert variant="info">配置草稿已更新</UiAlert>
      <UiAlert>连接失败，请检查地址和凭据</UiAlert>
      <UiTabs v-model="tab" :items="options" />
      <UiSegmented v-model="tab" :items="options" />
      <UiEmptyState title="尚未添加机器" description="添加一台 SSH 机器即可编辑远程配置。" />
    </section>
    <UiSelectionBar v-if="selected.length" :count="selected.length" @clear="selected = []">
      <UiButton @click="notify('示例导出')">导出所选</UiButton>
      <UiButton variant="danger-outline" @click="notify('示例删除')">删除</UiButton>
    </UiSelectionBar>
    <UiTable
      v-model:selected="selected"
      :rows="rows"
      :row-key="(row) => row.id"
      :columns="[
        { key: 'name', label: '模型', width: 600, sortable: true },
        { key: 'status', label: '状态' },
      ]"
      selectable
      storage-key="components"
    >
      <template #cell-status="{ row }"><UiStatus :value="row.status" /></template>
    </UiTable>
    <UiPagination v-model:page="page" :page-size="20" :total="76" />
    <section class="specimen surface stack">
      <h2>条目选择</h2>
      <UiSelectableRow :selected="selectedRow === 'primary'" @select="selectedRow = 'primary'">
        主凭据
        <template #actions><UiSwitch v-model="checked" label="启用主凭据" /></template>
      </UiSelectableRow>
      <UiSelectableRow :selected="selectedRow === 'fallback'" @select="selectedRow = 'fallback'">
        备用凭据
      </UiSelectableRow>
    </section>
    <section class="specimen surface stack">
      <h2>排序列表</h2>
      <UiSortableList
        :items="ordered"
        :row-key="(item) => item.id"
        label="示例目标"
        @reorder="reorder"
      >
        <template #default="{ item }">{{ item.name }}</template>
      </UiSortableList>
    </section>
    <UiDialog v-model:open="open" title="配置文件">
      <UiCodeEditor v-model="code" language="json" />
      <template #footer>
        <UiButton @click="open = false">取消</UiButton>
        <UiButton variant="primary" @click="saveExample">保存</UiButton>
      </template>
    </UiDialog>
  </div>
</template>
<style scoped>
.specimen {
  padding: var(--space-6);
}
</style>
