<script setup lang="ts">
import { computed } from 'vue'
import type { AgentBootstrap, AgentDraft, AgentId, LimitKind } from '@/api/agents'
import { UiField, UiNumberInput, UiSelect } from '@/components/ui'
import { derivedCompact, limitCandidates, setLimit, setRuntime } from './configuration'
const props = defineProps<{
  agent: AgentId
  draft: AgentDraft
  bootstrap: AgentBootstrap
  disabled?: boolean
}>()
const effortKey = computed(() =>
  props.agent === 'claude'
    ? 'effortLevel'
    : props.agent === 'codex'
      ? 'modelReasoningEffort'
      : 'reasoningEffort',
)
const efforts = computed(() => [
  { value: '', label: '不设置' },
  ...(props.agent === 'claude'
    ? ['low', 'medium', 'high', 'xhigh', 'max', 'auto']
    : props.agent === 'codex'
      ? ['minimal', 'low', 'medium', 'high', 'xhigh', 'max']
      : ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
  ).map((value) => ({ value, label: value === 'auto' ? 'auto（API 默认）' : value })),
])
const derived = computed(() => derivedCompact(props.agent, props.draft))
function candidates(kind: LimitKind) {
  return limitCandidates(props.agent, props.draft, kind, props.bootstrap).map((item) => ({
    value: item.value,
    label: `${item.value.toLocaleString()}${kind === 'compact' ? '%' : ''}`,
    description: `${item.referenceModel} · ${item.label}`,
  }))
}
</script>
<template>
  <div class="form-grid form-grid--four" role="group" aria-label="模型参数">
    <UiField
      label="思考强度"
      help="控制模型投入推理的程度。更高档位通常需要更多时间和 Token，实际可用档位由模型与上游服务决定。"
    >
      <UiSelect
        :model-value="draft.runtime[effortKey] || ''"
        :options="efforts"
        :disabled="disabled"
        @update:model-value="setRuntime(draft, effortKey, $event)"
      />
    </UiField>
    <UiField
      :label="agent === 'claude' ? '压缩窗口' : '上下文窗口'"
      :help="
        agent === 'claude'
          ? 'Claude 自动压缩使用的窗口大小，写入 CLAUDE_CODE_AUTO_COMPACT_WINDOW。候选值包含参考模型名，选择后写入草稿。'
          : '当前模型可使用的上下文 Token 数。候选优先展示 Provider 设置，再展示模型参数表中的参考值。'
      "
    >
      <UiNumberInput
        :model-value="draft.limits.context"
        :step="1"
        :suggestions="candidates('context')"
        :min="1"
        :disabled="disabled"
        placeholder="不设置"
        @commit="setLimit(draft, 'context', $event)"
      />
    </UiField>
    <UiField
      label="压缩阈值 %"
      help="达到上下文窗口的指定比例后触发压缩。Codex 保存精确的压缩上限，opencode 保存预留 Token；未修改百分比时保留原文件中的精确值。"
    >
      <template #label>
        <small v-if="agent !== 'claude' && derived.value != null" class="muted number">
          · {{ derived.label }} {{ derived.value.toLocaleString() }}
        </small>
      </template>
      <UiNumberInput
        :model-value="draft.limits.compact"
        :suggestions="candidates('compact')"
        :min="1"
        :max="agent === 'claude' ? 100 : 99"
        :disabled="disabled"
        placeholder="不设置"
        @commit="setLimit(draft, 'compact', $event)"
      />
    </UiField>
    <UiField
      v-if="agent !== 'codex'"
      label="最大输出"
      help="单次响应允许生成的最大 Token 数。该值不能提高模型或上游服务本身的输出限制。"
    >
      <UiNumberInput
        :model-value="draft.limits.output"
        :step="1"
        :suggestions="candidates('output')"
        :min="1"
        :disabled="disabled"
        placeholder="不设置"
        @commit="setLimit(draft, 'output', $event)"
      />
    </UiField>
  </div>
</template>
