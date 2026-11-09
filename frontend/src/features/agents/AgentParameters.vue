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
      :help="[
        '控制模型投入推理的程度。更高档位通常需要更多时间和 Token，实际可用档位由模型与上游服务决定。',
        agent === 'claude'
          ? '写入 env.CLAUDE_CODE_EFFORT_LEVEL，优先于会话 /effort 和启动参数；auto 表示由 API 决定默认强度。'
          : agent === 'codex'
            ? '写入 config.toml 的 model_reasoning_effort。'
            : '写入当前模型的 options.reasoningEffort。',
        '选择不设置后的移除操作见保存预览；其他配置层仍可能提供默认值。',
      ]"
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
      :help="[
        agent === 'claude'
          ? '自动压缩使用的窗口，单位为 Token，写入 env.CLAUDE_CODE_AUTO_COMPACT_WINDOW；不能扩展模型真实容量。已核对的 Claude Code 2.1.310 支持 100,000–1,000,000，小于下限会忽略，超过上限按 1,000,000 处理，且不超过模型窗口。'
          : `上下文包含历史消息、工具结果和模型输出，单位为 Token，应按模型实际容量填写。写入 ${agent === 'codex' ? 'model_context_window' : '当前模型的 limit.context'}。`,
        '候选优先采用 Provider 的显式设置，再使用模型参数表或推测参考；候选标明参考模型与来源，选择后才更新草稿，不保证上游具备该容量。',
        ...(agent === 'claude'
          ? [
              '优先参考 ANTHROPIC_MODEL；未设置主模型时，列出已设置别名模型的候选。此窗口是全局覆盖，不会按四个模型分别保存。',
            ]
          : []),
      ]"
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
      :help="
        agent === 'claude'
          ? [
              '自动压缩的触发比例，范围 1–100%，写入 env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE。数值越小，越早压缩。',
              '只能提前压缩，不能提高 Claude 默认触发阈值；高于默认值时会被忽略。实际预算还受模型窗口和输出预留影响。',
            ]
          : [
              '自动压缩使用的上下文比例，范围 1–99%。例如 200,000 Token 的窗口设为 85%，对应约 170,000 Token 的压缩上限。',
              agent === 'codex'
                ? '按上下文 × 百分比计算，写入 model_auto_compact_token_limit。'
                : '按上下文 ×（1 − 百分比）计算预留空间，写入 compaction.reserved；上例预留 30,000 Token。',
              '需要先设置上下文窗口。未修改百分比时保留文件中的精确 Token 数；数值改变后失焦或按 Enter 更新标题中的计算结果。',
            ]
      "
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
      :help="[
        '单次回复允许生成的最大 Token 数。不是上下文总容量，也不能提高模型或上游本身的输出限制。',
        agent === 'claude'
          ? '写入 env.CLAUDE_CODE_MAX_OUTPUT_TOKENS。Claude 按模型上限裁剪；更大的输出预算会占用压缩前的可用空间。此项对不同别名模型共用。已核对的 Claude Code 2.1.310 对无法识别的模型默认输出 32,000，上限 128,000；这不代表第三方上游也支持该值。'
          : '写入当前模型的 limit.output，不应超过该模型的上下文窗口。',
      ]"
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
