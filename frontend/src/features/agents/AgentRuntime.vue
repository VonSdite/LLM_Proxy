<script setup lang="ts">
import { computed } from 'vue'
import type { AgentDraft, AgentId } from '@/api/agents'
import { UiField, UiSelect, type SelectOption } from '@/components/ui'
import { CLAUDE_MODES, setRuntime } from './configuration'
const props = defineProps<{ agent: AgentId; draft: AgentDraft; disabled?: boolean }>()
const choices = (entries: [string, string][]) => [
  { value: '', label: '不设置' },
  ...entries.map(([value, label]) => ({ value, label })),
]
const tri = [
  { value: 'unset', label: '不设置' },
  { value: 'enabled', label: '启用' },
  { value: 'disabled', label: '禁用' },
]
const fields = computed<{ key: string; label: string; help: string; options: SelectOption[] }[]>(
  () =>
    props.agent === 'claude'
      ? [
          {
            key: 'permissionMode',
            label: '权限模式',
            help: '写入 permissions.defaultMode。命令行参数优先；auto 和 bypassPermissions 使用用户级 settings.json，组织策略仍可限制。IS_SANDBOX 与命令沙箱是独立设置。',
            options: choices([
              ['default', 'default（手动确认）'],
              ['acceptEdits', 'acceptEdits（自动接受编辑）'],
              ['plan', 'plan（规划）'],
              ['auto', 'auto（自动审核）'],
              ['dontAsk', 'dontAsk（仅预授权）'],
              ['bypassPermissions', 'bypassPermissions（跳过确认）'],
            ]),
          },
          {
            key: 'sandboxEnabled',
            label: '命令沙箱',
            help: '写入布尔值 sandbox.enabled，控制 Bash 命令的文件系统与网络隔离。选择不设置时移除该覆盖项。',
            options: tri,
          },
          ...CLAUDE_MODES.map((setting) => ({
            ...setting,
            help: `${setting.help} 写入 env.${setting.env}。`,
            options:
              setting.key === 'disableNonessentialTraffic'
                ? [
                    { value: 'unset', label: '不设置' },
                    { value: 'enabled', label: '禁用' },
                    { value: 'disabled', label: '允许' },
                  ]
                : tri,
          })),
          {
            key: 'skipWebFetchPreflight',
            label: '跳过 WebFetch 预检',
            help: '写入 settings.json 顶层 skipWebFetchPreflight。启用后跳过 WebFetch 域名安全预检；不会禁用 WebFetch 或 WebSearch 工具。',
            options: tri,
          },
        ]
      : props.agent === 'codex'
        ? [
            {
              key: 'serviceTier',
              label: 'Service tier',
              help: '写入 service_tier。auto 遵循项目配置；default 为标准处理；flex 为弹性处理；priority 为优先处理。实际可用性由上游决定。',
              options: choices([
                ['auto', 'auto（自动）'],
                ['default', 'default（标准）'],
                ['flex', 'flex'],
                ['priority', 'priority（Fast）'],
              ]),
            },
            {
              key: 'sandboxMode',
              label: 'Sandbox mode',
              help: '写入 sandbox_mode，控制命令执行时的文件系统权限。',
              options: choices([
                ['read-only', 'read-only（只读）'],
                ['workspace-write', 'workspace-write（工作区可写）'],
                ['danger-full-access', 'danger-full-access（完全访问）'],
              ]),
            },
            {
              key: 'approvalPolicy',
              label: 'Approval policy',
              help: '写入 approval_policy，控制执行命令前的确认策略。',
              options: choices([
                ['untrusted', 'untrusted（非信任需确认）'],
                ['on-request', 'on-request（按需确认）'],
                ['never', 'never（不请求确认）'],
              ]),
            },
            {
              key: 'memories',
              label: '记忆',
              help: '允许 Codex 生成和使用跨会话记忆，写入 features.memories。不设置时移除该字段并遵循 Codex 默认行为，当前默认关闭。',
              options: tri,
            },
          ]
        : [],
)
</script>
<template>
  <div v-if="fields.length" class="form-grid form-grid--four" role="group" aria-label="运行设置">
    <UiField v-for="field in fields" :key="field.key" :label="field.label" :help="field.help">
      <UiSelect
        :model-value="draft.runtime[field.key] || ''"
        :options="field.options"
        :disabled="disabled"
        @update:model-value="setRuntime(draft, field.key, $event)"
      />
    </UiField>
  </div>
</template>
