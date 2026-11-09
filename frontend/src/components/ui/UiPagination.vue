<script setup lang="ts">
import { computed } from 'vue'
import UiIconButton from './UiIconButton.vue'
import UiSelect from './UiSelect.vue'
import UiNumberInput from './UiNumberInput.vue'
const props = withDefaults(
  defineProps<{
    page: number
    pageSize: number
    total: number
    sizes?: number[]
    disabled?: boolean
  }>(),
  { sizes: () => [20, 50, 100] },
)
const emit = defineEmits<{ 'update:page': [value: number]; 'update:pageSize': [value: number] }>()
const pages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const visible = computed(() => props.total > Math.min(props.pageSize, ...props.sizes))
function pageSizeChanged(value: string) {
  emit('update:pageSize', Number(value))
  emit('update:page', 1)
}
</script>
<template>
  <div v-if="visible" class="ui-pagination">
    <span class="muted number">共 {{ total }} 项</span>
    <div class="ui-pagination-size">
      <UiSelect
        :model-value="String(pageSize)"
        :options="sizes.map((size) => ({ value: String(size), label: `${size} / 页` }))"
        :disabled="disabled"
        aria-label="每页条数"
        @update:model-value="pageSizeChanged"
      />
    </div>
    <UiIconButton
      icon="left"
      label="上一页"
      :disabled="disabled || page <= 1"
      @click="emit('update:page', page - 1)"
    />
    <div class="ui-pagination-jump">
      <UiNumberInput
        :model-value="page"
        :min="1"
        :max="pages"
        :step="1"
        :disabled="disabled"
        aria-label="跳转页码"
        @commit="$event !== null && emit('update:page', $event)"
      />
      <span class="number" aria-live="polite">/ {{ pages }}</span>
    </div>
    <UiIconButton
      icon="right"
      label="下一页"
      :disabled="disabled || page >= pages"
      @click="emit('update:page', page + 1)"
    />
  </div>
</template>
<style scoped>
.ui-pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--text-sm);
}

.ui-pagination > .muted {
  margin-right: auto;
}

.ui-pagination-size {
  width: 110px;
}

.ui-pagination-jump {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.ui-pagination-jump > :first-child {
  width: 68px;
}
</style>
