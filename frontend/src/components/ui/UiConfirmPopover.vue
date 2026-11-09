<script setup lang="ts">
import { ref } from 'vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import UiButton from './UiButton.vue'
import UiPopoverArrow from './UiPopoverArrow.vue'
defineProps<{
  message: string
  description?: string | readonly string[]
  confirmLabel?: string
  busy?: boolean
}>()
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
      <PopoverContent
        class="ui-confirm"
        :class="{ 'ui-confirm--described': description }"
        :side-offset="6"
        :arrow-padding="12"
        :collision-padding="12"
      >
        <UiPopoverArrow />
        <div class="ui-confirm-body">
          <p class="ui-confirm-message">{{ message }}</p>
          <ul v-if="Array.isArray(description)" class="ui-confirm-description ui-confirm-list">
            <li v-for="line in description" :key="line">{{ line }}</li>
          </ul>
          <p v-else-if="description" class="ui-confirm-description">{{ description }}</p>
        </div>
        <div class="ui-confirm-actions">
          <UiButton size="sm" @click="open = false">取消</UiButton>
          <UiButton size="sm" variant="danger-solid" :loading="busy" @click="confirm">
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
  color: var(--text);
  font-size: var(--text-sm);
  font-weight: 400;
}

:global(.ui-confirm--described) {
  width: min(288px, calc(100vw - 24px));
}

:global(.ui-confirm-body) {
  display: grid;
  gap: var(--space-2);
}

:global(.ui-confirm-message) {
  margin: 0;
  font-size: var(--text-base);
  font-weight: var(--weight-label);
  line-height: 1.5;
  overflow-wrap: anywhere;
}

:global(.ui-confirm-description) {
  margin: 0;
  color: var(--muted);
  line-height: 1.65;
  overflow-wrap: anywhere;
}

:global(.ui-confirm-list) {
  padding-left: 1.2em;
}

:global(.ui-confirm-actions) {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
</style>
