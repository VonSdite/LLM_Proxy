<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import UiTooltip from './UiTooltip.vue'
const props = defineProps<{
  label: string
  points: { label: string; value: number | null }[]
  format: (value: number) => string
}>()
const id = useId()
const container = ref<HTMLElement>()
const width = ref(760)
let observer: ResizeObserver | undefined
onMounted(() => {
  observer = new ResizeObserver(([entry]) => {
    if (entry) width.value = Math.max(240, entry.contentRect.width)
  })
  if (container.value) observer.observe(container.value)
})
onBeforeUnmount(() => observer?.disconnect())
const active = ref<number | null>(null)
const top = computed(() => Math.max(1, ...props.points.map((point) => point.value || 0)))
const coordinates = computed(() =>
  props.points.map((point, i) => ({
    ...point,
    x:
      props.points.length === 1
        ? width.value / 2
        : 48 + (i * (width.value - 96)) / Math.max(1, props.points.length - 1),
    left: i === 0 ? 48 : 48 + ((i - 0.5) * (width.value - 96)) / (props.points.length - 1),
    right:
      i === props.points.length - 1
        ? width.value - 48
        : 48 + ((i + 0.5) * (width.value - 96)) / (props.points.length - 1),
    y: point.value == null ? null : 192 - (point.value / top.value) * 160,
  })),
)
const segments = computed(() => {
  const groups: { x: number; y: number }[][] = []
  let current: { x: number; y: number }[] = []
  for (const point of coordinates.value) {
    if (point.y == null) {
      current = []
      continue
    }
    if (!current.length) groups.push(current)
    current.push({ x: point.x, y: point.y })
  }
  return groups.map((group) => ({
    path: group.map((point, i) => `${i ? 'L' : 'M'}${point.x},${point.y}`).join(' '),
    first: group[0]!,
    last: group.at(-1)!,
  }))
})
const focusPoint = computed(() =>
  active.value == null ? undefined : coordinates.value[active.value],
)
</script>
<template>
  <div ref="container" class="ui-trend">
    <svg :viewBox="`0 0 ${width} 230`" role="group" :aria-label="label">
      <defs>
        <linearGradient :id="id" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(--link)" stop-opacity="0.2" />
          <stop offset="100%" stop-color="var(--link)" stop-opacity="0.01" />
        </linearGradient>
      </defs>
      <g v-for="step in [0, 0.5, 1]" :key="step" class="ui-trend-axis">
        <line x1="48" :x2="width - 48" :y1="192 - step * 160" :y2="192 - step * 160" />
        <text x="38" :y="196 - step * 160" text-anchor="end">{{ format(top * step) }}</text>
      </g>
      <g v-for="segment in segments" :key="segment.first.x">
        <path
          :d="`${segment.path} L${segment.last.x},192 L${segment.first.x},192 Z`"
          :fill="`url(#${id})`"
        />
        <path :d="segment.path" class="ui-trend-line" />
        <circle
          v-if="segment.first.x === segment.last.x"
          :cx="segment.first.x"
          :cy="segment.first.y"
          r="4"
          class="ui-trend-dot"
        />
      </g>
      <text
        v-if="!segments.length"
        :x="width / 2"
        y="110"
        text-anchor="middle"
        class="ui-trend-date"
      >
        暂无可计算数据
      </text>
      <g v-if="focusPoint && focusPoint.y != null" class="ui-trend-focus">
        <line :x1="focusPoint.x" :x2="focusPoint.x" y1="24" y2="192" />
        <circle :cx="focusPoint.x" :cy="focusPoint.y" r="4" />
      </g>
      <foreignObject
        v-for="(point, index) in coordinates"
        :key="point.label"
        :x="point.left"
        y="16"
        :width="point.right - point.left"
        height="182"
      >
        <UiTooltip
          :text="`${point.label} · ${label} ${point.value == null ? '未知' : format(point.value)}`"
        >
          <button
            class="ui-trend-target"
            type="button"
            :aria-label="`${point.label} ${label} ${point.value == null ? '未知' : format(point.value)}`"
            @pointerenter="active = index"
            @pointerleave="active = null"
            @focus="active = index"
            @blur="active = null"
          />
        </UiTooltip>
      </foreignObject>
      <text x="48" y="222" class="ui-trend-date">{{ points[0]?.label }}</text>
      <text
        v-if="points.length > 1"
        :x="width - 48"
        y="222"
        text-anchor="end"
        class="ui-trend-date"
      >
        {{ points.at(-1)?.label }}
      </text>
    </svg>
  </div>
</template>
<style scoped>
.ui-trend {
  min-width: 0;
  width: 100%;
}

.ui-trend svg {
  display: block;
  width: 100%;
  height: 230px;
  overflow: visible;
}

.ui-trend-axis line {
  stroke: var(--border);
  stroke-dasharray: 3 5;
}

.ui-trend-axis text,
.ui-trend-date {
  fill: var(--muted);
  font-size: 12px;
  font-family: inherit;
}

.ui-trend-line {
  fill: none;
  stroke: var(--link);
  stroke-width: 2.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.ui-trend-dot,
.ui-trend-focus circle {
  fill: var(--link);
}

.ui-trend-focus line {
  stroke: var(--muted);
  stroke-dasharray: 3 3;
}

.ui-trend-target {
  display: block;
  width: 100%;
  height: 100%;
  padding: 0;
  background: transparent;
  border: 0;
}

.ui-trend-target:focus-visible {
  outline: 1px solid var(--link);
  outline-offset: -2px;
}
</style>
