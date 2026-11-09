<script setup lang="ts">
import type { Provider } from '@/api/providers'
import type { RowMove } from '@/features/shared/order'
import { UiBadge, UiButton, UiTable, UiTooltip, type TableColumn } from '@/components/ui'

defineProps<{
  rows: Provider[]
  loading?: boolean
  reorderDisabled?: boolean
  editDisabled?: boolean
}>()
const selected = defineModel<string[]>('selected', { default: () => [] })
const emit = defineEmits<{
  reorder: [move: RowMove]
  edit: [row: Provider]
  models: [row: Provider]
  auth: [name: string]
}>()
const columns: TableColumn[] = [
  { fixed: true, key: 'name', label: 'Provider', width: 176, minWidth: 176 },
  { key: 'api', label: 'API 地址', width: 168, minWidth: 168, maxWidth: 720, grow: 2.5 },
  { fixed: true, key: 'source_format', label: '上游协议', width: 112 },
  { key: 'models', label: '模型', width: 232, minWidth: 192, grow: 3 },
  { fixed: true, key: 'auth_group', label: '认证', width: 104 },
  { fixed: true, key: 'enabled', label: '启用', width: 64, minWidth: 64 },
  { fixed: true, key: 'actions', label: '操作', width: 168, minWidth: 168 },
]
const protocols: Record<Provider['source_format'], string> = {
  openai_chat: 'OpenAI Chat',
  openai_responses: 'Responses',
  claude_chat: 'Claude',
}
</script>
<template>
  <UiTable
    v-model:selected="selected"
    :rows="rows"
    :columns="columns"
    :row-key="(row) => row.name"
    selectable
    reorderable
    :reorder-group="(row) => String(row.enabled)"
    :reorder-disabled="reorderDisabled"
    :loading="loading"
    storage-key="providers"
    empty="没有匹配的 Provider"
    @reorder="emit('reorder', $event)"
  >
    <template #cell-name="{ row }">
      <UiTooltip :text="row.name">
        <UiButton
          class="provider-name"
          variant="link"
          :aria-label="`编辑 Provider ${row.name}`"
          aria-haspopup="dialog"
          :disabled="editDisabled"
          @click="emit('edit', row)"
        >
          <span class="truncate">{{ row.name }}</span>
        </UiButton>
      </UiTooltip>
    </template>
    <template #cell-api="{ row }">
      <div class="provider-endpoint">
        <UiTooltip :text="row.api">
          <span class="provider-address" tabindex="0">{{ row.api }}</span>
        </UiTooltip>
        <div
          v-if="
            row.verify_ssl ||
            row.force_upstream_stream ||
            row.safe_desensitization_enabled ||
            row.hook
          "
          class="provider-metadata"
        >
          <UiBadge v-if="row.verify_ssl">证书校验</UiBadge>
          <UiBadge v-if="row.force_upstream_stream">强制上游流式</UiBadge>
          <UiBadge v-if="row.safe_desensitization_enabled">安全脱敏</UiBadge>
          <UiTooltip v-if="row.hook" :text="row.hook">
            <UiBadge class="provider-hook" tabindex="0" :aria-label="`Hook：${row.hook}`">
              Hook:
              <span class="truncate">{{ row.hook }}</span>
            </UiBadge>
          </UiTooltip>
        </div>
      </div>
    </template>
    <template #cell-source_format="{ row }">
      <span class="provider-protocol">{{ protocols[row.source_format] || row.source_format }}</span>
    </template>
    <template #cell-models="{ row }">
      <div class="provider-models">
        <div class="provider-model-tags">
          <UiTooltip v-for="model in row.model_list.slice(0, 6)" :key="model" :text="model">
            <UiBadge class="provider-model" size="md">
              <span class="truncate">{{ model }}</span>
            </UiBadge>
          </UiTooltip>
        </div>
        <UiButton variant="link" aria-haspopup="dialog" @click="emit('models', row)">
          {{ row.model_list.length }} 个模型
        </UiButton>
      </div>
    </template>
    <template #cell-auth_group="{ row }">
      <UiButton
        v-if="row.auth_group"
        class="provider-auth"
        variant="link"
        @click="emit('auth', row.auth_group)"
      >
        {{ row.auth_group }}
      </UiButton>
      <span v-else class="provider-auth" :class="{ muted: !row.api_key }">
        {{ row.api_key ? 'API Key' : '无认证' }}
      </span>
    </template>
    <template #cell-enabled="{ row }">
      <div class="provider-toggle"><slot name="enabled" :row="row" /></div>
    </template>
    <template #cell-actions="{ row }"><slot name="actions" :row="row" /></template>
  </UiTable>
</template>
<style scoped>
.provider-name {
  display: flex;
  max-width: 100%;
  font-size: var(--text-section);
  font-weight: var(--weight-emphasis);
  line-height: var(--control);
}

.provider-address {
  display: block;
  font-size: var(--text-base);
  line-height: 1.6;
  word-break: break-all;
}

.provider-endpoint {
  display: grid;
  gap: var(--space-2);
  min-width: 0;
}

.provider-metadata {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1);
  min-width: 0;
}

.provider-hook {
  max-width: 100%;
}

.provider-protocol {
  line-height: var(--control);
}

.provider-models {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  justify-items: start;
  gap: var(--space-1);
}

.provider-model-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  width: 100%;
  min-width: 0;
  min-height: var(--control);
  gap: var(--space-1);
}

.provider-auth {
  display: flex;
  align-items: center;
  width: fit-content;
  min-height: var(--control);
  line-height: 1.5;
}

.provider-toggle {
  display: flex;
  align-items: center;
  min-height: var(--control);
}
</style>
