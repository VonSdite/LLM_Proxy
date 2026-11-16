<script setup lang="ts">
defineProps<{ disabled: boolean }>()
</script>
<template>
  <div class="ui-disabled-region" :inert="disabled" :aria-disabled="disabled || undefined">
    <slot />
  </div>
</template>
<style scoped>
.ui-disabled-region {
  display: grid;
  min-width: 0;
  gap: inherit;
}

.ui-disabled-region[inert] {
  opacity: 0.5;
}
</style>
