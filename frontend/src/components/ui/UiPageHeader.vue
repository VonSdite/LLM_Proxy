<script setup lang="ts">
defineProps<{ title: string; count?: number }>()
</script>
<template>
  <header class="ui-page-header">
    <div class="ui-page-header-title">
      <h1>{{ title }}</h1>
      <span v-if="count !== undefined" class="ui-page-header-count">{{ count }}</span>
      <slot name="help" />
    </div>
    <div v-if="$slots.navigation" class="ui-page-header-navigation">
      <slot name="navigation" />
    </div>
    <div v-if="$slots.default" class="ui-page-header-actions"><slot /></div>
  </header>
</template>
<style scoped>
.ui-page-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3) var(--space-6);
  min-width: 0;
  min-height: 40px;
}

.ui-page-header-title,
.ui-page-header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.ui-page-header-title {
  min-width: 0;
}

.ui-page-header-count {
  color: var(--muted);
  font-size: var(--text-section);
  font-variant-numeric: tabular-nums;
}

.ui-page-header-actions {
  margin-left: auto;
  flex-shrink: 0;
}

.ui-page-header-navigation {
  min-width: 0;
}

@media (width <= 700px) {
  .ui-page-header {
    gap: var(--space-3);
  }

  .ui-page-header-navigation {
    order: 1;
    flex-basis: 100%;
  }
}
</style>
