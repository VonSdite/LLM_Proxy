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
const fields = computed<
  { key: string; label: string; help: string | readonly string[]; options: SelectOption[] }[]
>(() =>
  props.agent === 'claude'
    ? [
        {
          key: 'permissionMode',
          label: '权限模式',
          help: [
            '新会话的默认权限模式，写入 permissions.defaultMode，命令行 --permission-mode 优先。',
            'default：需要时确认。acceptEdits：自动接受文件编辑及部分文件操作。plan：先分析与规划，批准计划后执行。',
            'auto：后台分类器审核操作，依赖版本与服务支持，可能增加请求。dontAsk：拒绝需要确认且未获预授权的操作。bypassPermissions：跳过确认，拒绝规则及部分强制检查仍适用。',
            'auto 与 bypassPermissions 应放在用户级 settings.json，项目级文件可能不生效；组织策略仍可限制。Linux/macOS root 的隔离环境检查由 IS_SANDBOX 等标记参与，命令沙箱不能代替它。',
          ],
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
          help: [
            '写入 sandbox.enabled，隔离 Bash 命令的文件系统与网络访问；权限模式控制是否需要批准，沙箱控制获批后能访问什么。',
            '支持 macOS、Linux、WSL2，原生 Windows 不支持。Linux / WSL2 需要 bubblewrap、socat 等依赖；沙箱不可用时是否拒绝执行取决于 sandbox.failIfUnavailable。',
            'IS_SANDBOX 只是环境标记，不会开启此沙箱。选择不设置时移除此覆盖项。',
          ],
          options: tri,
        },
        {
          key: 'attribution',
          label: 'Attribution',
          help: [
            '控制 Claude 在 Git 提交和 PR 描述中添加的署名，写入 settings.json 顶层 attribution。默认提交带 Co-Authored-By，PR 描述带 Claude 署名。',
            '不设置：移除当前文件的覆盖，沿用 Claude 默认行为或其他作用域的配置，不代表关闭署名。禁用：写入 false，关闭提交、PR 和会话链接署名；需要 Claude Code 2.1.281 或更新版本。',
            '旧版本可在配置文件中使用 {"commit":"","pr":"","sessionUrl":false}。已有对象显示为“文件自定义”，修改其他选项时原样保留；commit / pr 可分别设置自定义署名，空字符串表示隐藏。',
            'Attribution Header 控制系统提示中的客户端标识，与这里的 Git / PR 署名独立。',
          ],
          options: [
            { value: 'unset', label: '不设置' },
            { value: 'disabled', label: '禁用' },
            ...(props.draft.runtime.attribution === 'custom'
              ? [{ value: 'custom', label: '文件自定义', disabled: true }]
              : []),
          ],
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
          help: [
            '写入 settings.json 顶层 skipWebFetchPreflight：启用为 true，禁用为 false，不设置移除此字段。',
            '启用后跳过 WebFetch 抓取前向 api.anthropic.com 发起的域名安全检查，适用于无法访问该预检服务的网络。',
            'WebFetch 仍可抓取网页，访问范围继续受权限规则约束；此项不控制 WebSearch 搜索。',
          ],
          options: tri,
        },
      ]
    : props.agent === 'codex'
      ? [
          {
            key: 'serviceTier',
            label: 'Service tier',
            help: [
              '服务处理档位，按选定值写入 service_tier。auto / default / flex / priority 分别表达自动、标准、弹性、优先处理偏好；弹性通常更慢，优先通常费用更高。',
              '可用名称取决于 Codex 版本、模型和上游。新版 Codex 以模型目录公布的档位为准（例如 fast，对应请求中的 priority）；本页未列出的值可通过配置文件编辑器维护。',
            ],
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
            help: [
              'read-only：只读。workspace-write：工作区与允许目录可写。danger-full-access：取消沙箱的文件系统与网络限制。确认策略另行控制是否询问。',
              '写入 sandbox_mode；工作区模式的网络权限还取决于 sandbox_workspace_write.network_access。',
              '新版 Codex 支持 default_permissions 权限配置，不应与 sandbox_mode 混用。已使用权限配置的文件应在文件编辑器中维护；本页不会自动迁移。',
            ],
            options: choices([
              ['read-only', 'read-only（只读）'],
              ['workspace-write', 'workspace-write（工作区可写）'],
              ['danger-full-access', 'danger-full-access（完全访问）'],
            ]),
          },
          {
            key: 'approvalPolicy',
            label: 'Approval policy',
            help: [
              'on-request：允许在需要时请求确认。never：不请求确认，受限操作会失败；不会因此解除沙箱限制。写入 approval_policy。',
              'untrusted 是旧版本选项，非信任命令需确认；当前官方文档已不支持直接设置此值。新版本使用 on-request 或 never，已有旧版配置请结合目标机器版本处理。',
              '组织策略仍可约束这些选项。选择不设置后的移除操作见保存预览。',
            ],
            options: choices([
              ['untrusted', 'untrusted（旧版本）'],
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
