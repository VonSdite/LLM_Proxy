<script setup lang="ts">
import { ref } from 'vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import UiButton from './UiButton.vue'
defineProps<{ message: string; confirmLabel?: string; busy?: boolean }>()
const emit = defineEmits<{ confirm: [] }>()
const open = ref(false)
function confirm() {
  emit('confirm')
  open.value = false
}
</script>
<template>
  <PopoverRoot v-model:open="open">
    <PopoverTrigger as-child><slot /></PopoverTrigger>
    <PopoverPortal>
      <PopoverContent class="ui-confirm" :side-offset="8" :collision-padding="12">
        <p>{{ message }}</p>
        <div class="actions">
          <UiButton size="sm" @click="open = false">取消</UiButton>
          <UiButton size="sm" variant="danger" :loading="busy" @click="confirm">
            {{ confirmLabel || '确认删除' }}
          </UiButton>
        </div>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
<style scoped>
:global(.ui-confirm) {
  z-index: var(--z-popup);
  display: grid;
  gap: var(--space-3);
  max-width: min(320px, calc(100vw - 24px));
  padding: var(--space-4);
  border-radius: var(--radius-group);
  background: var(--overlay);
  box-shadow: var(--shadow-overlay);
  font-size: var(--text-sm);
}

:global(.ui-confirm) .actions {
  justify-content: flex-end;
}
</style>
