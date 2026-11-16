<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AgentId } from '@/api/agents'
import {
  UiButton,
  UiDialog,
  UiHelpPopover,
  UiIconButton,
  UiSortableList,
  UiSwitch,
} from '@/components/ui'
import { moveItem, type RowMove } from '@/features/shared/order'
import { AGENT_DEFINITIONS } from './registry'
type TabItem = { id: AgentId; label: string; visible: boolean }
const versionHelp: Record<string, string[]> = {
  opencode: [
    'V1 是 Opencode 的第一代版本，对应 1.x 客户端；这里区分的是软件版本，不是模型版本。可运行 opencode --version 查看已安装版本。',
    '此页签编辑 V1 配置：provider / options、permission 和 compaction.reserved；已有 V2 字段保持原样。',
    '两个版本默认使用同一配置路径。需要分别管理时可指定不同文件；显示或切换页签不会安装、升级或迁移 Opencode。',
  ],
  opencode_v2: [
    'V2 是 Opencode 的第二代新版，对应 2.x 客户端；配置结构与部分功能不同于 V1。可运行 opencode --version 查看已安装版本。',
    '此页签编辑 V2 配置：providers / settings、permissions 规则数组和 compaction.buffer；V2 也兼容部分 V1 字段，但本页不会自动迁移整个文件。',
    '两个版本默认使用同一配置路径。需要分别管理时可指定不同文件；显示或切换页签不会安装或升级 Opencode。',
  ],
}
const props = defineProps<{
  items: { id: AgentId; label: string; visible: boolean }[]
  disabled?: boolean
}>()
const emit = defineEmits<{
  change: [items: TabItem[]]
}>()
const open = ref(false)
const draft = ref<TabItem[]>([])
const baseline = ref('')
const dirty = computed(() => JSON.stringify(draft.value) !== baseline.value)
function setOpen(value: boolean) {
  if (value) {
    draft.value = props.items.map((item) => ({ ...item }))
    baseline.value = JSON.stringify(draft.value)
  }
  open.value = value
}
function save() {
  emit('change', draft.value)
  open.value = false
}
function setVisible(id: AgentId, visible: boolean) {
  if (!visible && draft.value.filter((item) => item.visible).length === 1) return
  draft.value = draft.value.map((item) => (item.id === id ? { ...item, visible } : item))
}
function reorder(move: RowMove) {
  draft.value = moveItem(draft.value, move, (item) => item.id)
}
function reset() {
  draft.value = AGENT_DEFINITIONS.map(({ id, label }) => ({ id, label, visible: true }))
}
</script>
<template>
  <UiIconButton
    icon="settings"
    label="设置 Agent 页签"
    :disabled="disabled"
    size="sm"
    @click="setOpen(true)"
  />
  <UiDialog
    :open="open"
    title="Agent 页签"
    size="sm"
    :dirty="dirty"
    :save="save"
    @update:open="setOpen"
  >
    <div class="stack">
      <UiSortableList
        :items="draft"
        :row-key="(item) => item.id"
        :disabled="disabled"
        label="Agent 页签顺序"
        @reorder="reorder"
      >
        <template #default="{ item }">
          <div class="agent-tab-setting">
            <span class="agent-tab-label">
              {{ item.label }}
              <UiHelpPopover
                v-if="versionHelp[item.id]"
                :label="`${item.label} 说明`"
                :text="versionHelp[item.id]"
              />
            </span>
            <UiSwitch
              :model-value="item.visible"
              :label="`显示 ${item.label}`"
              :disabled="
                disabled || (item.visible && draft.filter((entry) => entry.visible).length === 1)
              "
              @update:model-value="setVisible(item.id, $event)"
            />
          </div>
        </template>
      </UiSortableList>
    </div>
    <template #footer="{ close }">
      <UiButton icon="reset" :disabled="disabled" @click="reset">恢复默认</UiButton>
      <UiButton @click="close()">取消</UiButton>
      <UiButton variant="primary" :disabled="disabled" @click="save">保存</UiButton>
    </template>
  </UiDialog>
</template>
<style scoped>
.agent-tab-setting {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}

.agent-tab-label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
</style>
