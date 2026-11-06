<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiButton, UiCheckbox, UiDialog, UiEmptyState, UiInput } from '@/components/ui'
const props = defineProps<{ title: string; models: string[]; initial?: string[] }>()
const emit = defineEmits<{ close: []; apply: [models: string[]] }>()
const keyword = ref('')
const selected = ref(new Set(props.initial || []))
const visible = computed(() =>
  props.models.filter((model) => model.toLowerCase().includes(keyword.value.toLowerCase())),
)
const allVisible = computed(
  () => visible.value.length > 0 && visible.value.every((model) => selected.value.has(model)),
)
const someVisible = computed(() => visible.value.some((model) => selected.value.has(model)))
function toggle(model: string, enabled: boolean) {
  if (enabled) selected.value.add(model)
  else selected.value.delete(model)
}
function toggleVisible(enabled: boolean) {
  visible.value.forEach((model) => toggle(model, enabled))
}
</script>
<template>
  <UiDialog open :title="title" @update:open="emit('close')">
    <div class="stack">
      <UiInput v-model="keyword" placeholder="搜索模型" aria-label="筛选候选模型" />
      <div class="toolbar">
        <UiCheckbox
          :model-value="allVisible ? true : someVisible ? 'indeterminate' : false"
          :disabled="!visible.length"
          @update:model-value="toggleVisible"
        >
          选择当前筛选
        </UiCheckbox>
        <small class="muted">已选 {{ selected.size }} / {{ models.length }}</small>
      </div>
      <div class="model-picker">
        <UiCheckbox
          v-for="model in visible"
          :key="model"
          :model-value="selected.has(model)"
          @update:model-value="toggle(model, $event)"
        >
          <code>{{ model }}</code>
        </UiCheckbox>
        <UiEmptyState v-if="!visible.length" title="没有匹配的模型" />
      </div>
    </div>
    <template #footer>
      <UiButton @click="emit('close')">取消</UiButton>
      <UiButton
        variant="primary"
        @click="
          emit(
            'apply',
            models.filter((model) => selected.has(model)),
          )
        "
      >
        添加所选
      </UiButton>
    </template>
  </UiDialog>
</template>
<style scoped>
.model-picker {
  display: grid;
  gap: var(--space-3);
  max-height: 420px;
  overflow: auto;
  padding: var(--space-1);
}

.model-picker code {
  overflow-wrap: anywhere;
}
</style>
