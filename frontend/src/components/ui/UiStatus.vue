<script setup lang="ts">
import UiIcon from './UiIcon.vue'
import UiBadge from './UiBadge.vue'
defineProps<{ value: boolean; on?: string; off?: string }>()
</script>
<template>
  <UiBadge :variant="value ? 'success' : 'neutral'">
    <UiIcon :name="value ? 'check' : 'ban'" :size="12" />
    {{ value ? on || '启用' : off || '禁用' }}
  </UiBadge>
</template>
