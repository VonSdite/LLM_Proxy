<script setup lang="ts">
import { computed, provide, useId } from 'vue'
import { fieldKey } from './field'
import UiHelpPopover from './UiHelpPopover.vue'
import UiIcon from './UiIcon.vue'
const props = defineProps<{
  label: string
  help?: string
  error?: string
  hint?: string
  required?: boolean
  unit?: string
  group?: boolean
}>()
const id = useId()
provide(fieldKey, {
  id: props.group ? undefined : id,
  required: computed(() => props.required),
  error: computed(() => props.error),
  describedBy: computed(() =>
    props.error ? `${id}-error` : props.hint ? `${id}-hint` : undefined,
  ),
})
</script>
<template>
  <div
    class="ui-field"
    :class="{ 'ui-field--error': error }"
    :role="group ? 'group' : undefined"
    :aria-labelledby="group ? `${id}-label` : undefined"
  >
    <div class="ui-field-label">
      <component :is="group ? 'span' : 'label'" :id="`${id}-label`" :for="group ? undefined : id">
        {{ label }}
        <span v-if="unit" class="muted">（{{ unit }}）</span>
        <span v-if="required" class="danger" aria-hidden="true">*</span>
      </component>
      <slot name="label" />
      <UiHelpPopover v-if="help || $slots.help" :label="`${label}说明`">
        <slot name="help">{{ help }}</slot>
      </UiHelpPopover>
    </div>
    <slot :id="id" />
    <p v-if="error" :id="`${id}-error`" class="ui-field-error" role="alert">
      <UiIcon name="alert" :size="14" />
      {{ error }}
    </p>
    <p v-else-if="hint" :id="`${id}-hint`" class="ui-field-hint">{{ hint }}</p>
  </div>
</template>
<style scoped>
.ui-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.ui-field-label {
  overflow-wrap: anywhere;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
  min-height: 20px;
  font-size: var(--text-sm);
  font-weight: 500;
}

.ui-field-error,
.ui-field-hint {
  font-size: var(--text-xs);
}

.ui-field-error {
  display: flex;
  align-items: flex-start;
  gap: var(--space-1);
  color: var(--danger);
}

.ui-field-error svg {
  margin-top: 2px;
}

.ui-field-hint {
  color: var(--muted);
}
</style>
