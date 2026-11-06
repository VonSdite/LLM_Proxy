<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ModelPermissions } from '@/api/permissions'
import { UiCheckbox, UiEmptyState, UiInput, UiSegmented } from '@/components/ui'
const props = defineProps<{ modelValue: ModelPermissions; models: string[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: ModelPermissions] }>()
const search = ref('')
const remembered = ref<string[]>(Array.isArray(props.modelValue) ? [...props.modelValue] : [])
watch(
  () => props.modelValue,
  (value) => {
    if (Array.isArray(value)) remembered.value = [...value]
  },
)
const options = computed(() => [
  ...new Set([...props.models, ...(Array.isArray(props.modelValue) ? props.modelValue : [])]),
])
const visible = computed(() =>
  options.value.filter((model) => model.toLowerCase().includes(search.value.toLowerCase())),
)
const selected = computed(
  () => new Set(Array.isArray(props.modelValue) ? props.modelValue : props.models),
)
const allVisible = computed(
  () => visible.value.length > 0 && visible.value.every((model) => selected.value.has(model)),
)
const partial = computed(() => visible.value.some((model) => selected.value.has(model)))
function mode(value: string) {
  emit('update:modelValue', value === 'all' ? '*' : [...remembered.value])
}
function toggle(model: string, enabled: boolean) {
  const next = new Set(selected.value)
  if (enabled) next.add(model)
  else next.delete(model)
  emit('update:modelValue', [...next])
}
function toggleVisible(enabled: boolean) {
  const next = new Set(selected.value)
  for (const model of visible.value) {
    if (enabled) next.add(model)
    else next.delete(model)
  }
  emit('update:modelValue', [...next])
}
</script>
<template>
  <div class="stack">
    <UiSegmented
      :model-value="modelValue === '*' ? 'all' : 'selected'"
      :items="[
        { value: 'all', label: '全部模型', disabled },
        { value: 'selected', label: '指定模型', disabled },
      ]"
      label="模型权限范围"
      @update:model-value="mode"
    />
    <p v-if="modelValue === '*'" class="muted permission-summary">包括后续新增的模型</p>
    <template v-else>
      <UiInput
        v-model="search"
        placeholder="搜索模型"
        aria-label="筛选权限模型"
        :disabled="disabled"
      />
      <div class="permission-toolbar">
        <UiCheckbox
          :model-value="allVisible ? true : partial ? 'indeterminate' : false"
          :disabled="disabled || !visible.length"
          @update:model-value="toggleVisible"
        >
          选择当前筛选
        </UiCheckbox>
        <small class="muted">已选 {{ selected.size }} / {{ options.length }}</small>
      </div>
      <div class="permission-list">
        <UiCheckbox
          v-for="model in visible"
          :key="model"
          :model-value="selected.has(model)"
          :disabled="disabled"
          @update:model-value="toggle(model, $event)"
        >
          <span class="mono">{{ model }}</span>
          <small v-if="!models.includes(model)" class="muted">· 当前不可用</small>
        </UiCheckbox>
        <UiEmptyState v-if="!visible.length" title="没有匹配的模型" />
      </div>
    </template>
  </div>
</template>
<style scoped>
.permission-summary {
  font-size: var(--text-xs);
}

.permission-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.permission-list {
  display: grid;
  align-content: start;
  gap: var(--space-3);
  max-height: 280px;
  overflow: auto;
  padding: var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--input);
}

.permission-list .mono {
  overflow-wrap: anywhere;
}
</style>
