<script setup lang="ts">
import { computed } from 'vue'
import { UiIconButton, UiStatus } from '@/components/ui'
const props = defineProps<{ path: string; exists: boolean; disabled?: boolean }>()
const emit = defineEmits<{ open: []; settings: [] }>()
const filename = computed(() => props.path.split(/[\\/]/).at(-1) || props.path)
</script>
<template>
  <div class="config-path-row">
    <UiStatus :value="exists" on="存在" off="未创建" />
    <span class="config-path-name mono">{{ filename }}</span>
    <button
      v-if="exists"
      class="config-path-link mono truncate"
      type="button"
      :title="path"
      :disabled="disabled"
      @click="emit('open')"
    >
      {{ path }}
    </button>
    <span v-else class="config-path-missing mono truncate" :title="path">{{ path }}</span>
    <UiIconButton
      icon="settings"
      :label="`${filename} 路径设置`"
      :disabled="disabled"
      @click="emit('settings')"
    />
  </div>
</template>
<style scoped>
.config-path-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  padding: var(--space-2) var(--space-3);
}

.config-path-name {
  flex: none;
}

.config-path-link {
  max-width: 100%;
  min-width: 0;
  padding: 0;
  background: transparent;
  border: 0;
  color: var(--link);
  text-align: left;
}

.config-path-link:hover {
  text-decoration: underline;
}

.config-path-missing {
  color: var(--muted);
  min-width: 0;
}

.config-path-row > :last-child {
  margin-left: auto;
}

@media (width <= 600px) {
  .config-path-row {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    gap: var(--space-2);
  }

  .config-path-name {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .config-path-link,
  .config-path-missing {
    grid-column: 1 / -1;
    grid-row: 2;
    justify-self: start;
    max-width: 100%;
  }
}
</style>
