<script setup lang="ts">
import { ref } from 'vue'
import UiInput from './UiInput.vue'
import UiIconButton from './UiIconButton.vue'
defineOptions({ inheritAttrs: false })
defineProps<{ modelValue?: string; disabled?: boolean; passwordAutofill?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const visible = ref(false)
</script>
<template>
  <div class="ui-secret">
    <UiInput
      :model-value="modelValue"
      :type="!visible && passwordAutofill ? 'password' : 'text'"
      :class="{ 'ui-secret-mask': !visible && !passwordAutofill }"
      :disabled="disabled"
      :autocomplete="passwordAutofill ? 'current-password' : 'new-password'"
      :data-lpignore="passwordAutofill ? undefined : 'true'"
      :data-1p-ignore="passwordAutofill ? undefined : 'true'"
      :data-bwignore="passwordAutofill ? undefined : 'true'"
      autocapitalize="off"
      :spellcheck="false"
      v-bind="$attrs"
      @update:model-value="emit('update:modelValue', $event)"
    />
    <UiIconButton
      :icon="visible ? 'eyeOff' : 'eye'"
      :label="visible ? '隐藏内容' : '显示内容'"
      :disabled="disabled"
      @click="visible = !visible"
    />
  </div>
</template>
<style scoped>
.ui-secret-mask {
  -webkit-text-security: disc;
}

.ui-secret {
  display: flex;
  min-width: 0;
  position: relative;
}

.ui-secret :deep(.ui-input) {
  padding-right: calc(var(--control) + var(--space-1));
}

.ui-secret :deep(.ui-icon-button) {
  position: absolute;
  right: 0;
  top: 0;
}
</style>
