<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  ComboboxAnchor,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxItemIndicator,
  ComboboxPortal,
  ComboboxRoot,
  ComboboxTrigger,
  ComboboxViewport,
} from 'reka-ui'
import UiIcon from './UiIcon.vue'
import { useField } from './field'
import type { SelectOption } from './types'
const props = withDefaults(
  defineProps<{
    modelValue?: string
    options: SelectOption[]
    placeholder?: string
    disabled?: boolean
    allowCustom?: boolean
    secret?: boolean
  }>(),
  { modelValue: '', placeholder: '搜索或选择' },
)
const emit = defineEmits<{ 'update:modelValue': [value: string]; select: [value: string] }>()
const field = useField()
const open = ref(false)
const search = ref('')
const visible = ref(false)
const filtered = computed(() => {
  const query = search.value.toLowerCase()
  return props.options.filter((option) =>
    `${option.label} ${option.value} ${option.description || ''}`.toLowerCase().includes(query),
  )
})
watch(open, (value) => {
  if (!value) search.value = ''
})
function select(value: unknown) {
  emit('update:modelValue', String(value ?? ''))
  emit('select', String(value ?? ''))
  search.value = ''
}
function input(value: string | number) {
  search.value = String(value)
  if (props.allowCustom) emit('update:modelValue', String(value))
  open.value = true
}
function display(value: unknown) {
  if (props.allowCustom || props.secret) return String(value ?? '')
  return props.options.find((o) => o.value === value)?.label || String(value ?? '')
}
</script>
<template>
  <ComboboxRoot
    v-model:open="open"
    :model-value="modelValue"
    :disabled="disabled"
    :ignore-filter="true"
    :reset-search-term-on-blur="!allowCustom"
    @update:model-value="select"
  >
    <ComboboxAnchor class="ui-combobox" :class="{ 'ui-combobox--error': field?.error.value }">
      <ComboboxInput
        :id="field?.id"
        class="ui-combobox-input"
        :display-value="display"
        :placeholder="placeholder"
        type="text"
        :class="{ 'ui-combobox-mask': secret && !visible }"
        :aria-invalid="!!field?.error.value || undefined"
        :aria-required="field?.required?.value || undefined"
        :aria-describedby="field?.describedBy.value"
        autocomplete="new-password"
        data-lpignore="true"
        data-1p-ignore="true"
        data-bwignore="true"
        autocapitalize="off"
        :spellcheck="false"
        @input="input(($event.target as HTMLInputElement).value)"
        @focus="open = true"
        @click="open = true"
      />
      <button
        v-if="secret"
        class="ui-combobox-toggle"
        type="button"
        :aria-label="visible ? '隐藏内容' : '显示内容'"
        :disabled="disabled"
        @click="visible = !visible"
      >
        <UiIcon :name="visible ? 'eyeOff' : 'eye'" />
      </button>
      <ComboboxTrigger class="ui-combobox-toggle" aria-label="显示候选">
        <UiIcon name="down" :size="14" />
      </ComboboxTrigger>
    </ComboboxAnchor>
    <ComboboxPortal>
      <ComboboxContent
        class="ui-combobox-content"
        position="popper"
        :side-offset="6"
        :collision-padding="12"
      >
        <ComboboxViewport>
          <ComboboxEmpty class="ui-combobox-empty">
            {{ allowCustom ? '使用输入的值' : '没有匹配项' }}
          </ComboboxEmpty>
          <ComboboxItem
            v-for="option in filtered"
            :key="option.value"
            :value="option.value"
            :disabled="option.disabled"
            class="ui-combobox-item"
          >
            <div class="ui-combobox-text">
              <slot name="option" :option="option">
                <span>{{ option.label }}</span>
                <small v-if="option.description" class="muted">{{ option.description }}</small>
              </slot>
            </div>
            <ComboboxItemIndicator>
              <UiIcon name="check" :size="14" />
            </ComboboxItemIndicator>
          </ComboboxItem>
        </ComboboxViewport>
      </ComboboxContent>
    </ComboboxPortal>
  </ComboboxRoot>
</template>
<style scoped>
.ui-combobox-mask {
  -webkit-text-security: disc;
}

.ui-combobox {
  display: flex;
  min-width: 0;
  height: var(--control);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--input);
}

.ui-combobox:focus-within {
  border-color: var(--link);
  box-shadow: var(--focus);
}

.ui-combobox--error {
  border-color: var(--danger);
}

.ui-combobox-input {
  width: 0;
  min-width: 0;
  flex: 1;
  padding: 0 var(--space-3);
  border: 0;
  border-radius: inherit;
  outline: none;
  background: transparent;
  font-size: var(--text-base);
  text-overflow: ellipsis;
}

.ui-combobox-input::placeholder {
  color: var(--muted);
}

.ui-combobox-toggle {
  display: flex;
  align-items: center;
  padding: 0 var(--space-2);
  border: 0;
  border-radius: inherit;
  background: transparent;
  color: var(--muted);
}

@media (pointer: coarse) {
  .ui-combobox-toggle {
    min-width: var(--control);
    justify-content: center;
  }
}

:global(.ui-combobox-content) {
  z-index: var(--z-popup);
  width: var(--reka-combobox-trigger-width);
  min-width: 180px;
  max-width: calc(100vw - 24px);
  max-height: min(320px, var(--reka-combobox-content-available-height));
  overflow: auto;
  padding: var(--space-1);
  border-radius: var(--radius-control);
  background: var(--overlay);
  color: var(--text);
  box-shadow: var(--shadow-overlay);
}

.ui-combobox-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2);
  min-height: var(--control-sm);
  border-radius: var(--space-1);
  font-size: var(--text-base);
  cursor: pointer;
  outline: none;
}

.ui-combobox-item[data-highlighted] {
  background: var(--hover);
}

.ui-combobox-item[data-state='checked'] {
  color: var(--link);
}

.ui-combobox-item[data-disabled] {
  opacity: 0.5;
  cursor: not-allowed;
}

.ui-combobox-text {
  display: grid;
  min-width: 0;
  overflow-wrap: anywhere;
  gap: 2px;
}

.ui-combobox-empty {
  padding: var(--space-3);
  color: var(--muted);
  font-size: var(--text-xs);
}
</style>
