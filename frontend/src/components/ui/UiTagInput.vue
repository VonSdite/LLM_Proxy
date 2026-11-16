<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import UiBadge from './UiBadge.vue'
import UiCombobox from './UiCombobox.vue'
import UiIcon from './UiIcon.vue'
import type { SelectOption } from './types'
const props = defineProps<{
  modelValue: string[]
  options: SelectOption[]
  disabled?: boolean
  placeholder?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()
const input = ref('')
const valuesElement = ref<HTMLElement>()
watch(
  () => props.modelValue.length,
  async (length, previous) => {
    if (length <= previous) return
    await nextTick()
    if (valuesElement.value) valuesElement.value.scrollLeft = valuesElement.value.scrollWidth
  },
)
const options = computed(() =>
  props.options.filter((option) => !props.modelValue.includes(option.value)),
)
function add(value: string) {
  if (props.disabled) return
  const values = value.split(/[,，\s]+/).filter(Boolean)
  if (!values.length) return
  emit('update:modelValue', [...new Set([...props.modelValue, ...values])])
  input.value = ''
}
function enter(event: KeyboardEvent) {
  if (!(event.target instanceof HTMLInputElement) || event.isComposing || event.defaultPrevented)
    return
  event.preventDefault()
  add(input.value)
}
</script>
<template>
  <div class="ui-tag-input" :class="{ 'ui-tag-input--filled': modelValue.length }">
    <UiCombobox
      v-model="input"
      :options="options"
      :disabled="disabled"
      :placeholder="modelValue.length ? '添加' : placeholder || '选择或输入'"
      allow-custom
      @select="add"
      @keydown.enter="enter"
    >
      <template #leading>
        <div v-if="modelValue.length" ref="valuesElement" class="ui-tag-values">
          <UiBadge v-for="value in modelValue" :key="value" class="ui-tag-chip">
            <span class="ui-tag-label" :title="value">{{ value }}</span>
            <button
              type="button"
              class="ui-tag-remove"
              :aria-label="`移除 ${value}`"
              :disabled="disabled"
              @click="
                emit(
                  'update:modelValue',
                  modelValue.filter((item) => item !== value),
                )
              "
            >
              <UiIcon name="close" :size="12" />
            </button>
          </UiBadge>
        </div>
      </template>
    </UiCombobox>
  </div>
</template>
<style scoped>
.ui-tag-input {
  min-width: 0;
}

.ui-tag-values {
  display: flex;
  align-items: center;
  flex: 0 1 auto;
  max-width: calc(100% - 76px);
  gap: var(--space-1);
  margin-left: 5px;
  padding: 3px 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.ui-tag-chip {
  flex: 0 0 auto;
  max-width: 180px;
  gap: 2px;
  height: 24px;
  padding: 0 4px 0 8px;
  border-radius: 6px;
  background: var(--surface-soft);
  color: var(--text);
  font-size: var(--text-sm);
}

.ui-tag-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ui-tag-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 18px;
  width: 18px;
  height: 20px;
  padding: 2px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
}

.ui-tag-remove:hover:not(:disabled) {
  background: var(--hover);
  color: var(--text);
}

.ui-tag-remove:focus-visible {
  outline-offset: -1px;
}

.ui-tag-input--filled :deep(.ui-combobox-input) {
  padding-inline: var(--space-2) 0;
}

.ui-tag-input :deep(.ui-combobox) {
  align-items: center;
}

.ui-tag-input :deep(.ui-combobox-input),
.ui-tag-input :deep(.ui-combobox-toggle) {
  height: 100%;
}

@media (pointer: coarse) {
  .ui-tag-chip {
    height: 32px;
  }

  .ui-tag-remove {
    flex-basis: 28px;
    width: 28px;
    height: 28px;
  }
}
</style>
