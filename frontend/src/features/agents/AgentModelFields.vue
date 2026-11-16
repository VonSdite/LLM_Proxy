<script setup lang="ts">
import type { AgentBootstrap, AgentDraft, AgentId } from '@/api/agents'
import { UiField } from '@/components/ui'
import ModelSelect from '@/features/shared/ModelSelect.vue'
import { agentVersionHelp } from './versionHelp'
import {
  CLAUDE_MODEL_KEYS,
  setAllClaudeModels,
  setModel,
  uniformClaudeModel,
} from './configuration'
defineProps<{
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
const labels: Record<(typeof CLAUDE_MODEL_KEYS)[number], string> = {
  ANTHROPIC_DEFAULT_HAIKU_MODEL: 'Haiku 模型（轻量任务）',
  ANTHROPIC_DEFAULT_OPUS_MODEL: 'Opus 模型（复杂任务）',
  ANTHROPIC_DEFAULT_SONNET_MODEL: 'Sonnet 模型（日常编程）',
  ANTHROPIC_MODEL: '默认主模型',
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
        :help="[
          '一次设置下面全部四个模型变量。本服务地址下选择即同步；外部地址下输入即同步，清空也会清空四项。四项不同则此处留空，全部一致则显示共同 ID。点击保存后才写入文件。',
          '带 [1M] 的候选会在模型 ID 后保存 [1m] 标记，向 Claude Code 声明大上下文；不会自动改变下方参数或扩展上游模型的真实容量，仍需上游支持。',
        ]"
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
      <UiField v-for="key in CLAUDE_MODEL_KEYS" :key="key" :label="labels[key]">
        <template #help>
          <p>{{ descriptions[key] }}</p>
          <p>写入 env.{{ key }}；不设置时移除该覆盖项。</p>
          <p>带 [1M] 的候选声明大上下文，仍需上游支持，不会自动扩展模型容量。</p>
          <p v-for="version in agentVersionHelp(agent, key)" :key="version">{{ version }}</p>
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
    <UiField
      label="模型"
      :help="[
        agent === 'codex'
          ? 'Codex 默认模型，写入 config.toml 的 model。'
          : 'opencode 默认模型，写入 model 并关联对应 Provider 配置。',
        '本服务使用完整模型 ID；外部地址填写对应服务的模型 ID。参数参考值不会自动覆盖手动配置。',
        ...agentVersionHelp(agent, 'model'),
      ]"
    >
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
