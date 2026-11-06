<script setup lang="ts">
import { ref } from 'vue'
import { authGroupsApi, type AuthGroup } from '@/api/providers'
import { errorMessage } from '@/api/client'
import { useLifetime } from '@/features/shared/useLifetime'
import ConfigFileEditor from '@/features/shared/ConfigFileEditor.vue'
import { entryDraft, entryErrors, entryLimits, entryPayload, entriesYaml } from './auth-entry'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmPopover,
  UiDialog,
  UiField,
  UiIconButton,
  UiInput,
  UiNumberInput,
  UiSecretInput,
  UiSelect,
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
const yamlSource = ref<{ path: string; text: string; version: string }>()
const yamlOpen = ref(false)
const fileInput = ref<HTMLInputElement>()
const signal = useLifetime()
let headerKey = -1
function add() {
  const entry = entryDraft()
  entries.value.push(entry)
  current.value = entry.key
}
function remove(key: number) {
  entries.value = entries.value.filter((entry) => entry.key !== key)
  if (current.value === key) current.value = entries.value[0]?.key
}
function openYaml(text = entriesYaml(entries.value.map(entryPayload))) {
  yamlSource.value = { path: 'Auth Entries.yaml', text, version: '' }
  yamlOpen.value = true
}
async function readFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (file) {
    openYaml(await file.text())
    if (fileInput.value) fileInput.value.value = ''
  }
}
async function applyYaml(text: string) {
  const result = await authGroupsApi.importEntries(text, signal)
  if (signal.aborted) return
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
</script>
<template>
  <UiDialog
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
          help="最小并发优先选择当前请求数最少的条目；粘滞故障切换持续使用最近成功条目，直到不可用。"
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
        <UiField label="默认 429 冷却" unit="秒" :error="errors.cooldown">
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
        <h2>认证条目</h2>
        <div class="actions">
          <UiButton size="sm" :disabled="busy" @click="openYaml()">编辑 YAML</UiButton>
          <UiButton size="sm" icon="upload" :disabled="busy" @click="fileInput?.click()">
            导入
          </UiButton>
          <UiButton size="sm" icon="plus" :disabled="busy" @click="add">添加条目</UiButton>
          <input
            ref="fileInput"
            type="file"
            accept=".yaml,.yml,.json"
            class="sr-only"
            aria-label="导入认证条目"
            @change="readFile"
          />
        </div>
      </div>
      <UiAlert v-if="errors.entries">{{ errors.entries }}</UiAlert>
      <div class="entry-workspace surface">
        <nav class="entry-sidebar" aria-label="认证条目">
          <UiButton
            v-for="entry in entries"
            :key="entry.key"
            :variant="current === entry.key ? 'secondary' : 'ghost'"
            :disabled="busy"
            :aria-pressed="current === entry.key"
            @click="current = entry.key"
          >
            <span class="truncate">{{ entry.id || '未命名' }}</span>
            <UiBadge
              v-if="Object.keys(errors).some((key) => key.startsWith(`${entry.key}:`))"
              variant="danger"
            >
              !
            </UiBadge>
          </UiButton>
        </nav>
        <div class="entry-content">
          <div
            v-for="entry in entries"
            v-show="entry.key === current"
            :key="entry.key"
            class="stack"
          >
            <div class="toolbar">
              <UiSwitch v-model="entry.enabled" :disabled="busy">启用条目</UiSwitch>
              <UiConfirmPopover message="移除此认证条目？" @confirm="remove(entry.key)">
                <UiButton size="sm" variant="danger" :disabled="busy">移除条目</UiButton>
              </UiConfirmPopover>
            </div>
            <UiField label="Entry ID" required :error="errors[`${entry.key}:id`]">
              <UiInput v-model="entry.id" mono :disabled="busy" />
            </UiField>
            <div class="form-grid form-grid--four">
              <UiField
                v-for="limit in entryLimits"
                :key="limit.key"
                :label="limit.label"
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
            <UiField label="Headers" group required :error="errors[`${entry.key}:headers`]">
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
    <template #footer>
      <UiButton :disabled="busy" @click="emit('close')">取消</UiButton>
      <UiButton variant="primary" type="submit" form="auth-group-form" :loading="busy">
        保存
      </UiButton>
    </template>
  </UiDialog>
  <ConfigFileEditor
    v-if="yamlOpen && yamlSource"
    v-model:open="yamlOpen"
    :source="yamlSource"
    language="yaml"
    :save="applyYaml"
    save-unchanged
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
  grid-template-columns: 180px minmax(0, 1fr);
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
    flex-direction: row;
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
