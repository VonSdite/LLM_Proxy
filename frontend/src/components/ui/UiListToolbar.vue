<script setup lang="ts">
defineProps<{ wideFilters?: boolean }>()
</script>
<template>
  <div class="ui-list-toolbar" :class="{ 'ui-list-toolbar--wide-filters': wideFilters }">
    <div class="ui-list-toolbar-row">
      <div v-if="$slots.actions" class="ui-list-toolbar-actions"><slot name="actions" /></div>
      <div class="ui-list-toolbar-search"><slot name="search" /></div>
      <div v-if="$slots.filters" class="ui-list-toolbar-filters"><slot name="filters" /></div>
      <div class="ui-list-toolbar-tools"><slot /></div>
    </div>
  </div>
</template>
<style scoped>
.ui-list-toolbar {
  min-width: 0;
  container-type: inline-size;
}

.ui-list-toolbar-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  min-height: var(--control);
}

.ui-list-toolbar-search {
  width: clamp(200px, 18cqw, 280px);
  min-width: 160px;
  flex-shrink: 0;
  margin-left: auto;
}

.ui-list-toolbar-filters {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.ui-list-toolbar-actions,
.ui-list-toolbar-tools {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.ui-list-toolbar-actions {
  flex-wrap: wrap;
  max-width: 100%;
}

@container (width <= 1360px) {
  .ui-list-toolbar--wide-filters .ui-list-toolbar-actions {
    flex-basis: 100%;
  }
}

@container (width <= 1120px) {
  .ui-list-toolbar-filters {
    order: 1;
    flex-basis: 100%;
  }
}

@container (width <= 700px) {
  .ui-list-toolbar-actions {
    flex-basis: 100%;
  }

  .ui-list-toolbar-search {
    width: auto;
    min-width: 0;
    flex: 1;
  }
}

@container (width <= 520px) {
  .ui-list-toolbar-actions :deep(.ui-button) {
    padding-inline: var(--space-2);
  }
}
</style>
