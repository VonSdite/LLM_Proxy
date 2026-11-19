<script setup lang="ts">
import { computed } from 'vue'
import type { AgentId, AgentPreview } from '@/api/agents'
import { UiBadge, UiButton, UiIcon, UiIconButton } from '@/components/ui'
const props = defineProps<{
  agent: AgentId
  preview?: Partial<AgentPreview> & { pending: boolean; error: string; conflict?: boolean }
  dirty?: boolean
  saving?: boolean
  disabled?: boolean
}>()
const rows = computed(() => props.preview?.rows || [])
const emit = defineEmits<{ save: []; refresh: [] }>()
function display(value: unknown) {
  return value == null || value === ''
    ? '—'
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value)
}
</script>
<template>
  <section class="agent-preview">
    <header class="section-heading">
      <div class="actions">
        <h2>保存预览</h2>
        <span class="agent-preview-feedback" :class="{ danger: preview?.error }" role="status">
          <UiIcon v-if="preview?.pending" name="loading" :size="13" />
          {{
            preview?.error ||
            (preview?.pending ? '正在检查…' : preview?.hasChanges ? '有待保存的修改' : '无修改')
          }}
        </span>
        <UiIconButton
          v-if="preview?.error"
          icon="refresh"
          label="刷新"
          size="xs"
          :disabled="disabled || saving"
          @click="emit('refresh')"
        />
      </div>
      <UiButton
        variant="primary"
        icon="save"
        :loading="saving"
        :disabled="
          disabled ||
          preview?.pending ||
          (preview?.conflict
            ? !(dirty || preview.hasChanges)
            : !preview?.hasChanges || !!preview?.error)
        "
        @click="emit('save')"
      >
        保存
      </UiButton>
    </header>
    <div class="agent-preview-list" :aria-busy="preview?.pending || undefined">
      <div
        v-for="row in rows"
        :key="`${row.file}:${row.path}`"
        class="agent-preview-row"
        :class="{ 'agent-preview-row--write': row.kind === 'add' || row.kind === 'change' }"
      >
        <code class="agent-preview-key">{{ row.path }}</code>
        <span class="agent-preview-values">
          <span class="muted" :title="display(row.current)">{{ display(row.current) }}</span>
          <UiIcon name="right" :size="12" />
          <span :title="display(row.next)">{{ display(row.next) }}</span>
        </span>
        <UiBadge
          :variant="row.kind === 'same' ? 'neutral' : row.kind === 'remove' ? 'danger' : 'info'"
        >
          {{ row.kind === 'same' ? '保持' : row.kind === 'remove' ? '删除' : '写入' }}
        </UiBadge>
      </div>
      <p v-if="!preview?.rows?.length" class="agent-preview-empty muted">
        {{ preview?.pending ? '正在读取配置…' : '填写配置后查看保存内容' }}
      </p>
    </div>
  </section>
</template>
<style scoped>
.agent-preview {
  padding-top: var(--space-2);
}

.agent-preview-feedback {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: var(--muted);
}

.agent-preview-feedback.danger {
  color: var(--danger);
}

.agent-preview-list {
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  overflow: hidden;
}

.agent-preview-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(160px, 1fr) auto;
  gap: var(--space-3);
  align-items: center;
  min-height: 38px;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-base);
}

.agent-preview-row + .agent-preview-row {
  border-top: 1px solid var(--border);
}

.agent-preview-row--write {
  background: var(--selected);
  box-shadow: inset 2px 0 var(--link);
}

.agent-preview-key {
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}

.agent-preview-values {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.agent-preview-values span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-preview-empty {
  padding: var(--space-3);
  font-size: var(--text-sm);
}

@media (width <= 600px) {
  .agent-preview-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .agent-preview-key {
    grid-column: 1 / -1;
  }

  .agent-preview-values {
    min-width: 0;
  }
}
</style>
