<script setup lang="ts">
import type { AgentBootstrap, AgentDraft, AgentId } from '@/api/agents'
import { UiField } from '@/components/ui'
import ModelSelect from '@/features/shared/ModelSelect.vue'
import {
  CLAUDE_MODEL_KEYS,
  modelCandidates,
  setAllClaudeModels,
  setModel,
  uniformClaudeModel,
} from './configuration'
const props = defineProps<{
  agent: AgentId
  draft: AgentDraft
  ownService: boolean
  bootstrap: AgentBootstrap
  disabled?: boolean
}>()
const descriptions: Record<string, string> = {
  ANTHROPIC_DEFAULT_HAIKU_MODEL: 'Haiku 别名对应的模型，通常用于快速、轻量任务。',
  ANTHROPIC_DEFAULT_OPUS_MODEL: 'Opus 别名对应的模型，通常用于复杂分析和编程。',
  ANTHROPIC_DEFAULT_SONNET_MODEL: 'Sonnet 别名对应的模型，通常用于日常编程。',
  ANTHROPIC_MODEL: 'Claude Code 的默认主模型，启动参数和会话模型选择可覆盖此值。',
}
function help(model: string) {
  if (!model) return '选择模型后显示上下文与最大输出参考值。'
  const candidates = modelCandidates(model, props.bootstrap)
  return `${model} · 上下文 ${candidates.context.map((item) => item.value.toLocaleString()).join(' / ')} · 最大输出 ${candidates.output.map((item) => item.value.toLocaleString()).join(' / ')}${candidates.matched ? '' : '（未识别模型，通用参考值）'}`
}
</script>
<template>
  <div
    v-if="agent === 'claude'"
    class="agent-model-group"
    role="group"
    aria-label="Claude 模型设置"
  >
    <div class="agent-model-all">
      <UiField
        label="一键设置模型"
        help="一次设置全部四项。外部地址输入时立即同步；单独修改模型后，四项不同则此处留空。"
      >
        <ModelSelect
          :model-value="uniformClaudeModel(draft)"
          :models="bootstrap.models"
          :own-service="ownService"
          :disabled="disabled"
          large-context
          placeholder="选择并应用到全部"
          @update:model-value="setAllClaudeModels(draft, $event)"
        />
      </UiField>
    </div>
    <div class="form-grid">
      <UiField v-for="key in CLAUDE_MODEL_KEYS" :key="key" :label="key">
        <template #help>
          <p>{{ descriptions[key] }}</p>
          <p>{{ help(draft.models?.[key] || '') }}</p>
          <p>写入 env.{{ key }}；不设置时移除该覆盖项。</p>
        </template>
        <ModelSelect
          :model-value="draft.models?.[key] || ''"
          :models="bootstrap.models"
          :own-service="ownService"
          :disabled="disabled"
          allow-unset
          large-context
          @update:model-value="setModel(draft, $event, key)"
        />
      </UiField>
    </div>
  </div>
  <div v-else class="form-grid">
    <UiField label="模型" :help="help(draft.model || '')">
      <ModelSelect
        :model-value="draft.model || ''"
        :models="bootstrap.models"
        :own-service="ownService"
        :disabled="disabled"
        @update:model-value="setModel(draft, $event)"
      />
    </UiField>
  </div>
</template>
<style scoped>
.agent-model-group {
  display: grid;
  gap: var(--space-4);
  padding: var(--space-4);
  border: 1px solid var(--border);
  border-radius: var(--radius-group);
}

.agent-model-all {
  max-width: 480px;
}
</style>
