<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ value: number | null; label: string }>()
const percent = computed(() =>
  props.value == null ? null : Math.max(0, Math.min(100, props.value)),
)
</script>
<template>
  <div class="ui-meter">
    <div class="ui-meter-label">
      <span>{{ label }}</span>
      <span class="number">{{ percent === null ? '—' : `${Number(percent.toFixed(1))}%` }}</span>
    </div>
    <div
      class="ui-meter-track"
      role="progressbar"
      :aria-label="`${label} 剩余百分比`"
      :aria-valuenow="percent ?? undefined"
      :aria-valuemin="0"
      :aria-valuemax="100"
    >
      <span
        :style="{ width: `${percent ?? 0}%` }"
        :class="{ 'ui-meter-low': percent !== null && percent <= 15 }"
      />
    </div>
    <small v-if="$slots.default" class="muted"><slot /></small>
  </div>
</template>
<style scoped>
.ui-meter {
  display: grid;
  gap: var(--space-1);
}

.ui-meter-label {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--text-xs);
}

.ui-meter-track {
  height: 5px;
  border-radius: var(--radius-pill);
  background: var(--border);
  overflow: hidden;
}

.ui-meter-track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--primary);
}

.ui-meter-track .ui-meter-low {
  background: var(--danger);
}
</style>
