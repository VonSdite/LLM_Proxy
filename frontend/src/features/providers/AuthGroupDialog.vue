<script setup lang="ts">
import { useDraftChanges } from '@/features/shared/useDraftChanges'
import { nextTick, ref } from 'vue'
import { authGroupsApi, type AuthGroup } from '@/api/providers'
import { errorMessage } from '@/api/client'
import { useLifetime } from '@/features/shared/useLifetime'
import TextImportDialog from '@/features/shared/TextImportDialog.vue'
import { entryDraft, entryErrors, entryLimits, entryPayload, entriesYaml } from './auth-entry'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmPopover,
  UiDialog,
  UiField,
  UiHelpPopover,
  UiIconButton,
  UiInput,
  UiNumberInput,
  UiSecretInput,
  UiSelect,
  UiSelectableRow,
  UiSwitch,
} from '@/components/ui'
const props = defineProps<{ group?: AuthGroup }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const name = ref(props.group?.name || '')
const strategy = ref(props.group?.strategy || 'least_inflight')
const cooldown = ref(props.group?.cooldown_seconds_on_429 ?? null)
const entries = ref(
  props.group?.entries.length ? props.group.entries.map(entryDraft) : [entryDraft()],
)
const current = ref(entries.value[0]?.key)
const errors = ref<Record<string, string>>({})
const invalid = ref<Record<string, boolean>>({})
const busy = ref(false)
const error = ref('')
const yamlSource = ref('')
const yamlOpen = ref(false)
const sidebar = ref<HTMLElement>()
const signal = useLifetime()
let headerKey = -1
async function add() {
  const entry = entryDraft()
  entries.value.push(entry)
  current.value = entry.key
  await nextTick()
  sidebar.value?.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: 'nearest' })
}
function remove(key: number) {
  const index = entries.value.findIndex((entry) => entry.key === key)
  entries.value = entries.value.filter((entry) => entry.key !== key)
  if (current.value === key)
    current.value = entries.value[Math.min(index, entries.value.length - 1)]?.key
}
function openYaml() {
  yamlSource.value = entriesYaml(entries.value.map(entryPayload))
  yamlOpen.value = true
}
async function applyYaml(text: string, importSignal: AbortSignal) {
  const result = await authGroupsApi.importEntries(text, importSignal)
  if (importSignal.aborted) return
  entries.value = result.entries.map(entryDraft)
  current.value = entries.value[0]?.key
  errors.value = {}
  invalid.value = {}
}
async function submit() {
  errors.value = {}
  if (!name.value.trim()) errors.value.name = '请输入 Auth Group 名称'
  if (name.value.includes('/')) errors.value.name = '名称不能包含斜杠'
  if (
    invalid.value.cooldown ||
    (cooldown.value !== null && (!Number.isInteger(cooldown.value) || cooldown.value < 1))
  )
    errors.value.cooldown = '请输入正整数'
  if (!entries.value.length) errors.value.entries = '至少添加一个认证条目'
  let firstError: number | undefined
  for (const entry of entries.value) {
    const found = entryErrors(entry, entries.value)
    for (const limit of entryLimits)
      if (invalid.value[`${entry.key}:${limit.key}`]) found[limit.key] = '请输入正整数'
    for (const [key, value] of Object.entries(found)) errors.value[`${entry.key}:${key}`] = value
    if (Object.keys(found).length && firstError === undefined) firstError = entry.key
  }
  if (firstError !== undefined) current.value = firstError
  if (busy.value || Object.keys(errors.value).length) return
  busy.value = true
  error.value = ''
  try {
    await authGroupsApi.save(
      {
        name: name.value.trim(),
        strategy: strategy.value,
        cooldown_seconds_on_429: cooldown.value,
        entries: entries.value.map(entryPayload),
      },
      props.group?.name,
      signal,
    )
    if (signal.aborted) return
    emit('saved')
    emit('close')
  } catch (reason) {
    if (!signal.aborted) error.value = errorMessage(reason)
  } finally {
    busy.value = false
  }
}
const dirty = useDraftChanges(() => ({
  name: name.value,
  strategy: strategy.value,
  cooldown: cooldown.value,
  entries: entries.value.map((entry) => ({
    id: entry.id,
    enabled: entry.enabled,
    headers: entry.headers.map((header) => ({ name: header.name, value: header.value })),
    limits: entryLimits.map(({ key }) => entry[key] ?? null),
  })),
}))
</script>
<template>
  <UiDialog
    :save="submit"
    :dirty="dirty"
    open
    :title="group ? '编辑 Auth Group' : '新增 Auth Group'"
    size="xl"
    :busy="busy"
    @update:open="emit('close')"
  >
    <form id="auth-group-form" class="stack" @submit.prevent="submit">
      <UiAlert v-if="error">{{ error }}</UiAlert>
      <div class="group-fields">
        <UiField label="名称" required :error="errors.name">
          <UiInput v-model="name" autofocus :disabled="busy" />
        </UiField>
        <UiField
          label="选择策略"
          :help="[
            '最小并发：优先选择当前进行中请求最少的可用条目，适合长流式请求与分摊负载。',
            '粘滞故障切换：持续使用当前条目；冷却、禁用、配额耗尽或并发已满时，再按顺序选择其他可用条目。',
            '限制范围：并发与配额按条目计算。多个 Provider 绑定同一组时，共享凭据与用量。',
          ]"
        >
          <UiSelect
            v-model="strategy"
            :options="[
              { value: 'least_inflight', label: '最小并发' },
              { value: 'sticky_failover', label: '粘滞故障切换' },
            ]"
            :disabled="busy"
          />
        </UiField>
        <UiField
          label="默认 429 冷却"
          unit="秒"
          :error="errors.cooldown"
          :help="[
            '触发条件：条目收到上游 429 限流后暂停使用，只冷却被限流的条目。',
            '时长优先级：有效的 Retry-After → 条目冷却值 → 本组默认值。',
            '留空：本组默认冷却 60 秒。',
          ]"
        >
          <UiNumberInput
            v-model="cooldown"
            :min="1"
            :step="1"
            placeholder="60"
            :disabled="busy"
            @validity-change="invalid.cooldown = !$event"
          />
        </UiField>
      </div>
      <div class="toolbar">
        <div class="actions">
          <h2>认证条目</h2>
          <UiHelpPopover
            label="认证条目说明"
            :text="[
              '每个条目：一组发往上游的认证请求头，并发、冷却与配额分别按条目计算。',
              '编辑 / 导入：直接编辑 YAML，或选择 YAML、JSON 文件，确定后整体替换当前表单中的条目列表。',
              '生效时机：保存认证组后生效。',
            ]"
          />
        </div>
        <UiButton size="sm" :disabled="busy" @click="openYaml">编辑 / 导入</UiButton>
      </div>
      <UiAlert v-if="errors.entries">{{ errors.entries }}</UiAlert>
      <div class="entry-workspace surface">
        <nav ref="sidebar" class="entry-sidebar" aria-label="认证条目">
          <UiSelectableRow
            v-for="entry in entries"
            :key="entry.key"
            class="entry-sidebar-row"
            :selected="current === entry.key"
            :disabled="busy"
            :title="entry.id || '未命名'"
            @select="current = entry.key"
          >
            <span class="truncate">{{ entry.id || '未命名' }}</span>
            <UiBadge
              v-if="Object.keys(errors).some((key) => key.startsWith(`${entry.key}:`))"
              variant="danger"
            >
              !
            </UiBadge>
            <template #actions>
              <UiSwitch
                v-model="entry.enabled"
                :disabled="busy"
                :label="`启用条目 ${entry.id || '未命名'}`"
              />
              <UiConfirmPopover message="移除此认证条目？" @confirm="remove(entry.key)">
                <UiIconButton
                  icon="delete"
                  size="sm"
                  :label="`移除条目 ${entry.id || '未命名'}`"
                  :disabled="busy"
                />
              </UiConfirmPopover>
            </template>
          </UiSelectableRow>
          <UiButton class="entry-add" variant="ghost" icon="plus" :disabled="busy" @click="add">
            添加条目
          </UiButton>
        </nav>
        <div class="entry-content">
          <div
            v-for="entry in entries"
            v-show="entry.key === current"
            :key="entry.key"
            class="stack"
          >
            <UiField
              label="Entry ID"
              required
              :error="errors[`${entry.key}:id`]"
              :help="[
                '作用：组内唯一的凭据标识，用于关联运行状态与配额统计，不是上游 API Key。',
                '命名：建议使用稳定名称，例如 key-01。',
              ]"
            >
              <UiInput v-model="entry.id" mono :disabled="busy" />
            </UiField>
            <div class="form-grid form-grid--four">
              <UiField
                v-for="limit in entryLimits"
                :key="limit.key"
                :label="limit.label"
                :help="limit.help"
                :error="errors[`${entry.key}:${limit.key}`]"
              >
                <UiNumberInput
                  v-model="entry[limit.key]"
                  :min="1"
                  :step="1"
                  :placeholder="limit.key === 'cooldown_seconds_on_429' ? '继承组默认值' : '不限额'"
                  :disabled="busy"
                  @validity-change="invalid[`${entry.key}:${limit.key}`] = !$event"
                />
              </UiField>
            </div>
            <UiField
              label="Headers"
              group
              required
              :error="errors[`${entry.key}:headers`]"
              :help="[
                '作用：所选条目发给上游的认证请求头。',
                'OpenAI 兼容接口：通常填写 Authorization，值为 Bearer 密钥。',
                '其他上游：可能使用 x-api-key，请按上游要求填写完整值。',
                '名称：同一条目内不可重复。',
              ]"
            >
              <div class="stack">
                <div v-for="header in entry.headers" :key="header.key" class="header-row">
                  <UiInput
                    v-model="header.name"
                    placeholder="Authorization"
                    aria-label="Header 名称"
                    :disabled="busy"
                  />
                  <UiSecretInput
                    v-model="header.value"
                    placeholder="Bearer …"
                    aria-label="Header 值"
                    :disabled="busy"
                  />
                  <UiIconButton
                    icon="delete"
                    label="移除 Header"
                    :disabled="busy"
                    @click="entry.headers = entry.headers.filter((item) => item.key !== header.key)"
                  />
                </div>
                <div>
                  <UiButton
                    size="sm"
                    icon="plus"
                    :disabled="busy"
                    @click="entry.headers.push({ key: headerKey--, name: '', value: '' })"
                  >
                    添加 Header
                  </UiButton>
                </div>
              </div>
            </UiField>
          </div>
        </div>
      </div>
    </form>
    <template #footer="{ close }">
      <UiButton :disabled="busy" @click="close()">取消</UiButton>
      <UiButton variant="primary" type="submit" form="auth-group-form" :loading="busy">
        保存
      </UiButton>
    </template>
  </UiDialog>
  <TextImportDialog
    v-if="yamlOpen"
    v-model:open="yamlOpen"
    title="编辑认证条目"
    :initial-value="yamlSource"
    language="yaml"
    confirm-label="确定"
    :help="[
      '直接编辑 YAML，或选择 YAML / JSON 文件填入编辑区。',
      '确定后整体替换当前表单的认证条目；保存 Auth Group 后生效。',
    ]"
    :submit="applyYaml"
  />
</template>
<style scoped>
.group-fields {
  display: grid;
  grid-template-columns: 1fr 1fr 160px;
  gap: var(--space-4);
}

.entry-workspace {
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  min-height: 360px;
}

.entry-sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-2);
  border-right: 1px solid var(--border);
  overflow: auto;
  max-height: 500px;
}

.entry-sidebar > * {
  flex: none;
}

.entry-add {
  width: 100%;
  justify-content: flex-start;
}

.entry-content {
  min-width: 0;
  padding: var(--space-4);
}

.header-row {
  display: grid;
  grid-template-columns: minmax(120px, 1fr) minmax(0, 2fr) auto;
  gap: var(--space-2);
}

@media (width <= 700px) {
  .group-fields {
    grid-template-columns: minmax(0, 1fr);
  }

  .entry-workspace {
    grid-template-columns: minmax(0, 1fr);
  }

  .entry-sidebar {
    max-height: 220px;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }

  .header-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .header-row > :first-child {
    grid-column: 1 / -1;
  }
}
</style>
