<script setup lang="ts">
import { ref } from 'vue'
import UiIcon from './UiIcon.vue'
import UiIconButton from './UiIconButton.vue'
import UiInput from './UiInput.vue'
defineOptions({ inheritAttrs: false })
defineProps<{ modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const input = ref<InstanceType<typeof UiInput>>()
function clear() {
  emit('update:modelValue', '')
  input.value?.focus()
}
</script>
<template>
  <div class="ui-search-input">
    <UiIcon name="search" class="ui-search-input-icon" />
    <UiInput
      ref="input"
      :model-value="modelValue"
      v-bind="$attrs"
      @update:model-value="emit('update:modelValue', $event)"
    />
    <UiIconButton
      v-if="modelValue"
      class="ui-search-input-clear"
      icon="close"
      label="清空搜索"
      @click="clear"
    />
  </div>
</template>
<style scoped>
.ui-search-input {
  position: relative;
  min-width: 0;
  width: 100%;
}

.ui-search-input :deep(.ui-input) {
  padding-left: var(--space-8);
  padding-right: var(--control);
}

.ui-search-input-icon {
  position: absolute;
  left: var(--space-3);
  top: 50%;
  transform: translateY(-50%);
  color: var(--muted);
  pointer-events: none;
}

.ui-search-input-clear {
  position: absolute;
  right: 0;
  top: 0;
}
</style>
