<script setup lang="ts">
import UiIcon, { type IconName } from './UiIcon.vue'
withDefaults(
  defineProps<{ title?: string; description?: string; loading?: boolean; icon?: IconName }>(),
  { title: '暂无数据', icon: 'folder' },
)
</script>
<template>
  <div class="ui-empty" :role="loading ? 'status' : undefined">
    <UiIcon :name="loading ? 'loading' : icon" :size="24" />
    <p>{{ loading ? '正在加载…' : title }}</p>
    <small v-if="description" class="muted">{{ description }}</small>
    <slot />
  </div>
</template>
<style scoped>
.ui-empty {
  display: grid;
  justify-items: center;
  align-content: center;
  gap: var(--space-3);
  padding: var(--space-8);
  min-height: 180px;
  color: var(--muted);
  font-size: var(--text-sm);
  text-align: center;
}
</style>
