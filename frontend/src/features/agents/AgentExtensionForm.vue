<script setup lang="ts">
import { computed } from 'vue'
import type {
  AgentBootstrap,
  AgentDefinition,
  AgentDraft,
  AgentField,
  AgentMachine,
} from '@/api/agents'
import { UiCombobox, UiField, UiInput, UiNumberInput, UiSelect } from '@/components/ui'
import ApiKeyField from '@/features/shared/ApiKeyField.vue'
import ModelSelect from '@/features/shared/ModelSelect.vue'
import DisabledProvidersField from './DisabledProvidersField.vue'
import { defaultBaseUrl, isOwnServiceUrl, modelCandidates } from './configuration'

const props = defineProps<{
  definition: AgentDefinition
  draft: AgentDraft
  bootstrap: AgentBootstrap
  machine: AgentMachine
  machines: AgentMachine[]
}>()
const baseUrl = computed(() => {
  const field = props.definition.fields?.find((field) => field.role === 'baseUrl')
  return String(field ? props.draft.values?.[field.id] || '' : '')
})
const ownService = computed(() => isOwnServiceUrl(baseUrl.value, props.bootstrap, props.machines))
const addressOptions = computed(() => {
  const value = defaultBaseUrl(props.machine, props.definition.id, props.bootstrap)
  return [{ label: value, value }]
})
function value(field: AgentField) {
  const stored = props.draft.values?.[field.id]
  return stored == null ? '' : String(stored)
}
function update(field: AgentField, value: string) {
  const draft = props.draft
  const next =
    value === ''
      ? null
      : field.component === 'boolean'
        ? value === 'true'
        : field.component === 'number'
          ? Number(value)
          : value
  draft.values![field.id] = next
  draft.customFields = draft.customFields?.filter((id) => id !== field.id)
}
function options(field: AgentField) {
  const entries =
    field.component === 'boolean'
      ? [
          { value: 'true', label: '启用' },
          { value: 'false', label: '禁用' },
        ]
      : field.options || []
  return [
    ...(!field.required ? [{ value: '', label: '不设置' }] : []),
    ...(props.draft.customFields?.includes(field.id)
      ? [{ value: '__custom__', label: '保留自定义配置', disabled: true }]
      : []),
    ...entries,
  ]
}
const groups = computed(() => {
  const result = new Map<string, AgentField[]>()
  for (const field of props.definition.fields || []) {
    if (field.showWhen && props.draft.values?.[field.showWhen.field] !== field.showWhen.equals)
      continue
    const group = field.group || 'settings'
    if (!result.has(group)) result.set(group, [])
    result.get(group)!.push(field)
  }
  return [...result].map(([id, fields]) => ({
    id,
    fields,
    columns: props.definition.groups?.[id] || 2,
  }))
})
function suggestions(field: AgentField) {
  const model = props.definition.fields?.find((item) => item.component === 'model')
  if (!field.limit || !model) return []
  return modelCandidates(value(model), props.bootstrap)[field.limit].map((item) => ({
    value: item.value,
    label: item.value.toLocaleString(),
    description: `${value(model)} · ${item.label}`,
  }))
}
function needsModel(field: AgentField) {
  return (
    ['dsh', 'opencode_v2'].includes(props.definition.id) &&
    !props.draft.values?.model &&
    ['context', 'output', 'effort'].includes(field.id)
  )
}
</script>
<template>
  <div class="stack">
    <div
      v-for="group in groups"
      :key="group.id"
      class="form-grid agent-extension-grid"
      :style="{
        '--agent-columns': group.columns,
        '--agent-narrow-columns': Math.min(group.columns, 2),
      }"
    >
      <UiField
        v-for="field in group.fields"
        :key="field.id"
        :label="field.label"
        :help="field.help"
        :required="field.required"
      >
        <UiCombobox
          v-if="field.component === 'url'"
          :model-value="value(field)"
          :options="addressOptions"
          allow-custom
          placeholder="输入 API 地址"
          @update:model-value="update(field, $event)"
        />
        <ApiKeyField
          v-else-if="field.component === 'secret'"
          :model-value="value(field)"
          :candidates="bootstrap.apiKeys"
          :offer-candidates="ownService && bootstrap.apiKeyEnabled"
          @update:model-value="update(field, $event)"
        />
        <ModelSelect
          v-else-if="field.component === 'model'"
          :model-value="value(field)"
          :models="bootstrap.models"
          :own-service="ownService"
          :allow-unset="!field.required"
          @update:model-value="update(field, $event)"
        />
        <UiSelect
          v-else-if="field.component === 'select' || field.component === 'boolean'"
          :model-value="draft.customFields?.includes(field.id) ? '__custom__' : value(field)"
          :options="options(field)"
          :disabled="needsModel(field)"
          @update:model-value="update(field, $event)"
        />
        <DisabledProvidersField
          v-else-if="definition.id === 'opencode_v2' && field.id === 'disabled-providers'"
          :model-value="value(field)"
          :placeholder="draft.customFields?.includes(field.id) ? '保留自定义配置' : undefined"
          @update:model-value="update(field, $event)"
        />
        <UiCombobox
          v-else-if="field.component === 'text' && field.options?.length"
          :model-value="value(field)"
          :options="options(field)"
          :disabled="needsModel(field)"
          allow-custom
          display-label
          :placeholder="draft.customFields?.includes(field.id) ? '保留自定义配置' : '选择或输入'"
          @update:model-value="update(field, $event)"
        />
        <UiInput
          v-else-if="field.component !== 'number'"
          :model-value="value(field)"
          :required="field.required"
          :placeholder="draft.customFields?.includes(field.id) ? '保留自定义配置' : '不设置'"
          :disabled="needsModel(field)"
          autocomplete="off"
          @update:model-value="update(field, $event)"
        />
        <UiNumberInput
          v-else
          :model-value="value(field) === '' ? null : Number(value(field))"
          :min="field.min"
          :max="field.max"
          :step="field.step || 1"
          :required="field.required"
          :suggestions="suggestions(field)"
          :disabled="needsModel(field)"
          placeholder="不设置"
          @commit="update(field, $event == null ? '' : String($event))"
        />
      </UiField>
    </div>
  </div>
</template>
<style scoped>
.agent-extension-grid {
  grid-template-columns: repeat(var(--agent-columns), minmax(0, 1fr));
}

@media (width <= 1024px) {
  .agent-extension-grid {
    grid-template-columns: repeat(var(--agent-narrow-columns), minmax(0, 1fr));
  }
}

@media (width <= 600px) {
  .agent-extension-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
